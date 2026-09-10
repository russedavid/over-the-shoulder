import json
import sys
import threading
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

from otsc.context import ContextStore
from otsc.context_builder import ContextUpdate, apply_update
from otsc.models import Artifact, Assistance, ContextItem, ConversationResponse, NoAnswerUpdate
from otsc.output_history import OutputHistory
from otsc.planning import TaskPlanningProvider
from otsc.scheduler import Cancellation, Coordinator, answer_fingerprint
from otsc.settings import ModelChoice


def terminal(coordinator, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = coordinator.events.get(timeout=max(.01, deadline - time.monotonic()))
        if event["type"] in {"result", "error", "cancelled", "unchanged"}:
            return event
    raise AssertionError("No terminal event")


def seed():
    context = ContextStore()
    context.set_goal("Explain how empty measurements stay distinct from zero.")
    source = context.observations[0].id
    plan = dict(task=context.goal, approach="Keep missing data distinct from actual measurements.",
                instructions=["Explain the empty-input contract"], assumptions=[], quality_checks=["Zero stays valid"],
                source_ids=[source], outputs=[dict(key="guidance", label="Guidance", purpose="Explain the contract",
                                                   instructions="Use None for empty input", presentation="text", data_schema="")])
    answer = Assistance(task=context.goal, summary="Empty input returns None; zero is a valid measurement.",
                        artifacts=[Artifact(id="guidance", kind="explanation", title="Guidance", content="Return None for empty input.",
                                            basis="discussion", source_ids=[source], path="", language="", annotations=[], nodes=[], edges=[])],
                        conversation=[], observed_files=[], open_questions=[])
    answer._task_plan = plan
    context.integrate(answer)
    return context, answer


class Planner:
    def __init__(self, *, needed=False, wait=None, entered=None):
        self.needed, self.wait, self.entered = needed, wait, entered
        self.calls = []

    def generate_json(self, snapshot, lane, token, progress, **kwargs):
        self.calls.append(kwargs)
        if self.entered:
            self.entered.set()
        if self.wait:
            self.wait.wait(2)
        token.check()
        return dict(answer_needed=self.needed, answer_reason="A new question needs an answer." if self.needed else "Only OCR wording changed.",
                    reuse_previous=True, reason="The output contract still fits.", plan=None)


class AnswerProvider:
    choice = ModelChoice()

    def __init__(self, original, *, entered=None, release=None):
        self.original, self.entered, self.release = original, entered, release
        self.json_calls, self.quick_calls = 0, 0
        self.snapshots = {}

    def generate_json(self, snapshot, lane, token, progress, **kwargs):
        self.json_calls += 1
        self.snapshots[lane] = snapshot
        if self.entered:
            self.entered.set()
        if self.release:
            self.release.wait(2)
        token.check()
        return dict(task=snapshot.goal, summary="A new explanation.", conversation=[], observed_files=[], open_questions=[],
                    outputs={"guidance": "Empty input means unknown, not zero."},
                    output_sources={"guidance": [snapshot.observations[0].id]})

    def generate(self, snapshot, lane, token, progress):
        self.quick_calls += 1
        self.snapshots[lane] = snapshot
        result = self.original.model_copy(deep=True)
        result.artifacts = []
        return result


class RefreshGateTests(unittest.TestCase):
    def make(self, *, needed=False, planner=None, base=None):
        context, answer = seed()
        planner = planner or Planner(needed=needed)
        base = base or AnswerProvider(answer)
        planned = TaskPlanningProvider(base, planner)
        coordinator = Coordinator(context, lambda lane: planned if lane == "deep" else base)
        coordinator.current = answer
        self.addCleanup(coordinator.close)
        return context, answer, planner, base, planned, coordinator

    def test_unchanged_evidence_skips_both_answer_generations(self):
        context, answer, planner, base, _, coordinator = self.make()
        context.add("screen", "Measurements: missing remains unknown", "screen")
        self.assertTrue(coordinator.request())
        event = terminal(coordinator)
        self.assertEqual(event["type"], "unchanged")
        self.assertTrue(coordinator.accept(event))
        self.assertEqual(base.json_calls, 0)
        self.assertEqual(base.quick_calls, 0)
        self.assertEqual(len(planner.calls), 1)
        self.assertIs(coordinator.current, answer)
        self.assertEqual(coordinator.history, [])
        self.assertFalse(coordinator.active_lanes)
        self.assertFalse(coordinator.request())

    def test_memory_rewording_does_not_start_another_review(self):
        context, _, planner, base, _, coordinator = self.make()
        coordinator.request()
        coordinator.accept(terminal(coordinator))
        evidence_revision = context.evidence_revision
        item = ContextItem(id="missing", kind="requirement", text="Do not equate missing with zero.",
                           basis="inferred", source_ids=[context.observations[0].id])
        change = ContextUpdate(upsert=[item], remove=[], observed_files=[], retire_files=[])
        self.assertTrue(apply_update(context, context.snapshot(), change)[0])
        self.assertEqual(context.evidence_revision, evidence_revision)
        self.assertFalse(coordinator.request())
        self.assertEqual(coordinator.last_requested_revision, context.revision)
        self.assertEqual((len(planner.calls), base.json_calls, base.quick_calls), (1, 0, 0))

    def test_manual_help_starts_quick_immediately_and_bypasses_no_change_decision(self):
        entered, release = threading.Event(), threading.Event()
        context, _, planner, base, _, coordinator = self.make(planner=Planner(wait=release, entered=entered))
        try:
            self.assertTrue(coordinator.request(manual=True))
            self.assertTrue(entered.wait(1))
            quick = terminal(coordinator)
            self.assertEqual(quick["job"].lane, "quick")
            self.assertTrue(coordinator.accept(quick))
            self.assertFalse(quick["job"].snapshot.automatic_refresh)
            release.set()
            deep = terminal(coordinator)
            self.assertEqual(deep["type"], "result")
            self.assertEqual(deep["job"].lane, "deep")
            self.assertTrue(coordinator.accept(deep))
            self.assertEqual(base.json_calls, 1)
            self.assertIn('"refresh_request": "explicit_help"', planner.calls[0]["prompt"])
        finally:
            release.set()

    def test_substantive_change_releases_quick_while_deep_generation_continues(self):
        context, answer = seed()
        entered, release = threading.Event(), threading.Event()
        base = AnswerProvider(answer, entered=entered, release=release)
        context, _, _, base, _, coordinator = self.make(needed=True, base=base)
        context.add("speech", "What should we return for an all-missing batch?", "system", "other_people")
        try:
            self.assertTrue(coordinator.request())
            self.assertTrue(entered.wait(1))
            quick = terminal(coordinator)
            self.assertEqual(quick["job"].lane, "quick")
            self.assertTrue(coordinator.accept(quick))
            self.assertIn("deep", coordinator.active_lanes)
            release.set()
            deep = terminal(coordinator)
            self.assertTrue(coordinator.accept(deep))
            self.assertEqual(base.snapshots["quick"], base.snapshots["deep"])
            self.assertFalse(coordinator.active_lanes)
        finally:
            release.set()

    def test_new_speech_during_review_is_not_consumed_by_the_old_no_change_decision(self):
        entered, release = threading.Event(), threading.Event()
        context, _, planner, _, _, coordinator = self.make(planner=Planner(wait=release, entered=entered))
        try:
            coordinator.request()
            self.assertTrue(entered.wait(1))
            context.add("speech", "Actually, reject negative measurements.", "microphone", "primary_user")
            release.set()
            self.assertTrue(coordinator.accept(terminal(coordinator)))
            self.assertTrue(coordinator.request())
            self.assertTrue(coordinator.accept(terminal(coordinator)))
            self.assertEqual(len(planner.calls), 2)
            self.assertIn("reject negative", planner.calls[1]["prompt"])
        finally:
            release.set()

    def test_late_review_cannot_release_quick_for_a_superseded_task(self):
        entered, release = threading.Event(), threading.Event()
        context, _, _, base, _, coordinator = self.make(planner=Planner(needed=True, wait=release, entered=entered))
        try:
            coordinator.request()
            self.assertTrue(entered.wait(1))
            context.set_goal("A completely different task")
            coordinator.cancel()
            release.set()
            event = terminal(coordinator)
            self.assertFalse(coordinator.accept(event))
            self.assertEqual(base.quick_calls, 0)
            self.assertFalse(coordinator.active_lanes)
        finally:
            release.set()

    def test_connected_file_content_reaches_the_review(self):
        context, _, planner, _, _, coordinator = self.make()
        context.set_repo("/synthetic/project")
        context.set_verified_files("/synthetic/project", {"limit.py": "return value <= 10\n"})
        coordinator.request()
        coordinator.accept(terminal(coordinator))
        prompt = json.loads(planner.calls[0]["prompt"])
        self.assertEqual(prompt["verified_local_files"]["limit.py"], "return value <= 10\n")

    def test_duplicate_content_does_not_republish_for_new_citations_or_plan_notes(self):
        context, original = seed()
        observation = context.add("screen", "Empty input returns None", "screen")
        new = original.model_copy(deep=True)
        new.artifacts[0].source_ids = [observation.id]
        new.artifacts.append(Artifact(id="_assistance_plan", kind="structured", title="Plan", content='{"review":"rephrased"}',
                                      basis="discussion", source_ids=[observation.id], path="", language="json", annotations=[], nodes=[], edges=[]))
        coordinator = Coordinator(context, lambda lane: SimpleNamespace(generate=lambda *args: new))
        self.addCleanup(coordinator.close)
        coordinator.current = original
        coordinator.request()
        events = [terminal(coordinator), terminal(coordinator)]
        deep = next(e for e in events if e["job"].lane == "deep")
        self.assertTrue(coordinator.accept(deep))
        self.assertTrue(deep["duplicate"])
        self.assertIs(coordinator.current, original)
        self.assertEqual(coordinator.history, [])
        self.assertEqual(json.loads(context.previous_answer)["artifacts"][0]["source_ids"], [observation.id])
        changed = new.model_copy(deep=True)
        changed.artifacts[0].content = "Raise an error for empty input."
        self.assertNotEqual(answer_fingerprint(new), answer_fingerprint(changed))

    def test_failed_review_is_an_error_and_keeps_quick_assistance_available(self):
        context, _, planner, base, _, coordinator = self.make()
        planner.generate_json = Mock(side_effect=RuntimeError("Review unavailable"))
        coordinator.request()
        events = [terminal(coordinator), terminal(coordinator)]
        self.assertEqual({e["type"] for e in events}, {"error", "result"})
        self.assertEqual(base.quick_calls, 1)
        self.assertEqual(base.json_calls, 0)
        for event in events:
            coordinator.accept(event)
        self.assertFalse(coordinator.active_lanes)
        self.assertIn("could not complete", coordinator.last_refresh_reason)

    def test_duplicate_guard_preserves_a_new_question_or_different_speaker(self):
        context, original = seed()
        primary = context.add("speech", "Why None?", "microphone", "primary_user")
        other = context.add("speech", "Why None?", "system", "other_people")
        followup = context.add("speech", "What about a batch with no usable values?", "system", "other_people")
        original.conversation = [ConversationResponse(source_id=primary.id, action="answer", text="It indicates missing data.",
                                                     artifact_effect="Preserve the empty-input contract.")]
        before = answer_fingerprint(original, context.observations)
        changed = original.model_copy(deep=True)
        for source in (other, followup):
            changed.conversation[0].source_id = source.id
            self.assertNotEqual(before, answer_fingerprint(changed, context.observations))

    def test_no_change_review_does_not_invalidate_a_held_pending_answer(self):
        context, answer, _, _, _, coordinator = self.make()
        coordinator.pinned = True
        coordinator.pending = answer
        coordinator.pending_version = ("previous-request", context.session_id, context.task_revision)
        coordinator.current = None
        coordinator.request()
        coordinator.accept(terminal(coordinator))
        coordinator.toggle_pin()
        self.assertIs(coordinator.current, answer)
        self.assertIsNone(coordinator.pending)

    @unittest.skipUnless(sys.platform == "darwin", "AppKit presentation")
    def test_no_change_event_preserves_frozen_native_presentation_and_history(self):
        from otsc.app import Controller

        context, original, _, _, _, coordinator = self.make()
        history = OutputHistory()
        held = history.append(original, session_id=context.session_id, goal=context.goal, lane="deep")
        history.freeze()
        ui = SimpleNamespace(coordinator=coordinator, context=context, output_history=history, demo=True,
                             status=Mock(), refresh_passive_views=Mock(), render_response=Mock())
        coordinator.request()
        Controller.handle_event(ui, terminal(coordinator))
        self.assertEqual(len(history.entries), 1)
        self.assertIs(history.visible, held)
        self.assertTrue(history.frozen)
        ui.render_response.assert_not_called()
        self.assertIn("Keeping the current answer", ui.status.setStringValue_.call_args.args[0])

    def test_no_update_is_only_valid_for_automatic_requests_with_an_existing_answer(self):
        context, _, _, base, planned, coordinator = self.make()
        auto = replace(context.snapshot(), automatic_refresh=True)
        self.assertIsInstance(planned.generate(auto, "deep", Cancellation(), lambda text: None), NoAnswerUpdate)
        self.assertEqual(base.json_calls, 0)
        explicit = replace(auto, automatic_refresh=False)
        self.assertIsInstance(planned.generate(explicit, "deep", Cancellation(), lambda text: None), Assistance)
        self.assertEqual(base.json_calls, 1)
        first = replace(auto, previous_answer="{}")
        self.assertIsInstance(planned.generate(first, "deep", Cancellation(), lambda text: None), Assistance)
        self.assertEqual(base.json_calls, 2)


if __name__ == "__main__":
    unittest.main()
