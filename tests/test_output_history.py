import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from otsc.context import ContextStore
from otsc.models import Artifact, Assistance, ConversationResponse
from otsc.output_history import OutputHistory
from otsc.sessions import checkpoint, load_session, restore_context, save_session


def proposal(number, source="source"):
    return Assistance(
        task="Example task", summary=f"Proposal {number}", conversation=[], observed_files=[], open_questions=[],
        artifacts=[Artifact(id="answer", kind="code", title=f"Answer {number}", content=f"return {number}\n",
                            language="python", path="", basis="example", source_ids=[source],
                            annotations=[{"line": 1, "explanation": f"Return {number}."}], nodes=[], edges=[])],
    )


def add(history, number, **kwargs):
    return history.append(proposal(number), session_id="session", goal="goal", lane="deep", identity=str(number), **kwargs)


class OutputHistoryTests(unittest.TestCase):
    def test_selection_holds_exact_artifact_while_every_new_result_is_retained(self):
        history = OutputHistory()
        original = proposal(1)
        first = history.append(original, session_id="session", goal="goal", lane="deep")
        history.freeze()
        original.artifacts[0].content = "changed elsewhere"
        for number in (2, 3, 4):
            add(history, number)
        self.assertEqual(history.visible.id, first.id)
        self.assertEqual(history.visible.artifacts[0].clean_text(), "return 1\n")
        self.assertEqual(history.visible.artifacts[0].annotations[0].explanation, "Return 1.")
        self.assertEqual([item.response.summary for item in history.entries], ["Proposal 1", "Proposal 2", "Proposal 3", "Proposal 4"])
        self.assertEqual(history.newer_count, 3)

    def test_older_newer_hold_and_only_latest_resumes_updates(self):
        history = OutputHistory()
        for number in (1, 2, 3):
            add(history, number)
        self.assertTrue(history.move(-1))
        self.assertEqual(history.visible.response.summary, "Proposal 2")
        self.assertTrue(history.move(-1))
        self.assertFalse(history.move(-1))
        self.assertTrue(history.move(1))
        self.assertTrue(history.move(1))
        self.assertTrue(history.frozen)
        self.assertFalse(history.move(1))
        add(history, 4)
        self.assertEqual(history.visible.response.summary, "Proposal 3")
        history.resume()
        self.assertEqual(history.visible.response.summary, "Proposal 4")
        add(history, 5)
        self.assertEqual(history.visible.response.summary, "Proposal 5")

    def test_quick_replies_keep_latest_artifacts_not_the_browsed_ones(self):
        history = OutputHistory()
        add(history, 1)
        history.freeze()
        add(history, 2)
        quick = proposal(3)
        quick.artifacts = []
        history.append(quick, session_id="session", goal="goal", lane="quick")
        self.assertEqual(history.latest.artifacts[0].content, "return 2\n")
        self.assertEqual(history.visible.artifacts[0].content, "return 1\n")
        history.append(quick, session_id="session", goal="different task", lane="quick")
        self.assertEqual(history.latest.artifacts, [])

    def test_frozen_output_does_not_expire_when_recent_history_rolls_over(self):
        history = OutputHistory(limit=3)
        held = add(history, 1)
        history.freeze()
        for number in range(2, 15):
            add(history, number)
        self.assertEqual(history.visible.id, held.id)
        self.assertEqual(len(history.entries), 4)
        self.assertTrue(history.move(1))
        self.assertEqual(history.visible.response.summary, "Proposal 12")
        self.assertEqual(len(history.entries), 3)
        history.resume()
        self.assertEqual(history.visible.response.summary, "Proposal 14")

    def test_empty_and_duplicate_outputs_do_not_create_phantom_history(self):
        history = OutputHistory()
        self.assertFalse(history.move(-1))
        self.assertIsNone(history.visible)
        add(history, 1)
        add(history, 1)
        self.assertEqual(len(history.entries), 1)
        history.clear()
        self.assertFalse(history.frozen)
        self.assertIsNone(history.latest)

    def test_historical_conversation_keeps_its_original_source_without_image_access(self):
        context = ContextStore()
        source = context.add("speech", "What about a queue?", "system", "other_people", image_path="/private.png")
        response = proposal(1)
        response.conversation = [ConversationResponse(source_id=source.id, action="answer", text="Use a durable queue.", artifact_effect="Add queue")]
        history = OutputHistory()
        history.append(response, session_id=context.session_id, goal="goal", lane="deep", sources=context.observations)
        source.text = "Changed current transcript"
        context.observations.clear()
        self.assertEqual(history.visible.sources[0].text, "What about a queue?")
        self.assertEqual(history.visible.sources[0].speaker, "other_people")
        self.assertEqual(history.visible.sources[0].image_path, "")

    def test_saved_browsing_state_cannot_roll_back_the_latest_model_context(self):
        context = ContextStore()
        context.set_goal("Example task")
        history = OutputHistory()
        for number in (1, 2):
            response = proposal(number, context.observations[0].id)
            context.integrate(response)
            history.append(response, session_id=context.session_id, goal=context.goal, lane="deep", sources=context.observations)
        history.move(-1)
        coordinator = SimpleNamespace(current=response, history=[], pinned=False)
        document = checkpoint(context, coordinator, displayed_artifacts=history.visible.artifacts, output_history=history)
        with tempfile.TemporaryDirectory() as temp:
            path = save_session(document, Path(temp) / "session.json")
            loaded = load_session(path)
        restored = restore_context(loaded)
        self.assertTrue(loaded.pinned)
        self.assertEqual(loaded.selected_output_id, history.visible.id)
        self.assertEqual(loaded.displayed_artifacts[0].content, "return 1\n")
        self.assertEqual(json.loads(restored.previous_artifacts)[0]["content"], "return 2\n")
        self.assertEqual(restored.previous_summary, "Proposal 2")
        self.assertEqual(len(loaded.outputs), 2)


if __name__ == "__main__":
    unittest.main()
