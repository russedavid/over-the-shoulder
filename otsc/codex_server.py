"""Persistent local stdio Codex transports with ephemeral, tool-free turns.

One process per worker role keeps OCR/context/answers independent. No listener,
credential copy, API key, user plugin, or conversation-history reuse is involved.
"""

import atexit
import json
import os
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from uuid import uuid4

from otsc.models import decode_json
from otsc.privacy import redact
from otsc.streaming import string_preview
from otsc.telemetry import digest, record_progress

DISABLED_FEATURES = (
    "hooks", "plugins", "remote_plugin", "shell_tool", "unified_exec", "code_mode", "js_repl", "view_image",
    "multi_agent", "multi_agent_v2", "apps", "enable_mcp_apps", "memory_tool", "memories", "browser_use",
    "image_generation", "tool_search", "request_permissions_tool", "shell_snapshot",
)


def server_command(executable):
    command = [executable, "app-server", "--listen", "stdio://", "--strict-config"]
    for feature in DISABLED_FEATURES:
        command += ["--disable", feature]
    # Root-table overrides replace user integrations, including obsolete MCP
    # definitions that the current app-server cannot parse even when disabled.
    for value in ('mcp_servers={}', 'plugins={}', 'model_providers={}', 'model_provider="openai"',
                  'web_search="disabled"', 'agents.enabled=false', 'project_doc_max_bytes=0',
                  'history.persistence="none"', 'notify=[]', 'developer_instructions=""'):
        command += ["-c", value]
    return command


def child_environment():
    return {k: v for k, v in os.environ.items()
            if not any(marker in k.upper() for marker in ("API_KEY", "ACCESS_TOKEN", "SECRET", "PASSWORD"))
            and k not in {"CODEX_PERMISSION_PROFILE", "CODEX_SESSION_ID", "CODEX_THREAD_ID"}}


class CodexServer:
    def __init__(self):
        self.lock = threading.Lock()
        self.state_lock = threading.RLock()
        self.proc = None
        self.directory = None
        self.stderr = None
        self.messages = queue.Queue()
        self.identity = None
        self.serial = 0

    def close(self, identity=None):
        with self.state_lock:
            if identity is not None and self.identity != identity:
                return
            proc, self.proc = self.proc, None
            self.identity = None
            if proc:
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait(timeout=2)
                    except ProcessLookupError:
                        pass
                for stream in (proc.stdin, proc.stdout):
                    if stream:
                        stream.close()
            if self.stderr:
                self.stderr.close()
                self.stderr = None
            if self.directory:
                self.directory.cleanup()
                self.directory = None

    def _send(self, message):
        with self.state_lock:
            if self.proc is None or self.proc.poll() is not None:
                raise RuntimeError("Codex streaming process is not running")
            self.proc.stdin.write(json.dumps(message) + "\n")
            self.proc.stdin.flush()

    def _next(self, token, deadline):
        while True:
            token.check()
            if time.monotonic() >= deadline:
                raise TimeoutError("Codex streaming request exceeded its deadline")
            try:
                item = self.messages.get(timeout=.1)
            except queue.Empty:
                continue
            if isinstance(item, Exception):
                raise item
            if "method" in item and "id" in item:
                raise RuntimeError("Unexpected Codex host/tool request was blocked")
            return item

    def _rpc(self, method, params, token, deadline, deferred):
        self.serial += 1
        identity = self.serial
        self._send({"id": identity, "method": method, "params": params})
        while True:
            message = self._next(token, deadline)
            if message.get("id") == identity:
                if "error" in message:
                    raise RuntimeError("Codex streaming: " + redact(str(message["error"].get("message", "request failed")))[:600])
                return message.get("result", {})
            if "method" in message:
                deferred.append(message)

    def _start(self, token, deadline):
        if self.proc and self.proc.poll() is None:
            return
        self.close()
        executable = shutil.which("codex")
        if not executable:
            raise RuntimeError("Codex CLI was not found. Sign in with Codex, or select an API provider.")
        self.directory = tempfile.TemporaryDirectory(prefix="otsc-codex-stream-")
        self.stderr = tempfile.TemporaryFile(mode="w+")
        self.messages = queue.Queue()
        self.proc = subprocess.Popen(server_command(executable), cwd=self.directory.name, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=self.stderr, text=True,
                                     env=child_environment(), start_new_session=True)
        proc, messages = self.proc, self.messages

        def read():
            try:
                for line in proc.stdout:
                    if len(line) > 2_000_000:
                        raise RuntimeError("Codex streaming event exceeded the size limit")
                    messages.put(json.loads(line))
            except (OSError, ValueError) as error:
                messages.put(RuntimeError("Codex streaming connection failed: " + type(error).__name__))
            finally:
                messages.put(RuntimeError("Codex streaming process disconnected"))

        threading.Thread(target=read, daemon=True, name="otsc-codex-events").start()
        self._rpc("initialize", {"clientInfo": {"name": "otsc", "version": "0.2.0"},
                                 "capabilities": {"experimentalApi": True}}, token, deadline, [])
        self._send({"method": "initialized"})

    def generate(self, choice, lane, token, progress, *, schema, system, prompt, picture=None):
        while not self.lock.acquire(timeout=.1):
            token.check()
        identity, thread = uuid4().hex, None
        try:
            token.check()
            deadline = time.monotonic() + 240
            self._start(token, deadline)
            self.identity = identity
            token.on_cancel(lambda: self.close(identity))
            config = {"features.fast_mode": choice.fast_mode}
            if choice.reasoning:
                config["model_reasoning_effort"] = choice.reasoning
            deferred = []
            result = self._rpc("thread/start", {
                "cwd": self.directory.name, "model": choice.model, "modelProvider": "openai",
                "approvalPolicy": "never", "sandbox": "read-only", "ephemeral": True,
                "baseInstructions": system,
                "developerInstructions": "Use only the supplied immutable evidence. Tools, host actions, file edits and network access are unavailable. Return the structured response only.",
                "config": config, "serviceTier": "fast" if choice.fast_mode else "default",
            }, token, deadline, deferred)
            if result.get("approvalPolicy") != "never" or result.get("sandbox", {}).get("type") != "readOnly":
                raise RuntimeError("Codex did not honor the read-only assistance boundary")
            if result.get("modelProvider") != "openai" or not result.get("thread", {}).get("ephemeral"):
                raise RuntimeError("Codex did not create an ephemeral OpenAI session")
            thread = result["thread"]["id"]
            inputs = [{"type": "text", "text": prompt}]
            if picture:
                inputs.append({"type": "image", "url": "data:image/png;base64," + picture})
            record_progress(progress, "provider_request", provider="codex", model=choice.model,
                            reasoning=choice.reasoning, requested_service_tier="fast" if choice.fast_mode else "standard",
                            transport="app-server", prompt_hash=digest(system + prompt), schema_hash=digest(schema))
            turn = self._rpc("turn/start", {"threadId": thread, "input": inputs, "outputSchema": schema,
                                           "approvalPolicy": "never", "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
                                           "model": choice.model, "effort": choice.reasoning or None,
                                           "serviceTierForTurn": "fast" if choice.fast_mode else "default"},
                             token, deadline, deferred)["turn"]["id"]
            text, preview, usage = "", "", {}
            parser = getattr(progress, "preview_parser", string_preview)
            while True:
                event = deferred.pop(0) if deferred else self._next(token, deadline)
                params = event.get("params", {})
                if params.get("threadId") != thread or params.get("turnId", turn) != turn:
                    continue
                method = event.get("method")
                if method in {"item/started", "item/completed"}:
                    item = params.get("item", {})
                    if item.get("type") not in {"userMessage", "agentMessage", "reasoning", "plan", "contextCompaction"}:
                        raise RuntimeError("Unexpected Codex tool action was blocked")
                    if method == "item/completed" and item.get("type") == "agentMessage":
                        text = item.get("text", text)
                elif method == "item/agentMessage/delta":
                    text += params.get("delta", "")
                elif method == "thread/tokenUsage/updated":
                    usage = params.get("tokenUsage", {}).get("last", {})
                elif method == "turn/completed":
                    ended = params["turn"]
                    if ended.get("status") != "completed":
                        raise RuntimeError("Codex turn did not complete: " + redact(str(ended.get("error") or ended.get("status")))[:500])
                    break
                if len(text) > 200000:
                    raise RuntimeError("Codex response exceeded the app's size limit")
                current = parser(text)
                if current and current != preview:
                    preview = current
                    progress("Draft: " + preview)
            token.check()
            if usage:
                record_progress(progress, "provider_usage", **{target: usage[key] for key, target in
                                (("inputTokens", "input_tokens"), ("outputTokens", "output_tokens"), ("cachedInputTokens", "cached_tokens"))
                                if isinstance(usage.get(key), int)})
            result = decode_json(text)
            self._rpc("thread/unsubscribe", {"threadId": thread}, token, deadline, [])
            self.identity = None
            return result, text
        except Exception:
            self.close()
            raise
        finally:
            self.identity = None
            self.lock.release()


_servers = {}
_servers_lock = threading.Lock()


def server_for(lane):
    with _servers_lock:
        # Fixed worker roles bound resource use; no arbitrary model-keyed pool.
        key = lane if lane in {"quick", "deep", "ocr", "context_builder", "planner"} else "deep"
        if key not in _servers:
            _servers[key] = CodexServer()
        return _servers[key]


def close_servers():
    with _servers_lock:
        for server in _servers.values():
            server.close()
        _servers.clear()


atexit.register(close_servers)
