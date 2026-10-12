import json
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from evals.planning_comparison import seed
from otsc.capture import ScreenCapture, UnchangedScreen
from otsc.context import ContextStore
from otsc.models import Assistance, NoAnswerUpdate
from otsc.planning import CombinedTaskProvider
from otsc.quick import QuickProgress, QuickProvider
from otsc.scheduler import Cancellation, Coordinator
from otsc.settings import Settings, load_settings, save_settings
from otsc.streaming import completed_field, string_preview


def answer(text):
    return Assistance(task="Task", summary=text, conversation=[], artifacts=[], observed_files=[], open_questions=[])


class ExactFrameTests(unittest.TestCase):
    def test_unchanged_frame_stays_recent_without_mutating_old_snapshots_or_starting_new_work(self):
        now = [0.0]
        context = ContextStore(clock=lambda: now[0])
        original = context.add("screen", "Still visible code", "screen", image_path="frame.png")
        old = context.snapshot()
        revision, evidence = context.revision, context.evidence_revision
        now[0] = 400
        self.assertTrue(context.refresh_screen("frame.png", now[0]))
        current = context.snapshot()
        self.assertEqual(current.observations[0].id, original.id)
        self.assertEqual(current.observations[0].at, 400)
        self.assertEqual(old.observations[0].at, 0)
        self.assertEqual((context.revision, context.evidence_revision), (revision, evidence))
        self.assertFalse(context.refresh_screen("unknown.png", now[0]))

    def test_only_an_accepted_exact_frame_is_skipped_and_one_pixel_or_region_change_is_read(self):
        screen = ScreenCapture()
        frame = Image.new("RGB", (20, 20), "white")
        shot = Mock(side_effect=lambda **_: frame.copy())
        with (
            patch.dict(sys.modules, {"pyautogui": SimpleNamespace(screenshot=shot)}),
            patch.object(screen, "interpret", return_value=("text", "frame.png")) as interpret,
        ):
            for _ in range(2):
                screen.capture((0, 0, 20, 20), skip_unchanged=True)
            self.assertEqual(interpret.call_count, 2)  # Failed OCR has never committed this frame.
            screen.accept_frame("frame.png")
            callback = Mock()
            with self.assertRaises(UnchangedScreen):
                screen.capture((0, 0, 20, 20), skip_unchanged=True, on_captured=callback)
            callback.assert_called_once_with()
            self.assertEqual(interpret.call_count, 2)
            frame.putpixel((19, 19), (255, 254, 255))
            screen.capture((0, 0, 20, 20), skip_unchanged=True)
            screen.accept_frame("frame.png")
            screen.capture((-20, 0, 20, 20), skip_unchanged=True)
            screen.accept_frame("frame.png")
            screen.capture((-20, 0, 20, 20), skip_unchanged=False)  # Help now forces a fresh read.
            screen.invalidate_frame()
            screen.capture((-20, 0, 20, 20), skip_unchanged=True)
            self.assertEqual(interpret.call_count, 6)

    def test_audio_endpointing_keeps_custom_chunk_ceiling_and_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = Settings().model_dump()
            old.pop("audio_endpointing")
            old["audio_chunk_seconds"] = 8
            (root / "settings.json").write_text(json.dumps(old))
            settings = load_settings(root)
            self.assertTrue(settings.audio_endpointing)
            self.assertEqual(settings.audio_chunk_seconds, 8)
            settings.audio_endpointing = False
            save_settings(settings, root)
            self.assertFalse(load_settings(root).audio_endpointing)


class StreamingTests(unittest.TestCase):
    def test_partial_strings_decode_escapes_and_ignore_deceptive_nested_text(self):
        text = '{"reason":"A fake \\"summary\\": \\"bad\\"", "response":{"summary":"Keep \\nzero and \\u006e'
        # Incomplete unicode is withheld instead of printing malformed characters.
        self.assertEqual(string_preview(text, ("response", "summary")), "Keep \nzero and n")
        self.assertEqual(string_preview('{"other":{"summary":"wrong"},"summary":"right'), "right")
        self.assertEqual(string_preview('{"summary":"before\\'), "before")
        self.assertIs(completed_field('{"answer_needed":false,"response":', "answer_needed"), False)

    def test_quick_preview_requires_positive_decision_and_known_sources(self):
        p = QuickProgress(lambda _: None, {"known"})
        self.assertEqual(
            p.preview_parser('{"answer_needed":true,"source_ids":["known"],"response":{"summary":"Keep zero'),
            "Keep zero",
        )
        for flag, source in ((False, "known"), (True, "invented")):
            raw = json.dumps({"answer_needed": flag, "source_ids": [source], "response": {"summary": "hidden"}})
            self.assertEqual(p.preview_parser(raw), "")


class QuickDecisionTests(unittest.TestCase):
    def test_one_call_can_retain_previous_answer_and_explicit_help_cannot_be_suppressed(self):
        context, _ = seed("repeat_after_answer")
        base = SimpleNamespace(
            choice=Settings().quick,
            generate_json=Mock(
                return_value={
                    "answer_needed": False,
                    "source_ids": [],
                    "reason": "The proposed fix still covers this view.",
                    "response": None,
                }
            ),
        )
        provider = QuickProvider(base)
        snapshot = replace(context.snapshot(), automatic_refresh=True)
        self.assertIsInstance(provider.generate(snapshot, "quick", Cancellation(), lambda _: None), NoAnswerUpdate)
        self.assertEqual(base.generate_json.call_count, 1)
        sent = json.loads(base.generate_json.call_args.kwargs["prompt"])
        self.assertTrue(sent["previous_answer"])
        with self.assertRaises(ValueError):
            provider.generate(replace(snapshot, automatic_refresh=False), "quick", Cancellation(), lambda _: None)

    def test_unknown_decision_sources_cannot_publish_a_summary(self):
        context, _ = seed("new_question")
        raw = {
            "answer_needed": True,
            "source_ids": ["invented"],
            "reason": "New question",
            "response": {"task": "Task", "summary": "Bad source", "conversation": [], "open_questions": []},
        }
        base = SimpleNamespace(choice=Settings().quick, generate_json=lambda *a, **k: raw)
        with self.assertRaises(ValueError):
            QuickProvider(base).generate(context.snapshot(), "quick", Cancellation(), lambda _: None)


class CombinedAnswerTests(unittest.TestCase):
    def test_single_call_validates_unseen_json_and_isolates_a_bad_section(self):
        context, _ = seed("new_question")
        fields = [
            {
                "key": key,
                "label": key,
                "purpose": "Test data",
                "instructions": "Use evidence",
                "presentation": "json",
                "data_schema": '{"type":"object","properties":{"a":{"type":"integer"}},"required":["a"]}',
            }
            for key in ("valid", "invalid")
        ]
        plan = {
            "task": "Task",
            "approach": "Return data",
            "instructions": ["Return data"],
            "assumptions": [],
            "quality_checks": ["Check type"],
            "source_ids": ["new-evidence"],
            "outputs": fields,
        }
        raw = {
            "decision": {
                "answer_needed": True,
                "answer_reason": "New data",
                "reuse_previous": False,
                "reason": "New contract",
                "plan": plan,
            },
            "task": "Task",
            "summary": "Data",
            "conversation": [],
            "observed_files": [],
            "open_questions": [],
            "outputs": [
                {"key": "valid", "source_ids": ["new-evidence"], "value_json": '{"a":2}'},
                {"key": "invalid", "source_ids": ["new-evidence"], "value_json": "not json"},
            ],
        }
        base = SimpleNamespace(choice=Settings().deep, generate_json=Mock(return_value=raw), last_raw_text="")
        response = CombinedTaskProvider(base).generate(context.snapshot(), "deep", Cancellation(), lambda _: None)
        self.assertEqual(base.generate_json.call_count, 1)
        self.assertEqual(json.loads(next(a.content for a in response.artifacts if a.id == "valid")), {"a": 2})
        self.assertNotIn("invalid", [a.id for a in response.artifacts])
        self.assertTrue(response._delivery_notes)
        self.assertEqual(response._task_plan, plan)

    def test_single_no_change_decision_does_not_request_an_answer(self):
        context, _ = seed("repeat_after_answer")
        raw = {
            "decision": {
                "answer_needed": False,
                "answer_reason": "Still covered",
                "reuse_previous": True,
                "reason": "Same task",
                "plan": None,
            },
            "task": "Task",
            "summary": "",
            "conversation": [],
            "observed_files": [],
            "open_questions": [],
            "outputs": [],
        }
        base = SimpleNamespace(choice=Settings().deep, generate_json=Mock(return_value=raw))
        result = CombinedTaskProvider(base).generate(
            replace(context.snapshot(), automatic_refresh=True), "deep", Cancellation(), lambda _: None
        )
        self.assertIsInstance(result, NoAnswerUpdate)
        self.assertEqual(base.generate_json.call_count, 1)


class IndependentLaneTests(unittest.TestCase):
    def test_new_quick_question_finishes_while_older_deep_is_blocked(self):
        context = ContextStore()
        context.set_goal("Task")
        deep_started, release = threading.Event(), threading.Event()

        class Provider:
            def generate(self, snapshot, lane, token, progress):
                if lane == "deep":
                    deep_started.set()
                    release.wait(3)
                    return answer("Older deep reply")
                return answer("Quick " + str(snapshot.evidence_revision))

        coordinator = Coordinator(context, lambda _: Provider(), independent_quick=True)

        def next_result(lane):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                event = coordinator.events.get(timeout=1)
                if event["type"] in {"result", "error", "unchanged"}:
                    coordinator.accept(event)
                    if event["job"].lane == lane:
                        return event
            self.fail("No result")

        try:
            self.assertTrue(coordinator.request())
            self.assertTrue(deep_started.wait(1))
            first = next_result("quick")
            context.add("speech", "What about the second case?", "system", "other_people")
            self.assertTrue(coordinator.request())
            second = next_result("quick")
            self.assertNotEqual(first["job"].snapshot.evidence_revision, second["job"].snapshot.evidence_revision)
            self.assertIn("deep", coordinator.active_lanes)
            self.assertFalse(coordinator.request())  # Same evidence doesn't start another quick call.
            release.set()
            old = next_result("deep")
            self.assertTrue(old["duplicate"])  # Old text cannot replace the newer quick reply.
            self.assertEqual(coordinator.current.summary, second["response"].summary)
        finally:
            release.set()
            coordinator.close()

    def test_manual_request_supersedes_both_lane_jobs_and_noop_keeps_history(self):
        context = ContextStore()
        context.set_goal("Task")

        class Provider:
            def generate(self, snapshot, lane, token, progress):
                return NoAnswerUpdate(reason="No new assistance needed")

        c = Coordinator(context, lambda _: Provider(), independent_quick=True)
        try:
            c.request()
            old = dict(c.lane_jobs)
            c.request(manual=True)
            for lane, job in old.items():
                self.assertTrue(job.token.event.is_set())
                self.assertFalse(c.accept({"type": "result", "job": job, "response": answer("old")}))
            deadline = time.monotonic() + 2
            while c.active_lanes and time.monotonic() < deadline:
                c.accept(c.events.get(timeout=1))
            self.assertEqual(c.history, [])
            self.assertIsNone(c.current)
        finally:
            c.close()
