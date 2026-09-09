"""Reuse Codex CLI authentication and interpretation in an ephemeral read-only snapshot."""

import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

from otsc.models import parse_response, response_schema
from otsc.privacy import private_write, redact
from otsc.prompts import SYSTEM, build_prompt
from otsc.providers import latest_image
from otsc.workspace import derive_patches, materialize_snapshot, verified_from_snapshot


def codex_command(executable, directory, schema, output, choice):
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
    ]
    if choice.model:
        command += ["--model", choice.model]
    if choice.reasoning:
        command += ["-c", "model_reasoning_effort=" + json.dumps(choice.reasoning)]
    return command


class CodexProvider:
    def __init__(self, choice):
        self.choice = choice.model_copy(deep=True)

    def generate(self, snapshot, lane, token, progress):
        executable = shutil.which("codex")
        if not executable:
            raise RuntimeError("Codex CLI was not found. Install it and sign in, or choose an API provider.")
        files = verified_from_snapshot(snapshot)
        with tempfile.TemporaryDirectory(prefix="otsc-codex-") as temp:
            directory = Path(temp)
            work = directory / "project"
            work.mkdir(mode=0o700)
            materialize_snapshot(work, files)
            schema, output = directory / "schema.json", directory / "response.json"
            private_write(schema, json.dumps(response_schema()))
            command = codex_command(executable, work, schema, output, self.choice)
            picture = latest_image(snapshot, self.choice.send_images)
            if picture:
                import base64

                path = directory / "context.png"
                private_write(path, base64.b64decode(picture))
                command += ["--image", str(path)]
            command += ["-"]
            prompt = (
                SYSTEM + "\n\nThe current directory is a filtered, read-only source snapshot. "
                "Do not edit files, run commands, read outside this directory, or access the network. "
                "The complete available context is below. Return the structured proposal only.\n\n"
                + build_prompt(snapshot, lane, verified_files=files)
            )
            env = {
                k: v
                for k, v in os.environ.items()
                if not any(marker in k.upper() for marker in ("API_KEY", "ACCESS_TOKEN", "SECRET", "PASSWORD"))
            }
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
                    proc.stdin.write(prompt)
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
                    result = parse_response(output.read_text()).validate_sources(snapshot.observations, files)
                    return derive_patches(result, files)
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
