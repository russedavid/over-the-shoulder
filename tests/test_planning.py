import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from otsc.context import ContextStore
from otsc.planning import TaskPlan, TaskPlanningProvider, checked_data_schema, contract_for, structured_text
from otsc.scheduler import Cancellation


def section(key="route_options", presentation="json", schema=None):
    return dict(key=key, label=key.replace("_", " "), purpose="Complete the requested deliverable",
                instructions="Keep the answer grounded in the stated constraints", presentation=presentation,
                data_schema=json.dumps(schema) if schema is not None else "{}")


def plan(source, outputs=None):
    return dict(task="Choose a route", approach="Compare routes against the stated limits",
                instructions=["Preserve unknowns"], outputs=outputs or [section()], assumptions=[],
                quality_checks=["Do not invent travel times"], source_ids=[source])


class FakeProvider:
    choice = SimpleNamespace(model="synthetic", provider="disabled")

    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate_json(self, *args, **kwargs):
        self.calls.append(kwargs)
        return self.response


class TaskPlanningTests(unittest.TestCase):
    def setUp(self):
        self.context = ContextStore()
        self.context.set_goal("Choose a safe cycling route; time estimates are unknown.")
        self.source = self.context.snapshot().observations[0].id

    def run_answer(self, specification, outputs, *, reuse=False, reason="The deliverable is unchanged."):
        decision = FakeProvider(dict(reuse_previous=reuse, reason=reason, plan=None if reuse else specification))
        answer = FakeProvider(dict(task="Help with this task", summary="A concrete proposal", conversation=[],
                                   outputs=outputs, output_sources={key: [self.source] for key in outputs},
                                   observed_files=[], open_questions=[]))
        provider = TaskPlanningProvider(answer, decision)
        result = provider.generate(self.context.snapshot(), "deep", Cancellation(), lambda text: None)
        return result, provider, answer

    def test_novel_nested_data_and_open_maps_survive_without_fixed_section_names(self):
        value = {"Canal path": {"grade": 2.5, "open": True, "duration": None}, "alternatives": []}
        specification = plan(self.source)
        result, provider, answer = self.run_answer(specification, {"route_options": json.dumps(value)})
        self.assertEqual(json.loads(result.artifacts[0].content), value)
        self.assertEqual(result.artifacts[0].kind, "structured")
        self.assertIn("Canal path", result.artifacts[0].annotated_text())
        schema = answer.calls[0]["schema"]["properties"]["outputs"]
        self.assertEqual(schema["required"], ["route_options"])
        self.assertEqual(provider.last_plan, specification)

    def test_closed_nested_schema_uses_native_values(self):
        schema = {"type": "object", "properties": {"passages": {"type": "array", "items": {"type": "string"}},
                                                   "available": {"type": "boolean"}},
                  "required": ["passages", "available"], "additionalProperties": False}
        value = {"passages": ["North"], "available": False}
        result, _, answer = self.run_answer(plan(self.source, [section(schema=schema)]), {"route_options": value})
        self.assertEqual(json.loads(result.artifacts[0].clean_text()), value)
        self.assertEqual(answer.calls[0]["schema"]["properties"]["outputs"]["properties"]["route_options"]["type"], "object")

    def test_optional_properties_keep_absence_semantics(self):
        schema = {"type": "object", "properties": {"later": {"type": "integer"}}, "additionalProperties": False}
        specification = TaskPlan.model_validate(plan(self.source, [section(schema=schema)]))
        _, encodings, _ = contract_for(specification)
        self.assertEqual(encodings["route_options"], "json_string")
        result, _, _ = self.run_answer(specification.model_dump(), {"route_options": "{}"})
        self.assertEqual(json.loads(result.artifacts[0].content), {})

    def test_plan_reuse_and_material_change_are_explicit_and_reviewable(self):
        specification = plan(self.source)
        initial, _, _ = self.run_answer(specification, {"route_options": "[]"})
        self.context.integrate(initial)
        self.context.add("note", "Name the route North Canal", "typed", "primary_user")
        reused, provider, _ = self.run_answer(None, {"route_options": '["North Canal"]'}, reuse=True,
                                             reason="A naming clarification does not change the route-comparison deliverable.")
        self.assertEqual(provider.last_plan, specification)
        review = json.loads(reused.artifacts[-1].content)["review"]
        self.assertEqual(review["decision"], "kept")
        self.assertIn("naming clarification", review["reason"])
        self.context.integrate(reused)
        replacement = plan(self.source, [section("packing_manifest", schema={"type": "array", "items": {"type": "string"}})])
        revised, _, _ = self.run_answer(replacement, {"packing_manifest": ["pump"]}, reason="The deliverable is now a packing manifest.")
        self.context.integrate(revised)
        self.assertEqual([x["key"] for x in json.loads(self.context.task_plan)["outputs"]], ["packing_manifest"])
        self.assertEqual(json.loads(revised.artifacts[-1].content)["review"]["decision"], "revised")

    def test_invalid_section_does_not_hide_valid_siblings(self):
        specification = plan(self.source, [section("distances", schema={"type": "array", "items": {"type": "number"}}),
                                           section("explanation", "text")])
        result, _, _ = self.run_answer(specification, {"distances": ["invented"], "explanation": "Check closure notices."})
        self.assertIn("could not be validated: distances", result.summary)
        self.assertEqual([a.id for a in result.artifacts], ["explanation", "_assistance_plan"])
        self.assertEqual(result._delivery_notes[-1]["kind"], "section_rejected")

    def test_code_remains_annotated_and_copyable(self):
        value = dict(content="answer = 42\n", language="python", path="", basis="example",
                     annotations=[dict(line=1, explanation="Assign the example answer.")])
        result, _, _ = self.run_answer(plan(self.source, [section("solution", "code")]), {"solution": value})
        self.assertEqual(result.artifacts[0].clean_text(), "answer = 42\n")
        self.assertIn("Assign", result.artifacts[0].annotated_text())

    def test_image_is_a_brief_and_keep_preserves_the_original_request(self):
        specification = plan(self.source, [section("map", "image")])
        result, _, _ = self.run_answer(specification, {"map": dict(action="generate", prompt="Draw two route alternatives.", caption="Route map")})
        self.assertEqual(result.artifacts[0].kind, "image")
        original = json.loads(result.artifacts[0].content)
        self.assertEqual(original["status"], "pending")
        self.context.integrate(result)
        result, _, _ = self.run_answer(None, {"map": dict(action="keep", prompt="", caption="")}, reuse=True)
        self.assertEqual(json.loads(result.artifacts[0].content), original)

    def test_schema_references_and_excessive_depth_are_rejected_without_network(self):
        for schema in ({"$ref": "https://example.com/schema"}, {"$dynamicRef": "#loop"}):
            with self.assertRaises(ValueError):
                checked_data_schema(json.dumps(schema))
        nested = {"type": "string"}
        for _ in range(20):
            nested = {"type": "array", "items": nested}
        with self.assertRaises(ValueError):
            checked_data_schema(json.dumps(nested))
        self.assertIn("null", structured_text([False, None, 0]))

    def test_reusing_without_an_existing_plan_fails_explicitly(self):
        with self.assertRaises(ValueError):
            self.run_answer(None, {}, reuse=True)

    def test_plan_survives_session_restore_and_new_task_clears_it(self):
        from otsc.sessions import checkpoint, load_session, restore_context, save_session

        result, _, _ = self.run_answer(plan(self.source), {"route_options": "[]"})
        self.context.integrate(result)
        coordinator = SimpleNamespace(current=result, history=[], pinned=False)
        document = checkpoint(self.context, coordinator, displayed_artifacts=result.artifacts)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            save_session(document, path)
            restored = restore_context(load_session(path))
        self.assertEqual(json.loads(restored.task_plan), result._task_plan)
        self.assertEqual(restored.snapshot().prompt_context()["task_plan"], result._task_plan)
        restored.clear()
        self.assertEqual(json.loads(restored.task_plan), {})
