import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from otsc.codex import CodexProvider
from otsc.codex_server import CodexServer, child_environment, server_command
from otsc.context import ContextStore
from otsc.scheduler import Cancellation, Cancelled
from otsc.settings import Settings


class FakeServer(CodexServer):
    """The real RPC/event consumer against a deterministic local peer."""

    def __init__(self, *, tool=False, disconnect=False, hold=False):
        super().__init__()
        self.sent = []
        self.tool, self.disconnect, self.hold = tool, disconnect, hold
        self.closes = 0
        self.starts = 0

    def _start(self, token, deadline):
        if self.proc is None:
            self.starts += 1
            self.proc = object()
            self.directory = SimpleNamespace(name="/tmp/otsc-empty-snapshot")

    def close(self, identity=None):
        if identity is None or identity == self.identity:
            self.closes += 1
            self.proc = None
            self.identity = None

    def _send(self, message):
        self.sent.append(message)
        method = message["method"]
        if method == "thread/start":
            result = {
                "approvalPolicy": "never",
                "sandbox": {"type": "readOnly"},
                "modelProvider": "openai",
                "thread": {"id": "thread-" + str(message["id"]), "ephemeral": True},
            }
            self.thread = result["thread"]["id"]
        elif method == "turn/start":
            result = {"turn": {"id": "turn"}}
        else:
            result = {}
        self.messages.put({"id": message["id"], "result": result})
        if method == "turn/start":

            def event(method, **fields):
                self.messages.put({"method": method, "params": {"threadId": self.thread, "turnId": "turn", **fields}})

            if self.disconnect:
                self.messages.put(RuntimeError("Disconnected"))
            elif self.tool:
                event("item/started", item={"type": "commandExecution", "command": "blocked"})
            elif not self.hold:
                for delta in ('{"summary":"Keep ', 'zero distinct."}'):
                    event("item/agentMessage/delta", delta=delta)
                event(
                    "thread/tokenUsage/updated",
                    tokenUsage={"last": {"inputTokens": 10, "outputTokens": 5, "cachedInputTokens": 0}},
                )
                event("turn/completed", turn={"id": "turn", "status": "completed"})


class CodexStreamingTests(unittest.TestCase):
    def test_startup_clears_user_integrations_and_never_inherits_api_credentials(self):
        cmd = server_command("codex")
        for item in (
            "stdio://",
            "mcp_servers={}",
            "plugins={}",
            "model_providers={}",
            "project_doc_max_bytes=0",
            "notify=[]",
        ):
            self.assertIn(item, cmd)
        for feature in ("shell_tool", "unified_exec", "hooks", "plugins", "multi_agent", "apps"):
            self.assertEqual(cmd[cmd.index(feature) - 1], "--disable")
        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "do-not-inherit", "CODEX_PERMISSION_PROFILE": "do-not-inherit", "PATH": "/bin"},
        ):
            env = child_environment()
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("CODEX_PERMISSION_PROFILE", env)
            self.assertEqual(env["PATH"], "/bin")

    def test_streams_before_completion_and_reuses_process_not_conversation_history(self):
        server = FakeServer()
        seen = []
        for _ in range(2):
            value, text = server.generate(
                Settings().quick,
                "quick",
                Cancellation(),
                seen.append,
                schema={},
                system="Use supplied evidence",
                prompt="Synthetic question",
            )
            self.assertEqual(value, {"summary": "Keep zero distinct."})
            self.assertEqual(json.loads(text), value)
        self.assertIn("Draft: Keep ", seen)
        self.assertEqual(server.starts, 1)
        threads = [r["params"] for r in server.sent if r["method"] == "thread/start"]
        self.assertEqual(len(threads), 2)
        self.assertTrue(
            all(r["ephemeral"] and r["sandbox"] == "read-only" and r["approvalPolicy"] == "never" for r in threads)
        )
        self.assertEqual(sum(r["method"] == "thread/unsubscribe" for r in server.sent), 2)
        turns = [r["params"] for r in server.sent if r["method"] == "turn/start"]
        self.assertNotEqual(turns[0]["threadId"], turns[1]["threadId"])
        self.assertTrue(all(r["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False} for r in turns))

    def test_tool_requests_and_disconnects_fail_without_fallback(self):
        for server in (FakeServer(tool=True), FakeServer(disconnect=True)):
            with self.subTest(tool=server.tool):
                with self.assertRaises(RuntimeError):
                    server.generate(
                        Settings().quick,
                        "quick",
                        Cancellation(),
                        lambda _: None,
                        schema={},
                        system="No tools",
                        prompt="Synthetic",
                    )
                self.assertIsNone(server.proc)
                self.assertEqual(server.starts, 1)

    def test_cancellation_closes_only_its_active_process(self):
        server = FakeServer(hold=True)
        token = Cancellation()
        timer = threading.Timer(0.05, token.cancel)
        timer.start()
        try:
            with self.assertRaises(Cancelled):
                server.generate(
                    Settings().quick, "quick", token, lambda _: None, schema={}, system="No tools", prompt="Synthetic"
                )
            self.assertIsNone(server.proc)
        finally:
            timer.cancel()

    def test_provider_honors_processing_override_and_keeps_image_input_in_memory(self):
        server = FakeServer()
        c = ContextStore()
        c.set_goal("Synthetic")
        provider = CodexProvider(Settings().quick, fast_mode=False)
        with patch("otsc.codex_server.server_for", return_value=server):
            provider.generate_json(
                c.snapshot(), "quick", Cancellation(), lambda _: None, schema={}, system="System", prompt="Prompt"
            )
        turn = next(r["params"] for r in server.sent if r["method"] == "turn/start")
        self.assertEqual(turn["serviceTierForTurn"], "default")
