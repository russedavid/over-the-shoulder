import json
import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from otsc.context import ContextStore
from otsc.context_builder import ContextBuilder, ContextUpdate, apply_update
from otsc.models import Artifact, Assistance, ContextItem, ContextRemoval, ObservedFile, ScreenReading
from otsc.perception import read_screen
from otsc.prompts import build_prompt
from otsc.scheduler import Cancellation, Coordinator
from otsc.sessions import checkpoint, load_session, restore_context, save_session
from otsc.settings import Settings


def reading(text):
    return ScreenReading(visible_text=text, facts=[], inferred_task="", uncertainties=[], important_details=[])


def empty_answer(snapshot):
    return Assistance(task=snapshot.goal, summary="Latest useful answer", conversation=[], artifacts=[],
                      observed_files=[], open_questions=[])


def delta(**kwargs):
    return ContextUpdate(**{"upsert": [], "remove": [], "observed_files": [], "retire_files": [], **kwargs})


def next_result(coordinator, *, include_cancelled=False):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        event = coordinator.events.get(timeout=2)
        if include_cancelled and event["type"] == "cancelled":
            return event
        if event["type"] == "result":
            return event
    raise AssertionError("No answer arrived")


class TimelineTests(unittest.TestCase):
    def test_full_five_minutes_includes_both_audio_channels_and_long_debug_logs(self):
        context = ContextStore(clock=lambda: 1000)
        context.add("speech", "Expired", "system", "other_people", at=699)
        for i in range(150):
            context.add("speech", f"Question {i}", "system", "other_people", at=700 + i * 2)
            context.add("speech", f"Answer {i}", "microphone", "primary_user", at=700 + i * 2)
        logs = "\n".join(f"Shift {i} completed. Total: {i}" for i in range(500))
        for i in range(10):
            context.add("screen", f"Frame {i}\n{logs}", "screen", at=710 + i * 29, keep_repeats=True)
        snapshot = context.snapshot()
        self.assertEqual(len(snapshot.recent_observation_ids), 310)
        self.assertGreater(sum(len(o.text) for o in snapshot.observations), 24000)
        self.assertNotIn("Expired", [o.text for o in snapshot.observations])
        self.assertEqual(sum(o.speaker == "primary_user" for o in snapshot.observations), 150)
        self.assertTrue(all(logs in o.text for o in snapshot.observations if o.kind == "screen"))
        self.assertFalse(snapshot.omitted_observation_ids)

    def test_expired_raw_evidence_remains_available_when_memory_cites_it(self):
        now = [1000]
        context = ContextStore(clock=lambda: now[0])
        source = context.add("speech", "Draw a queue-based delivery system", "microphone", "primary_user")
        item = ContextItem(id="deliverable", kind="task", text="Create the delivery diagram", source_ids=[source.id], basis="reported")
        apply_update(context, context.snapshot(), delta(upsert=[item]))
        now[0] = 1400
        context.add("speech", "How would we retry?", "system", "other_people")
        snapshot = context.snapshot()
        self.assertNotIn(source.id, snapshot.recent_observation_ids)
        self.assertIn(source.id, [o.id for o in snapshot.observations])
        self.assertEqual(json.loads(snapshot.context_items)[0]["id"], "deliverable")

    def test_repeated_screen_refreshes_the_window_without_triggering_identical_work(self):
        now = [1000]
        context = ContextStore(clock=lambda: now[0])
        context.add("screen", "same code", "screen", keep_repeats=True)
        revision = context.revision
        now[0] += 29
        recent = context.add("screen", "same code", "screen", keep_repeats=True)
        self.assertEqual(context.revision, revision)
        self.assertIn(recent.id, context.snapshot().recent_observation_ids)
        altered = reading("same labels")
        context.add("screen", altered.visible_text, "screen", reading=altered, keep_repeats=True)
        revision = context.revision
        altered.important_details = ["Arrow now runs from B to A"]
        context.add("screen", altered.visible_text, "screen", reading=altered, keep_repeats=True)
        self.assertGreater(context.revision, revision)

    def test_diagram_and_previous_substantive_answer_survive_a_quick_followup(self):
        context = ContextStore()
        context.set_goal("Draw a delivery system")
        response = empty_answer(context.snapshot())
        response.artifacts = [Artifact(id="delivery", kind="diagram", title="Delivery design", content="Durable work queue",
                                      language="", path="", basis="discussion", source_ids=[context.observations[0].id], annotations=[],
                                      nodes=[{"id": "ingress", "label": "Ingress"}, {"id": "queue", "label": "Queue"}],
                                      edges=[{"source": "ingress", "target": "queue", "label": "enqueue"}])]
        context.integrate(response)
        context.integrate(empty_answer(context.snapshot()), replace_open_questions=False)
        prompt = build_prompt(context.snapshot(), "deep")
        self.assertIn('"previous_answer":', prompt)
        self.assertIn('"source": "ingress", "target": "queue"', prompt)
        self.assertEqual(json.loads(context.previous_answer)["artifacts"][0]["id"], "delivery")


class ContextBuilderTests(unittest.TestCase):
    def test_memory_revisions_retire_superseded_notes_without_changing_user_decisions(self):
        context = ContextStore()
        context.set_goal("Draw a service")
        context.set_task_details(["Durable storage"], ["Use a queue"])
        source = context.add("speech", "Keep events for seven days", "microphone", "primary_user")
        item = ContextItem(id="retention", kind="requirement", text="Keep events for seven days", source_ids=[source.id], basis="reported")
        self.assertTrue(apply_update(context, context.snapshot(), delta(upsert=[item]))[0])
        old_snapshot = context.snapshot()
        new = context.add("speech", "Actually retain them for thirty days", "microphone", "primary_user")
        updated = item.model_copy(update={"text": "Retain for thirty days", "source_ids": [new.id]})
        apply_update(context, context.snapshot(), delta(upsert=[updated]))
        self.assertEqual(len(context.context_items), 1)
        self.assertEqual(context.context_items["retention"].text, updated.text)
        self.assertFalse(apply_update(context, old_snapshot, delta(upsert=[item]))[0])
        apply_update(context, context.snapshot(), delta(remove=[ContextRemoval(id="retention", reason="No longer needed", source_ids=[new.id])]))
        self.assertFalse(context.context_items)
        self.assertEqual(context.decisions, ("Use a queue",))
        self.assertEqual(context.constraints, ("Durable storage",))

    def test_optional_files_require_literal_source_and_retirement_keeps_history(self):
        context = ContextStore()
        source = context.add("screen", "src/job.py\nreturn 1", "screen")
        source2 = context.add("speech", "Removed src/job.py", "microphone", "primary_user")
        good = ObservedFile(path="src/job.py", content="return 1", first_line=5, source_ids=[source.id], confidence="high")
        invented = good.model_copy(update={"path": "fake.py", "content": "invented()"})
        item = ContextItem(id="status", kind="progress", text="Reviewing job.py", source_ids=[source.id], basis="observed")
        changed, notes = apply_update(context, context.snapshot(), delta(upsert=[item], observed_files=[invented, good]))
        self.assertTrue(changed)
        self.assertEqual(list(context.files), ["src/job.py"])
        self.assertTrue(notes)
        apply_update(context, context.snapshot(), delta(retire_files=[ContextRemoval(id="src/job.py", reason="User reported removal", source_ids=[source2.id])]))
        self.assertIn("src/job.py", context.files)
        self.assertNotIn("src/job.py", context.workspace_text())
        self.assertFalse(context.verified_files)

    def test_unknown_sources_and_spoken_observation_claims_are_rejected(self):
        context = ContextStore()
        source = context.add("speech", "There is a database", "system", "other_people")
        item = ContextItem(id="db", kind="progress", text="Database observed", source_ids=[source.id], basis="observed")
        fake = item.model_copy(update={"id": "fake", "source_ids": ["invented"]})
        changed, notes = apply_update(context, context.snapshot(), delta(upsert=[item, fake]))
        self.assertFalse(changed)
        self.assertFalse(context.context_items)
        self.assertEqual(len(notes), 2)

    def test_builder_and_answers_progress_independently_with_new_speech_during_work(self):
        entered, release = threading.Event(), threading.Event()
        seen = []

        class BuilderProvider:
            def generate_json(self, snapshot, lane, token, progress, **kwargs):
                seen.append(snapshot)
                entered.set()
                release.wait(2)
                token.check()
                return delta().model_dump()

        class AnswerProvider:
            def generate(self, snapshot, lane, token, progress):
                return empty_answer(snapshot)

        context = ContextStore()
        context.set_goal("Draw the system")
        builder = ContextBuilder(context, BuilderProvider)
        answers = Coordinator(context, lambda lane: AnswerProvider())
        try:
            self.assertTrue(builder.request())
            self.assertTrue(entered.wait(1))
            self.assertFalse(builder.request())
            answers.request()
            speech = context.add("speech", "What about duplicate delivery?", "system", "other_people")
            accepted = [answers.accept(next_result(answers)) for _ in range(2)]
            self.assertTrue(any(accepted))
            self.assertEqual(answers.published_lane, 1)
            self.assertIsNotNone(answers.current)
            self.assertIsNotNone(builder.job)
            self.assertNotIn(speech.id, [o.id for o in seen[0].observations])
            release.set()
            builder.accept(builder.events.get(timeout=2))
            self.assertTrue(builder.request())
            builder.accept(builder.events.get(timeout=2))
            self.assertIn(speech.id, [o.id for o in seen[-1].observations])
            self.assertFalse(builder.request(), "A completed no-op must not create a memory feedback loop")
        finally:
            release.set()
            builder.close()
            answers.close()

    def test_cancellation_discards_a_delayed_context_update(self):
        release = threading.Event()
        entered = threading.Event()

        class Provider:
            def generate_json(self, snapshot, *args, **kwargs):
                entered.set()
                release.wait(2)
                return delta(upsert=[ContextItem(id="old", kind="task", text="Old task", source_ids=[snapshot.observations[0].id], basis="reported")]).model_dump()

        context = ContextStore()
        context.set_goal("Old task")
        builder = ContextBuilder(context, Provider)
        try:
            builder.request()
            self.assertTrue(entered.wait(1))
            builder.cancel()
            context.clear()
            release.set()
            self.assertFalse(builder.accept(builder.events.get(timeout=2)))
            self.assertFalse(context.context_items)
        finally:
            release.set()
            builder.close()

    def test_checkpoint_keeps_memory_sources_but_cannot_restore_image_access(self):
        context = ContextStore()
        source = context.add("screen", "Keep events for seven days", "screen", image_path="/private.png")
        apply_update(context, context.snapshot(), delta(upsert=[ContextItem(id="retention", kind="requirement", text=source.text,
                                                                          source_ids=[source.id], basis="observed")]))
        coordinator = SimpleNamespace(current=None, history=[], pinned=False)
        with tempfile.TemporaryDirectory() as temp:
            path = save_session(checkpoint(context, coordinator), Path(temp) / "session.json")
            restored = restore_context(load_session(path))
        self.assertIn("retention", restored.context_items)
        self.assertEqual(restored.retained_sources[source.id].image_path, "")
        self.assertFalse(restored.verified_files)


class PerceptionTests(unittest.TestCase):
    def test_ocr_requires_an_actual_image_and_preserves_structured_evidence(self):
        provider = SimpleNamespace(choice=Settings().ocr, generate_json=Mock(return_value=reading("Actual code and logs").model_dump()))
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"OTSC_DATA_DIR": temp}):
            target = Path(temp) / "captures" / "test.png"
            target.parent.mkdir()
            with self.assertRaises(ValueError):
                read_screen(target, provider, Cancellation(), lambda text: None)
            provider.generate_json.assert_not_called()
            target.write_bytes(b"synthetic image bytes for transport")
            result = read_screen(target, provider, Cancellation(), lambda text: None)
            self.assertEqual(result.visible_text, "Actual code and logs")
            kwargs = provider.generate_json.call_args.kwargs
            self.assertIn("debug logs", kwargs["prompt"])
            self.assertEqual(provider.generate_json.call_args.args[1], "ocr")
            self.assertEqual(Settings().ocr.model, "gpt-6-astra")
            self.assertEqual(Settings().ocr.reasoning, "low")
            self.assertTrue(Settings().ocr.fast_mode)

    def test_newer_request_or_changed_task_still_rejects_old_answers(self):
        class Provider:
            def generate(self, snapshot, lane, token, progress):
                return empty_answer(snapshot)

        context = ContextStore()
        context.set_goal("First task")
        coordinator = Coordinator(context, lambda lane: Provider())
        try:
            coordinator.request()
            event = next_result(coordinator)
            context.add("speech", "New evidence", "system", "other_people")
            self.assertTrue(coordinator.accept(event))
            context.set_goal("Different task")
            # An obsolete request may now be stopped before its provider runs,
            # or finish before the task edit and then be rejected on delivery.
            stale = next_result(coordinator, include_cancelled=True)
            self.assertIn(stale["type"], {"result", "cancelled"})
            self.assertFalse(coordinator.accept(stale))
            coordinator.request(manual=True)
            next_event = next_result(coordinator)
            coordinator.request(manual=True)
            self.assertFalse(coordinator.accept(next_event))
        finally:
            coordinator.close()


@unittest.skipUnless(sys.platform == "darwin", "Controller integration uses PyObjC")
class ContinuousControllerTests(unittest.TestCase):
    def test_help_now_uses_available_context_without_waiting_for_ocr_but_waits_for_audio(self):
        from otsc.app import Controller

        context = ContextStore()
        controller = SimpleNamespace(
            manual_help_pending=True, pending_manual=True, capture_busy=True, awaiting_audio_flush=None,
            context=context, flush_context=Mock(), coordinator=SimpleNamespace(request=Mock()), finish_voice_capture=Mock(),
        )
        Controller.send_manual_help_if_ready(controller)
        controller.coordinator.request.assert_not_called()
        context.set_goal("Draw the architecture")
        controller.awaiting_audio_flush = "pending-utterance"
        Controller.send_manual_help_if_ready(controller)
        controller.coordinator.request.assert_not_called()
        controller.awaiting_audio_flush = None
        Controller.send_manual_help_if_ready(controller)
        controller.coordinator.request.assert_called_once_with(manual=True)
        self.assertTrue(controller.capture_busy)
        self.assertFalse(controller.manual_help_pending)

    def test_screen_repeats_after_completion_while_builder_and_answer_are_busy(self):
        from otsc.app import Controller

        capture_calls, active, peak = [], [0], [0]
        entered, release = threading.Event(), threading.Event()

        class Screen:
            def capture(self, *args, **kwargs):
                capture_calls.append(time.monotonic())
                self.assert_full_resolution = kwargs["full_resolution"]
                if kwargs.get("on_captured"):
                    kwargs["on_captured"]()
                return "discard local OCR", "frame.png"

        class Provider:
            def generate_json(self, *args, **kwargs):
                entered.set()
                release.wait(3)
                return delta().model_dump()

        def ocr(*args, **kwargs):
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            time.sleep(0.01)
            active[0] -= 1
            return reading(f"Source frame {len(capture_calls)}")

        context = ContextStore()
        context.set_goal("Draw a system")
        builder = ContextBuilder(context, Provider)
        coordinator = SimpleNamespace(events=queue.Queue(), active_lanes={"deep"}, request=Mock(), last_requested_revision=-1)
        controller = SimpleNamespace(
            closed=False, demo=False, options=SimpleNamespace(smoke_test=False),
            context=context, context_builder=builder, coordinator=coordinator,
            events=queue.Queue(), settings=Settings(configured=True), credentials=None,
            screen=Screen(), window=Mock(), status=Mock(), goal=SimpleNamespace(stringValue=lambda: context.goal),
            running=True, capture_busy=False, capture_id="", capture_generation=0, capture_request_after=False,
            capture_token=None, capture_failures=0, next_capture=0, manual_help_pending=False, voice_recording=False,
            context_build_pending=False, auto_help_pending=False, overlay_hidden=False, next_answer_attempt=0,
            window_sharing=False, capture_hidden_for="", capture_frame_pending=False, controls={"sharing": Mock()},
            trace=SimpleNamespace(record=lambda *a, **kw: None), refresh_body=lambda: None,
            refresh_passive_views=lambda: None,
            checkpoint_if_enabled=lambda: None,
        )
        controller.capture_context = lambda **kw: Controller.capture_context(controller, **kw)
        controller.handle_event = lambda event: Controller.handle_event(controller, event)
        controller.apply_context_event = lambda event: Controller.apply_context_event(controller, event)
        controller.finish_screen_capture = lambda identity: Controller.finish_screen_capture(controller, identity)
        try:
            with (patch("otsc.app.read_screen", side_effect=ocr), patch("otsc.app.provider_for", return_value=None),
                  patch("otsc.app.AppHelper.callAfter", side_effect=lambda fn, *args: fn(*args))):
                deadline = time.monotonic() + 2
                while len(capture_calls) < 3 and time.monotonic() < deadline:
                    Controller.tick_.callable(controller, None)
                    time.sleep(0.01)
                controller.running = False
                time.sleep(0.03)
                Controller.tick_.callable(controller, None)
            self.assertGreaterEqual(len(capture_calls), 3)
            self.assertTrue(entered.is_set())
            self.assertIsNotNone(builder.job)
            self.assertEqual(peak[0], 1)
            self.assertTrue(controller.screen.assert_full_resolution)
            self.assertLess(capture_calls[2] - capture_calls[0], 1)
            self.assertTrue(any(o.text.startswith("Source frame") for o in context.observations))
            coordinator.request.assert_not_called()
            controller.window.orderOut_.assert_not_called()
            controller.window.orderFront_.assert_not_called()
            controller.window.makeKeyAndOrderFront_.assert_not_called()
            controller.window.orderFrontRegardless.assert_not_called()
        finally:
            controller.running = False
            if controller.capture_token:
                controller.capture_token.cancel()
            release.set()
            builder.close()

    def test_sharing_controls_only_the_capture_hide_and_ten_ms_wait(self):
        from otsc.app import Controller
        from otsc.capture import ScreenCapture

        for mode in ("off", "on", "failure", "cancel", "user_hidden"):
            with self.subTest(mode=mode):
                order = []
                context = ContextStore()
                context.set_goal("Current task")
                controller = SimpleNamespace(
                    context=context, settings=Settings(), goal=SimpleNamespace(stringValue=lambda: context.goal),
                    capture_busy=False, capture_request_after=False, capture_generation=0, capture_id="",
                    capture_hidden_for="", capture_frame_pending=False, window_sharing=mode != "off",
                    overlay_hidden=False, closed=False, controls={"sharing": Mock()}, status=Mock(), window=Mock(),
                    events=queue.Queue(), credentials=None, screen=ScreenCapture(), trace=Mock(),
                )
                controller.window.isVisible.return_value = True
                controller.window.isMiniaturized.return_value = False
                controller.window.orderOut_.side_effect = lambda _: order.append("hide")
                controller.window.orderFrontRegardless.side_effect = lambda: order.append("restore")
                controller.finish_screen_capture = lambda identity: Controller.finish_screen_capture(controller, identity)
                token = Cancellation()

                def wait(delay):
                    if mode == "cancel":
                        token.cancel()
                        return True
                    return False

                token.event.wait = Mock(side_effect=wait)

                def screenshot(**kwargs):
                    order.append("capture")
                    if mode == "failure":
                        raise RuntimeError("Synthetic screenshot failure")
                    if mode == "user_hidden":
                        controller.overlay_hidden = True
                    return object()

                def interpret(*args, **kwargs):
                    order.append("local_ocr")
                    return "unused local text", "fixture.png"

                def ocr(*args, **kwargs):
                    order.append("astra")
                    return reading("Current screen")

                with (patch("otsc.app.Cancellation", return_value=token),
                      patch("pyautogui.screenshot", side_effect=screenshot),
                      patch.object(controller.screen, "interpret", side_effect=interpret),
                      patch("otsc.app.read_screen", side_effect=ocr),
                      patch("otsc.app.provider_for", return_value=None),
                      patch("otsc.app.AppHelper.callAfter", side_effect=lambda fn, *args: fn(*args))):
                    Controller.capture_context(controller)
                    result = controller.events.get(timeout=2)
                if mode == "off":
                    token.event.wait.assert_not_called()
                    self.assertEqual(order, ["capture", "local_ocr", "astra"])
                else:
                    token.event.wait.assert_called_once_with(0.010)
                    if mode == "on":
                        self.assertEqual(order, ["hide", "capture", "restore", "local_ocr", "astra"])
                    elif mode == "failure":
                        self.assertEqual(order, ["hide", "capture", "restore"])
                        self.assertIn("Synthetic screenshot failure", result["error"])
                    elif mode == "cancel":
                        self.assertEqual(order, ["hide", "restore"])
                        self.assertTrue(result["cancelled"])
                    else:
                        self.assertEqual(order, ["hide", "capture", "local_ocr", "astra"])
                self.assertFalse(controller.capture_frame_pending)
                self.assertEqual(controller.capture_hidden_for, "")
                controller.controls["sharing"].setEnabled_.assert_called_with(True)

    def test_old_capture_callbacks_cannot_reopen_the_window(self):
        from otsc.app import Controller

        controller = SimpleNamespace(capture_id="new", capture_hidden_for="new", capture_frame_pending=True,
                                     window=Mock(), controls={"sharing": Mock()}, closed=False, overlay_hidden=False)
        Controller.finish_screen_capture(controller, "old")
        self.assertTrue(controller.capture_frame_pending)
        self.assertEqual(controller.capture_hidden_for, "new")
        controller.window.orderFrontRegardless.assert_not_called()


if __name__ == "__main__":
    unittest.main()
