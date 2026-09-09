import threading
import time
import unittest

from otsc.context import ContextStore
from otsc.models import Artifact, Assistance, ConversationResponse, LineAnnotation, ObservedFile
from otsc.scheduler import Coordinator


def response(snapshot, summary="A useful response"):
    return Assistance(
        task=snapshot.goal or "Current task",
        summary=summary,
        conversation=[],
        artifacts=[],
        observed_files=[],
        open_questions=[],
    )


class ContextTests(unittest.TestCase):
    def test_same_screen_and_clock_changes_do_not_trigger_new_work(self):
        c = ContextStore()
        c.add("screen", "editor.py\nclock 12:01\nreturn value", "screen")
        version = c.revision
        self.assertIsNone(c.add("screen", "editor.py\nclock 12:02\nreturn value", "screen"))
        self.assertEqual(c.revision, version)
        self.assertIsNotNone(c.add("screen", "editor.py\nclock 12:02\nreturn other", "screen"))

    def test_speakers_remain_separate_and_snapshots_do_not_change(self):
        c = ContextStore()
        c.add("speech", "Use a queue", "system", "other_people")
        snapshot = c.snapshot()
        c.add("speech", "Use a queue", "microphone", "primary_user")
        self.assertEqual(len(snapshot.observations), 1)
        self.assertEqual([o.speaker for o in c.observations], ["other_people", "primary_user"])
        c.observations[0].text = "Edited current state"
        self.assertEqual(snapshot.observations[0].text, "Use a queue")

    def test_observed_files_preserve_conflicting_fragments_without_becoming_verified(self):
        c = ContextStore()
        observation = c.add("screen", "file.py\nreturn 1", "screen")
        first = response(c.snapshot())
        first.observed_files = [
            ObservedFile(
                path="file.py", content="return 1", first_line=12, source_ids=[observation.id], confidence="medium"
            )
        ]
        c.integrate(first)
        second = first.model_copy(deep=True)
        second.observed_files[0].content = "return 2"
        c.integrate(second)
        self.assertEqual(len(c.files["file.py"]), 2)
        self.assertIn("not_verified_files", c.workspace_text())

    def test_code_annotations_are_required_and_clean_copy_is_exact(self):
        args = dict(
            id="mean",
            kind="code",
            title="Mean",
            content="def mean(x):\n    return sum(x) / len(x)\n",
            language="python",
            path="",
            basis="example",
            source_ids=["obs"],
            nodes=[],
            edges=[],
        )
        with self.assertRaises(ValueError):
            Artifact(**args, annotations=[LineAnnotation(line=1, explanation="Define the function")])
        code = Artifact(
            **args,
            annotations=[
                LineAnnotation(line=1, explanation="Define the function"),
                LineAnnotation(line=2, explanation="Compute the mean"),
            ],
        )
        self.assertEqual(code.clean_text(), args["content"])
        self.assertIn("Compute the mean", code.annotated_text())
        self.assertNotIn("Compute the mean", code.clean_text())

    def test_conversation_reply_must_target_actual_evidence(self):
        c = ContextStore()
        c.set_goal("Finish the design")
        r = response(c.snapshot())
        r.conversation = [ConversationResponse(source_id="invented", action="answer", text="Yes", artifact_effect="")]
        with self.assertRaises(ValueError):
            r.validate_sources(c.observations)


class SchedulerTests(unittest.TestCase):
    def wait_results(self, coordinator, count=1, timeout=2):
        results, deadline = [], time.monotonic() + timeout
        while len(results) < count and time.monotonic() < deadline:
            item = coordinator.events.get(timeout=timeout)
            if item["type"] == "result":
                results.append(item)
        self.assertEqual(len(results), count)
        return results

    def test_lanes_start_in_parallel_and_fast_cannot_replace_completed_deep(self):
        started = {name: threading.Event() for name in ("quick", "deep")}
        release = threading.Event()

        class Provider:
            def generate(self, snapshot, lane, token, progress):
                started[lane].set()
                if lane == "quick":
                    release.wait(2)
                return response(snapshot, lane)

        c = ContextStore()
        c.set_goal("Build a queue")
        coordinator = Coordinator(c, lambda lane: Provider())
        try:
            coordinator.request(manual=True)
            self.assertTrue(started["quick"].wait(1))
            self.assertTrue(started["deep"].wait(1))
            deep = self.wait_results(coordinator)[0]
            self.assertTrue(coordinator.accept(deep))
            release.set()
            quick = self.wait_results(coordinator)[0]
            self.assertFalse(coordinator.accept(quick))
            self.assertEqual(coordinator.current.summary, "deep")
        finally:
            release.set()
            coordinator.close()

    def test_changed_context_and_pinning_cannot_silently_replace_current_work(self):
        class Provider:
            def generate(self, snapshot, lane, token, progress):
                return response(snapshot, lane)

        c = ContextStore()
        c.set_goal("First task")
        coordinator = Coordinator(c, lambda lane: Provider())
        try:
            coordinator.request(manual=True)
            events = self.wait_results(coordinator, 2)
            c.set_goal("Second task")
            self.assertFalse(any(coordinator.accept(e) for e in events))
            coordinator.request(manual=True)
            coordinator.pinned = True
            events = self.wait_results(coordinator, 2)
            for e in events:
                coordinator.accept(e)
            self.assertIsNone(coordinator.current)
            self.assertIsNotNone(coordinator.pending)
            coordinator.toggle_pin()
            self.assertEqual(coordinator.current.task, "Second task")
            self.assertFalse(coordinator.request())
        finally:
            coordinator.close()


if __name__ == "__main__":
    unittest.main()
