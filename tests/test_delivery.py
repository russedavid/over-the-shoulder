import json
import time
import unittest
from copy import deepcopy
from itertools import product

from otsc.context import ContextStore
from otsc.delivery import prepare_response
from otsc.models import Artifact
from otsc.scheduler import Cancellation, Cancelled


def scene():
    context = ContextStore()
    seen = context.add("screen", "helper.py\ndef helper():\n    return 1", "screen")
    return context, seen.id


def answer(source_id, *, code="def helper():\n\n    return 2\n", annotations=None):
    return {
        "task": "Improve the helper",
        "summary": "A useful answer.",
        "conversation": [],
        "artifacts": [
            {
                "id": "code",
                "kind": "code",
                "title": "Helper",
                "content": code,
                "language": "python",
                "path": "helper.py",
                "basis": "observed_fragment",
                "source_ids": [source_id],
                "annotations": annotations
                if annotations is not None
                else [{"line": 1, "explanation": "Define the helper."}, {"line": 3, "explanation": "Return two."}],
                "nodes": [],
                "edges": [],
            }
        ],
        "observed_files": [],
        "open_questions": [],
    }


class RepairProvider:
    def __init__(self, mode="valid"):
        self.calls = 0
        self.mode = mode
        self.last_raw_text = "original model output"
        self.input = None

    def generate_json(self, snapshot, lane, token, progress, **kwargs):
        self.calls += 1
        self.input = json.loads(kwargs["prompt"])
        assert not snapshot.observations and snapshot.verified_files == "{}"
        if self.mode == "timeout":
            token.event.wait(1)
            token.check()
        self.last_raw_text = "repair response"
        return (
            {
                "artifacts": [
                    {
                        "artifact_id": "code",
                        "annotations": [
                            {"line": 1, "explanation": "Define the helper."},
                            {"line": 3, "explanation": "Return two."},
                        ],
                    }
                ]
            }
            if self.mode == "valid"
            else {"wrong": []}
        )


class DeliveryTests(unittest.TestCase):
    def test_malformed_optional_entries_cannot_discard_valid_siblings(self):
        context, source = scene()
        base = answer(source)
        base["artifacts"][0].update(basis="example", path="")
        base["conversation"] = [
            {"source_id": source, "action": "answer", "text": "A reply", "artifact_effect": "Keep the helper"}
        ]
        base["observed_files"] = [
            {
                "path": "helper.py",
                "content": "def helper():\n    return 1",
                "first_line": 1,
                "source_ids": [source],
                "confidence": "high",
            }
        ]
        malformed = [None, True, 0, "not an entry", [], ["entry"], {}]
        fields = ("conversation", "artifacts", "observed_files")
        snapshot = context.snapshot()
        for field, item, position in product(fields, malformed, (0, 1)):
            with self.subTest(field=field, item=item, position=position):
                raw = deepcopy(base)
                raw[field].insert(position, item)
                original = deepcopy(raw)
                result = prepare_response(raw, snapshot, "deep", Cancellation(), lambda text: None)
                self.assertTrue(result.summary.startswith(base["summary"]))
                for retained in fields:
                    self.assertEqual(result.model_dump()[retained], base[retained])
                self.assertEqual(len(result._delivery_notes), 1)
                self.assertEqual(raw, original)
                self.assertEqual(context.snapshot(), snapshot)

    def test_artifact_field_mutations_preserve_independent_valid_code(self):
        context, source = scene()
        base = answer(source)
        base["artifacts"][0].update(basis="example", path="")
        good = base["artifacts"][0]
        values = [None, False, 0, -1, "", [], [[]], {}, {"unexpected": True}]
        snapshot = context.snapshot()
        for field, value, position in product(good, values, (0, 1)):
            with self.subTest(field=field, value=value, position=position):
                raw = deepcopy(base)
                mutant = {**deepcopy(good), "id": "mutant", field: value}
                raw["artifacts"].insert(position, mutant)
                original = deepcopy(raw)
                result = prepare_response(raw, snapshot, "deep", Cancellation(), lambda text: None)
                retained = [a for a in result.artifacts if a.id == good["id"]]
                self.assertEqual([a.model_dump() for a in retained], [good])
                result.validate_sources(snapshot.observations, {})
                self.assertEqual(raw, original)
                self.assertEqual(context.snapshot(), snapshot)

    def test_rejected_sources_do_not_reserve_a_valid_artifacts_identity(self):
        context, source = scene()
        raw = answer(source)
        good = deepcopy(raw["artifacts"][0])
        raw["artifacts"] = [
            {**good, "source_ids": ["unknown"]},
            good,
            {**good, "id": "another-valid-artifact"},
        ]
        result = prepare_response(raw, context.snapshot(), "deep", Cancellation(), lambda text: None)
        self.assertEqual([a.id for a in result.artifacts], [good["id"], "another-valid-artifact"])
        self.assertEqual(result.artifacts[0].content, good["content"])
        self.assertEqual([n["reason"] for n in result._delivery_notes], ["artifact_rejected"])

    def test_quick_summary_survives_malformed_optional_replies(self):
        context, _ = scene()
        for item in [None, True, 0, "not a reply", [], {}, {"source_id": []}]:
            with self.subTest(item=item):
                raw = {"task": "Explain", "summary": "Useful help", "conversation": [item], "open_questions": []}
                result = prepare_response(raw, context.snapshot(), "quick", Cancellation(), lambda text: None)
                self.assertEqual(result.summary, raw["summary"])
                self.assertEqual(result.conversation, [])
                self.assertEqual(result.artifacts, [])
                self.assertEqual(result._delivery_notes[0]["reason"], "conversation_citation_rejected")

    def test_invalid_primary_envelopes_and_quick_limits_still_fail(self):
        context, source = scene()
        for lane in ("quick", "deep"):
            base = answer(source)
            if lane == "quick":
                del base["artifacts"], base["observed_files"]
            malformed = [
                None,
                [],
                {k: v for k, v in base.items() if k != "task"},
                {**base, "summary": None},
                {**base, "conversation": {}},
                {**base, "open_questions": [None]},
                {**base, "unexpected": True},
            ]
            if lane == "quick":
                malformed += [
                    {**base, "conversation": [None, None]},
                    {**base, "open_questions": ["First?", "Second?"]},
                    {**base, "artifacts": []},
                ]
            else:
                malformed += [{**base, field: None} for field in ("artifacts", "observed_files")]
            for raw in malformed:
                with self.subTest(lane=lane, raw=raw), self.assertRaises(ValueError):
                    prepare_response(raw, context.snapshot(), lane, Cancellation(), lambda text: None)

    def test_repair_budget_preserves_unrelated_code_without_extra_calls(self):
        context, source = scene()
        for count, content in ((4, "pass"), (1, "#" + "x" * 24000)):
            with self.subTest(count=count, characters=len(content)):
                raw = answer(source)
                good = deepcopy(raw["artifacts"][0])
                raw["artifacts"] += [
                    {**good, "id": f"missing-notes-{i}", "content": content, "annotations": []} for i in range(count)
                ]
                provider = RepairProvider()
                result = prepare_response(
                    raw, context.snapshot(), "deep", Cancellation(), lambda text: None, provider=provider
                )
                self.assertEqual(provider.calls, 0)
                self.assertEqual([a.model_dump() for a in result.artifacts], [good])
                self.assertIn("withheld", result.summary)
                self.assertEqual(len(result._delivery_notes), count)

    def test_quick_summary_survives_a_bad_optional_conversation_citation(self):
        context, source = scene()
        raw = {
            "task": "Locate the helper",
            "summary": "The helper is in the visible code.",
            "conversation": [
                {"source_id": "missing", "action": "answer", "text": "A reply", "artifact_effect": "No change"}
            ],
            "open_questions": [],
        }
        result = prepare_response(raw, context.snapshot(), "quick", Cancellation(), lambda text: None)
        self.assertEqual(result.summary, raw["summary"])
        self.assertEqual(result.conversation, [])
        self.assertEqual(result.artifacts, [])
        self.assertEqual(result._delivery_notes[0]["reason"], "conversation_citation_rejected")

    def test_bad_cache_updates_do_not_block_the_answer_or_become_observed_state(self):
        context, source = scene()
        raw = answer(source)
        raw["observed_files"] = [
            {
                "path": "helper.py",
                "content": "An invented description of unseen changes",
                "first_line": None,
                "source_ids": [source],
                "confidence": "high",
            }
        ]
        result = prepare_response(raw, context.snapshot(), "deep", Cancellation(), lambda text: None)
        self.assertEqual(result.summary, raw["summary"])
        self.assertEqual(result.artifacts[0].content, raw["artifacts"][0]["content"])
        self.assertEqual(result.observed_files, [])
        self.assertFalse(any(a.kind == "patch" for a in result.artifacts))
        context.integrate(result)
        self.assertEqual(context.files, {})
        self.assertIn("file_observation_quarantined", [n["reason"] for n in result._delivery_notes])

    def test_verified_file_authority_and_unknown_citations_still_fail_closed_per_artifact(self):
        context, source = scene()
        raw = answer(source)
        missing = {**raw["artifacts"][0], "id": "missing", "basis": "verified_file", "path": "unseen.py"}
        uncited = {**raw["artifacts"][0], "id": "uncited", "source_ids": ["not-in-the-snapshot"]}
        raw["artifacts"] += [missing, uncited]
        result = prepare_response(raw, context.snapshot(), "deep", Cancellation(), lambda text: None)
        self.assertEqual([a.id for a in result.artifacts], ["code"])
        self.assertIn("withheld", result.summary)
        result.validate_sources(context.snapshot().observations, {})

    def test_blank_lines_need_no_filler_but_structural_lines_still_need_explanations(self):
        _, source = scene()
        item = answer(source)["artifacts"][0]
        artifact = Artifact.model_validate(item)
        self.assertEqual(artifact.clean_text(), item["content"])
        self.assertIn("Return two.", artifact.annotated_text())
        with self.assertRaises(ValueError):
            Artifact.model_validate({**item, "content": "if (ready) {\n\n}\n", "annotations": [item["annotations"][0]]})

    def test_misaligned_annotations_are_repaired_without_rewriting_code_or_sources(self):
        context, source = scene()
        raw = answer(source, annotations=[{"line": 4, "explanation": "An extra, misplaced explanation."}])
        provider = RepairProvider()
        result = prepare_response(raw, context.snapshot(), "deep", Cancellation(), lambda text: None, provider=provider)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(provider.last_raw_text, "original model output")
        self.assertEqual(result.artifacts[0].content, raw["artifacts"][0]["content"])
        self.assertEqual(result.artifacts[0].source_ids, [source])
        self.assertEqual([a.line for a in result.artifacts[0].annotations], [1, 3])
        self.assertEqual(result.summary, raw["summary"])

    def test_failed_or_slow_repair_preserves_the_answer_without_publishing_unannotated_code(self):
        for mode in ("invalid", "timeout"):
            with self.subTest(mode=mode):
                context, source = scene()
                provider = RepairProvider(mode)
                start = time.monotonic()
                result = prepare_response(
                    answer(source, annotations=[]),
                    context.snapshot(),
                    "deep",
                    Cancellation(),
                    lambda text: None,
                    provider=provider,
                    repair_seconds=0.02,
                )
                self.assertLess(time.monotonic() - start, 0.5)
                self.assertEqual(provider.calls, 1)
                self.assertEqual(result.artifacts, [])
                self.assertTrue(result.summary.startswith("A useful answer."))
                self.assertIn("withheld", result.summary)

    def test_task_cancellation_does_not_become_a_partial_response(self):
        context, source = scene()
        token = Cancellation()
        token.cancel()
        with self.assertRaises(Cancelled):
            prepare_response(
                answer(source, annotations=[]),
                context.snapshot(),
                "deep",
                token,
                lambda text: None,
                provider=RepairProvider(),
            )

    def test_only_corroborated_fragments_get_diffs_and_blank_additions_do_not_break_them(self):
        context, source = scene()
        raw = answer(source)
        raw["observed_files"] = [
            {
                "path": "helper.py",
                "content": "def helper():\n    return 1",
                "first_line": 1,
                "source_ids": [source],
                "confidence": "high",
            }
        ]
        result = prepare_response(raw, context.snapshot(), "deep", Cancellation(), lambda text: None)
        self.assertEqual([a.kind for a in result.artifacts], ["code", "patch"])
        self.assertTrue(all(a.basis == "observed_fragment" for a in result.artifacts))
        self.assertEqual(result.observed_files[0].content, raw["observed_files"][0]["content"])
        result.artifacts[1].annotated_text()

    def test_unconfirmed_visual_paths_do_not_become_file_targets_or_fake_diffs(self):
        context, source = scene()
        raw = answer(source)
        raw["artifacts"][0]["path"] = "an/unseen/helper.py"
        result = prepare_response(raw, context.snapshot(), "deep", Cancellation(), lambda text: None)
        self.assertEqual(result.artifacts[0].path, "")
        self.assertEqual(result.artifacts[0].basis, "observed_fragment")
        self.assertEqual([a.kind for a in result.artifacts], ["code"])

    def test_a_duplicate_model_patch_does_not_erase_the_host_calculated_diff(self):
        context, source = scene()
        raw = answer(source)
        raw["observed_files"] = [
            {
                "path": "helper.py",
                "content": "def helper():\n    return 1",
                "first_line": 1,
                "source_ids": [source],
                "confidence": "high",
            }
        ]
        first = prepare_response(raw, context.snapshot(), "deep", Cancellation(), lambda text: None)
        patch = next(a for a in first.artifacts if a.kind == "patch")
        raw["artifacts"].append(patch.model_dump())
        result = prepare_response(raw, context.snapshot(), "deep", Cancellation(), lambda text: None)
        self.assertEqual(result.summary, raw["summary"])
        self.assertEqual(len(result.artifacts), 2)
        self.assertEqual([a.id for a in result.artifacts], ["code", "code-observed-diff"])


if __name__ == "__main__":
    unittest.main()
