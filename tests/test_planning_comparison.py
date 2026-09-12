import json
import tempfile
import time
import unittest
from concurrent.futures import Future, TimeoutError
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from evals.planning_comparison import (
    BASE_CODE,
    CASES,
    ContextReview,
    PreparedReview,
    content_check,
    run_arm,
    seed,
)
from otsc.context_builder import ContextUpdate
from otsc.models import Assistance
from otsc.planning import PlanDecision
from otsc.scheduler import Cancellation
from otsc.settings import ModelChoice, Settings
from otsc.telemetry import digest


def review(needed=False):
    return ContextReview(
        context_update=ContextUpdate(upsert=[], remove=[], observed_files=[], retire_files=[]),
        decision=PlanDecision(answer_needed=needed, answer_reason="Review the new evidence.", reuse_previous=True,
                              reason="The existing output contract fits.", plan=None),
    )


class PlanningComparisonTests(unittest.TestCase):
    def test_pair_inputs_are_identical_and_first_answer_has_no_seeded_answer(self):
        for case in CASES:
            a, _ = seed(case)
            b, _ = seed(case)
            self.assertEqual(digest(a.snapshot().prompt_context()), digest(b.snapshot().prompt_context()))
        context, prior = seed("first_answer")
        self.assertIsNone(prior)
        self.assertEqual(json.loads(context.snapshot().previous_answer), {})
        self.assertEqual(json.loads(context.snapshot().task_plan), {})

    def test_warm_memory_is_source_backed_and_stops_before_new_evidence(self):
        for case in ("small_constraint", "new_question", "repeat_after_answer"):
            context, _ = seed(case, warm_memory=True)
            snapshot = context.snapshot()
            self.assertEqual(len(json.loads(snapshot.context_items)), 3)
            self.assertEqual(snapshot.context_updated_through, snapshot.evidence_revision - 1)
            self.assertTrue(all("new-evidence" not in item.source_ids for item in context.context_items.values()))
            other, _ = seed(case, warm_memory=True)
            self.assertEqual(digest(snapshot.prompt_context()), digest(other.snapshot().prompt_context()))

    def test_approval_cannot_be_used_twice(self):
        context, _ = seed("small_constraint")
        snapshot = replace(context.snapshot(), automatic_refresh=True)
        future = Future()
        future.set_result(review(True))
        prepared = PreparedReview(snapshot, future)
        self.assertTrue(prepared.generate_json(snapshot, "planner", Cancellation(), None)["answer_needed"])
        with self.assertRaisesRegex(ValueError, "consumed or stale"):
            prepared.generate_json(snapshot, "planner", Cancellation(), None)

    def test_new_evidence_task_or_answer_invalidates_cached_review(self):
        context, _ = seed("small_constraint")
        snapshot = replace(context.snapshot(), automatic_refresh=True)
        future = Future()
        future.set_result(review(True))
        for change in [dict(evidence_revision=snapshot.evidence_revision + 1), dict(task_revision=snapshot.task_revision + 1),
                       dict(session_id="new-session"), dict(previous_answer='{"summary":"Newly delivered answer"}'),
                       dict(task_plan='{"task":"Changed plan"}'), dict(automatic_refresh=False)]:
            with self.assertRaisesRegex(ValueError, "consumed or stale"):
                PreparedReview(snapshot, future).generate_json(replace(snapshot, **change), "planner", Cancellation(), None)

    def test_memory_rewording_does_not_make_a_review_stale(self):
        context, _ = seed("small_constraint")
        snapshot = context.snapshot()
        future = Future()
        future.set_result(review())
        result = PreparedReview(snapshot, future).generate_json(replace(snapshot, memory_revision=99, context_items="[]"),
                                                               "planner", Cancellation(), None)
        self.assertFalse(result["answer_needed"])

    def test_completed_provider_timeout_is_not_an_infinite_wait(self):
        context, _ = seed("same_screen")
        future = Future()
        future.set_exception(TimeoutError("provider failed"))
        with self.assertRaisesRegex(TimeoutError, "provider failed"):
            PreparedReview(context.snapshot(), future).generate_json(context.snapshot(), "planner", Cancellation(), None)

    def test_old_code_fails_zero_weight_change_without_executing_generated_code(self):
        _, answer = seed("same_screen")
        self.assertEqual(answer.artifacts[0].content, BASE_CODE)
        result = content_check("small_constraint", answer)
        self.assertEqual(result["status"], "fail")
        self.assertIn("weight 0", result["reason"])
        self.assertEqual(content_check("first_answer", answer)["status"], "pass")

    def test_unnecessary_answer_is_not_a_successful_no_change(self):
        _, answer = seed("same_screen")
        self.assertEqual(content_check("same_screen", answer)["status"], "fail")
        self.assertEqual(content_check("same_screen", None)["status"], "pass")

    def test_both_arms_run_real_delivery_and_publication_gate_without_extra_planner_call(self):
        class FakeProvider:
            def __init__(self, choice):
                self.choice = choice

            def generate_json(self, snapshot, lane, token, progress, **kwargs):
                if self.choice.model == "context_builder":
                    if "decision" in kwargs["schema"]["properties"]:
                        return review().model_dump()
                    return review().context_update.model_dump()
                if self.choice.model == "planner":
                    return review().decision.model_dump()
                raise AssertionError("No answer should be generated")

            def generate(self, *args):
                raise AssertionError("No quick answer should be generated")

        settings = Settings(**{role: ModelChoice(provider="codex", model=role)
                               for role in ("context_builder", "planner", "deep", "quick")})
        with tempfile.TemporaryDirectory() as temporary, patch("evals.planning_comparison.provider_for", side_effect=lambda choice, _: FakeProvider(choice)):
            results = {arm: run_arm("same_screen", arm, Path(temporary) / arm, settings) for arm in ("current", "combined")}
        for row in results.values():
            self.assertIsNone(row["error"])
            self.assertTrue(row["decision_correct"])
            self.assertEqual(row["unnecessary_generations"], 0)
            self.assertEqual(row["unnecessary_publications"], 0)
            self.assertIsNone(row["deep_seconds"])
        self.assertEqual([c["role"] for c in results["combined"]["calls"]], ["context_builder"])
        self.assertEqual({c["role"] for c in results["current"]["calls"]}, {"context_builder", "planner"})
        self.assertEqual(results["current"]["input_hash"], results["combined"]["input_hash"])

    def test_missing_required_answer_is_a_failure(self):
        self.assertEqual(content_check("small_constraint", None)["status"], "fail")
        empty = Assistance(task="Task", summary="Summary", artifacts=[], conversation=[], observed_files=[], open_questions=[])
        self.assertEqual(content_check("small_constraint", empty)["status"], "fail")

    def test_busy_context_worker_delays_combined_review_but_not_current_planning(self):
        class FakeProvider:
            def __init__(self, choice):
                self.choice = choice

            def generate_json(self, snapshot, lane, token, progress, **kwargs):
                if self.choice.model == "context_builder":
                    newer = any(o.id == "new-evidence" for o in snapshot.observations)
                    if not newer:
                        time.sleep(1.3)
                    if "decision" in kwargs["schema"]["properties"]:
                        return review(newer).model_dump()
                    return review().context_update.model_dump()
                if self.choice.model == "planner":
                    return review(True).decision.model_dump()
                _, answer = seed("same_screen")
                code = answer.artifacts[0].model_dump()
                return dict(task="Delivery fees", summary="The 3-kilogram parcel costs 10 dollars.", conversation=[],
                            observed_files=[], open_questions=[],
                            outputs={"implementation": {key: code[key] for key in ("content", "language", "path", "basis", "annotations")},
                                     "explanation": "4 + 2 * 3 = 10 dollars."},
                            output_sources={"implementation": ["seed-1"], "explanation": ["new-evidence"]})

            def generate(self, snapshot, lane, token, progress):
                return Assistance(task="Delivery fees", summary="3 kilograms costs 10 dollars.", artifacts=[], conversation=[],
                                  observed_files=[], open_questions=[])

        settings = Settings(**{role: ModelChoice(provider="codex", model=role)
                               for role in ("context_builder", "planner", "deep", "quick")})
        with tempfile.TemporaryDirectory() as temporary, patch("evals.planning_comparison.provider_for", side_effect=lambda choice, _: FakeProvider(choice)):
            results = {arm: run_arm("new_question", arm, Path(temporary) / arm, settings, background_busy=True)
                       for arm in ("current", "combined")}
        for row in results.values():
            self.assertTrue(row["background_busy_at_arrival"])
            self.assertIsNone(row["error"])
            self.assertTrue(row["decision_correct"])
            self.assertGreater(row["context_queue_wait_seconds"], 0)
        current, combined = results["current"], results["combined"]
        self.assertLess(current["deep_seconds"], current["context_queue_wait_seconds"])
        self.assertGreater(combined["deep_seconds"], combined["context_queue_wait_seconds"])


if __name__ == "__main__":
    unittest.main()
