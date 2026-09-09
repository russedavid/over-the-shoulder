import json
import unittest

from otsc.context import ContextStore
from otsc.inspection import InspectionDecision, InspectionProvider, SnapshotTools
from otsc.models import Assistance
from otsc.scheduler import Cancellation
from otsc.settings import ModelChoice


def decision(action, path="", query="", artifact_id=""):
    return InspectionDecision(action=action, path=path, query=query, artifact_id=artifact_id, reason="Test decision")


class InspectionTests(unittest.TestCase):
    def context(self):
        c = ContextStore()
        c.set_goal("Explain the retry decision")
        c.set_repo("/selected")
        c.set_verified_files("/selected", {"retry.py": "return status == 429\n", "other.py": "unrelated = True\n"})
        return c

    def test_tools_cannot_read_outside_the_supplied_snapshot(self):
        tools = SnapshotTools(self.context().snapshot())
        for path in ("../secrets.py", "/etc/passwd", "missing.py"):
            self.assertFalse(tools.execute(decision("read_file", path))["ok"])
        first = tools.execute(decision("read_file", "retry.py"))
        self.assertTrue(first["ok"])
        size = tools.used_chars
        self.assertTrue(tools.execute(decision("read_file", "retry.py"))["cached"])
        self.assertEqual(tools.used_chars, size)
        self.assertEqual(list(tools.read_files), ["retry.py"])

    def test_tool_budget_does_not_return_a_truncated_verified_file(self):
        tools = SnapshotTools(self.context().snapshot(), max_chars=2)
        result = tools.execute(decision("read_file", "retry.py"))
        self.assertFalse(result["ok"])
        self.assertEqual(tools.read_files, {})

    def test_model_choices_drive_bounded_inspection_before_the_original_response(self):
        calls = []

        class Provider:
            choice = ModelChoice(provider="codex")

            def generate_json(self, snapshot, lane, token, progress, **kwargs):
                content = json.loads(kwargs["prompt"])
                calls.append(content)
                return (
                    decision("read_file", "retry.py").model_dump()
                    if len(calls) == 1
                    else decision("finish").model_dump()
                )

            def generate(self, snapshot, lane, token, progress):
                self.final = snapshot
                return Assistance(
                    task=snapshot.goal,
                    summary="Use the selected file",
                    conversation=[],
                    artifacts=[],
                    observed_files=[],
                    open_questions=[],
                )

        base = Provider()
        agent = InspectionProvider(base)
        result = agent.generate(self.context().snapshot(), "deep", Cancellation(), lambda text: None)
        self.assertEqual(result.summary, "Use the selected file")
        self.assertEqual(list(json.loads(base.final.verified_files)), ["retry.py"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["previous_tool_results"][0]["result"]["content"], "return status == 429\n")

    def test_inspection_deadline_preserves_the_standard_response_path(self):
        class Provider:
            choice = ModelChoice(provider="codex")

            def generate_json(self, *args, **kwargs):
                raise AssertionError("Expired inspection must not call a model")

            def generate(self, snapshot, *args):
                return snapshot

        snapshot = self.context().snapshot()
        result = InspectionProvider(Provider(), deadline_seconds=0).generate(
            snapshot, "deep", Cancellation(), lambda text: None
        )
        self.assertEqual(json.loads(result.verified_files), json.loads(snapshot.verified_files))


if __name__ == "__main__":
    unittest.main()
