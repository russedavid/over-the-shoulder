"""Reuse Codex CLI authentication and interpretation in an ephemeral read-only snapshot."""

import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

from otsc.models import decode_json, response_schema
from otsc.privacy import private_write, redact
from otsc.prompts import SYSTEM, build_prompt
from otsc.providers import latest_image
from otsc.telemetry import digest, record_progress
from otsc.workspace import materialize_snapshot, verified_from_snapshot


def codex_command(executable, directory, schema, output, choice, *, fast_mode=None):
    fast_mode = choice.fast_mode if fast_mode is None else fast_mode
    command = [
        executable,
        "-a",
        "never",
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--cd",
        str(directory),
        "--json",
        "--output-schema",
        str(schema),
        "--output-last-message",
        str(output),
        "--color",
        "never",
        "-c",
        'web_search="disabled"',
        "--strict-config",
    ]
    # The standard application supplies its complete input snapshot. Tool execution
    # is not needed for generation; optional inspection tools run through our bounded host API.
    for feature in (
        "shell_tool",
        "unified_exec",
        "code_mode",
        "js_repl",
        "view_image",
        "multi_agent",
        "multi_agent_v2",
        "apps",
        "enable_mcp_apps",
        "memory_tool",
        "browser_use",
        "image_generation",
        "tool_search",
        "request_permissions_tool",
        "shell_snapshot",
    ):
        command += ["--disable", feature]
    command += ["-c", "agents.enabled=false", "-c", "project_doc_max_bytes=0"]
    if choice.model:
        command += ["--model", choice.model]
    if choice.reasoning:
        command += ["-c", "model_reasoning_effort=" + json.dumps(choice.reasoning)]
    command += ["--enable" if fast_mode else "--disable", "fast_mode"]
    if fast_mode:
        command += ["-c", 'service_tier="fast"']
    return command


class CodexProvider:
    def __init__(self, choice, *, fast_mode=None):
        self.choice = choice.model_copy(deep=True)
        self.fast_mode = choice.fast_mode if fast_mode is None else fast_mode
        self.last_raw_response = None
        self.last_raw_text = ""

    def generate(self, snapshot, lane, token, progress):
        files = verified_from_snapshot(snapshot)
        raw = self.generate_json(
            snapshot,
            lane,
            token,
            progress,
            schema=response_schema(lane),
            system=SYSTEM,
            prompt=build_prompt(snapshot, lane, verified_files=files),
        )
        self.last_raw_response = raw
        from otsc.delivery import prepare_response

        return prepare_response(raw, snapshot, lane, token, progress, provider=self)

    def generate_json(self, snapshot, lane, token, progress, *, schema, system, prompt):
        executable = shutil.which("codex")
        if not executable:
            raise RuntimeError("Codex CLI was not found. Install it and sign in, or choose an API provider.")
        files = verified_from_snapshot(snapshot)
        with tempfile.TemporaryDirectory(prefix="otsc-codex-") as temp:
            directory = Path(temp)
            work = directory / "project"
            work.mkdir(mode=0o700)
            materialize_snapshot(work, files)
            schema_path, output = directory / "schema.json", directory / "response.json"
            private_write(schema_path, json.dumps(schema))
            command = codex_command(executable, work, schema_path, output, self.choice, fast_mode=self.fast_mode)
            picture = latest_image(snapshot, self.choice.send_images)
            if picture:
                import base64

                path = directory / "context.png"
                private_write(path, base64.b64decode(picture))
                command += ["--image", str(path)]
            command += ["-"]
            full_prompt = (
                system + "\n\nThe current directory is a filtered, read-only source snapshot. "
                "Do not edit files, run commands, read outside this directory, or access the network. "
                "The complete available context is below. Return the structured proposal only.\n\n" + prompt
            )
            env = {
                k: v
                for k, v in os.environ.items()
                if not any(marker in k.upper() for marker in ("API_KEY", "ACCESS_TOKEN", "SECRET", "PASSWORD"))
                and k not in {"CODEX_PERMISSION_PROFILE", "CODEX_SESSION_ID", "CODEX_THREAD_ID"}
            }
            record_progress(
                progress,
                "provider_request",
                provider="codex",
                model=self.choice.model,
                reasoning=self.choice.reasoning,
                requested_service_tier="fast" if self.fast_mode else "standard",
                prompt_hash=digest(full_prompt),
                schema_hash=digest(schema),
            )
            with (directory / "stderr.txt").open("w+") as errors:
                proc = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=errors,
                    text=True,
                    env=env,
                    start_new_session=True,
                )

                def stop():
                    if proc.poll() is None:
                        try:
                            os.killpg(proc.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass

                token.on_cancel(stop)
                timeout = threading.Timer(240, stop)
                timeout.daemon = True
                timeout.start()
                try:
                    token.check()
                    proc.stdin.write(full_prompt)
                    proc.stdin.close()
                    progress("Codex is interpreting this context…")
                    for line in proc.stdout:
                        token.check()
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        if event.get("type") == "turn.failed":
                            raise RuntimeError("Codex failed: " + redact(json.dumps(event.get("error", {})))[:350])
                        if event.get("type") == "turn.completed":
                            usage = event.get("usage", {})
                            record_progress(
                                progress,
                                "provider_usage",
                                **{
                                    ("cached_tokens" if k == "cached_input_tokens" else k): int(v)
                                    for k, v in usage.items()
                                    if k in {"input_tokens", "output_tokens", "cached_input_tokens"}
                                    and isinstance(v, int)
                                },
                            )
                        item = event.get("item", {})
                        if event.get("type") == "item.started" and item.get("type") in {
                            "command_execution",
                            "mcp_tool_call",
                            "web_search",
                            "file_change",
                        }:
                            stop()
                            raise RuntimeError(
                                "An unexpected Codex tool action was blocked; assistance uses only the supplied context"
                            )
                        if (
                            event.get("type") == "item.completed"
                            and event.get("item", {}).get("type") == "agent_message"
                        ):
                            progress("Codex is validating the proposed artifact…")
                    proc.wait(timeout=5)
                    token.check()
                    if proc.returncode or not output.exists():
                        errors.seek(0)
                        raise RuntimeError("Codex did not complete: " + redact(errors.read()[-700:]))
                    if output.stat().st_size > 200000:
                        raise RuntimeError("Codex response exceeded the app's size limit")
                    self.last_raw_text = output.read_text()
                    return decode_json(self.last_raw_text)
                finally:
                    timeout.cancel()
                    stop()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait(timeout=3)
                    if proc.stdout:
                        proc.stdout.close()
