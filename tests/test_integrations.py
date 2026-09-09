import io
import json
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import httpx

from otsc.capture import Transcriber, wav_bytes
from otsc.codex import codex_command
from otsc.context import ContextStore
from otsc.models import Artifact, Assistance, LineAnnotation, ObservedFile
from otsc.providers import HTTPProvider, make_request
from otsc.scheduler import Cancellation
from otsc.settings import ModelChoice, Settings, load_settings, save_settings
from otsc.workspace import derive_patches, materialize_snapshot, read_project


class FakeCredentials:
    def get(self, choice, role):
        return "synthetic-test-credential"


def sample_response(snapshot):
    return Assistance(
        task=snapshot.goal,
        summary="Return None for missing data.",
        conversation=[],
        artifacts=[],
        observed_files=[],
        open_questions=[],
    )


class ProviderTests(unittest.TestCase):
    def test_all_http_providers_stream_into_the_same_validated_contract(self):
        context = ContextStore()
        context.set_goal("Handle missing data")
        snapshot = context.snapshot()
        payload = sample_response(snapshot).model_dump_json()
        for provider in ("openai", "gemini", "anthropic", "groq", "compatible"):
            with self.subTest(provider=provider):
                choice = ModelChoice(provider=provider, model="test-model", base_url="https://example.test/v1")
                seen = []

                def handle(request):
                    body = json.loads(request.content)
                    self.assertTrue(body.get("stream", provider == "gemini"))
                    self.assertNotIn("synthetic-test-credential", request.url.query.decode())
                    chunks = []
                    for text in (payload[: len(payload) // 2], payload[len(payload) // 2 :]):
                        event = {
                            "openai": {"type": "response.output_text.delta", "delta": text},
                            "gemini": {"candidates": [{"content": {"parts": [{"text": text}]}}]},
                            "anthropic": {
                                "type": "content_block_delta",
                                "delta": {"type": "input_json_delta", "partial_json": text},
                            },
                            "groq": {"choices": [{"delta": {"content": text}}]},
                            "compatible": {"choices": [{"delta": {"content": text}}]},
                        }[provider]
                        chunks.append("data: " + json.dumps(event) + "\n\n")
                    return httpx.Response(200, text="".join(chunks), headers={"content-type": "text/event-stream"})

                result = HTTPProvider(choice, FakeCredentials(), transport=httpx.MockTransport(handle)).generate(
                    snapshot, "quick", Cancellation(), seen.append
                )
                self.assertEqual(result.summary, "Return None for missing data.")
                self.assertTrue(any("Draft:" in item for item in seen))

    def test_request_images_and_role_configuration_are_not_silently_ignored(self):
        for provider in ("openai", "gemini", "anthropic", "groq", "compatible"):
            with self.subTest(provider=provider):
                choice = ModelChoice(provider=provider, model="test-model", base_url="https://example.test/v1")
                url, headers, body = make_request(choice, "context", "fake-key", "base64-image")
                self.assertIn("base64-image", json.dumps(body))
                self.assertNotIn("fake-key", url)
                self.assertNotIn("fake-key", json.dumps(body))

    def test_error_response_redacts_credentials_and_does_not_retry(self):
        calls = []

        def handle(request):
            calls.append(request)
            return httpx.Response(429, json={"error": "api_key=synthetic-private-value quota exceeded"})

        context = ContextStore()
        context.set_goal("Help")
        provider = HTTPProvider(
            ModelChoice(provider="groq", model="test"), FakeCredentials(), transport=httpx.MockTransport(handle)
        )
        with self.assertRaises(RuntimeError) as error:
            provider.generate(context.snapshot(), "quick", Cancellation(), lambda text: None)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("synthetic-private-value", str(error.exception))
        self.assertIn("429", str(error.exception))

    def test_invalid_model_output_cannot_bypass_annotation_validation(self):
        context = ContextStore()
        context.set_goal("Write code")
        payload = sample_response(context.snapshot()).model_dump()
        payload["artifacts"] = [
            dict(
                id="a",
                kind="code",
                title="Bad",
                content="return None",
                language="python",
                path="",
                basis="example",
                source_ids=[context.observations[0].id],
                annotations=[],
                nodes=[],
                edges=[],
            )
        ]

        def handle(request):
            event = {"choices": [{"delta": {"content": json.dumps(payload)}}]}
            return httpx.Response(200, text="data: " + json.dumps(event) + "\n\n")

        provider = HTTPProvider(
            ModelChoice(provider="groq", model="test"), FakeCredentials(), transport=httpx.MockTransport(handle)
        )
        with self.assertRaises(ValueError):
            provider.generate(context.snapshot(), "quick", Cancellation(), lambda text: None)

    def test_codex_uses_existing_cli_in_read_only_snapshot_without_approval_bypass(self):
        command = codex_command(
            "codex", Path("/tmp/project"), Path("/tmp/schema"), Path("/tmp/result"), ModelChoice(provider="codex")
        )
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ephemeral", command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)


class WorkspaceTests(unittest.TestCase):
    def test_observed_excerpt_diff_keeps_its_partial_basis_and_line_positions(self):
        from otsc.demo import DemoProvider, seed_demo

        c = ContextStore()
        seed_demo(c)
        # Replay only; an existing observed first-line offset must be preserved in the host diff.
        r = DemoProvider().generate(c.snapshot(), "quick", Cancellation(), lambda text: None)
        r.observed_files[0].first_line = 20
        r.artifacts = [
            Artifact(
                id="edit",
                kind="code",
                title="Edit",
                content="def mean(values):\n    return None\n",
                language="python",
                path="stats.py",
                basis="observed_fragment",
                source_ids=[o.id for o in c.observations],
                annotations=[
                    LineAnnotation(line=1, explanation="Function"),
                    LineAnnotation(line=2, explanation="Missing data"),
                ],
                nodes=[],
                edges=[],
            )
        ]
        result = derive_patches(r, {})
        self.assertEqual(result.artifacts[0].kind, "code")
        diff = result.artifacts[1]
        self.assertEqual(diff.basis, "observed_fragment")
        self.assertIn("@@ -20,2 +20,2 @@", diff.content)
        self.assertEqual(diff.annotations[0].line, 21)

    def test_project_read_excludes_keys_ignored_files_binary_content_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".gitignore").write_text("ignored.py\n")
            (root / "good.py").write_text("def answer():\n    return 42\n")
            (root / ".env").write_text("DO_NOT_COPY=synthetic\n")
            (root / "secret.py").write_text('api_key="synthetic-secret"\n')
            (root / "binary.py").write_bytes(b"a\0b")
            (root / "ignored.py").write_text("private = True")
            (Path(outside) / "external.py").write_text("do_not_copy = True")
            (root / "linked.py").symlink_to(Path(outside) / "external.py")
            (root / "folder").symlink_to(outside, target_is_directory=True)
            files, _ = read_project(str(root))
            self.assertEqual(list(files), ["good.py"])
            with tempfile.TemporaryDirectory() as scratch:
                materialize_snapshot(Path(scratch), files)
                self.assertEqual((Path(scratch) / "good.py").read_text(), files["good.py"])
                with self.assertRaises(ValueError):
                    materialize_snapshot(Path(scratch), {"../escape.py": "bad"})

    def test_host_diff_applies_to_the_exact_snapshot_with_no_source_mutation(self):
        for before, after in [
            ("def answer():\n    return 1\n", "def answer():\n    return 2\n"),
            ("value = 1", "value = 2"),
            ("first = 1\nsecond = 2\n", "first = 1\n"),
        ]:
            with self.subTest(before=before):
                artifact = Artifact(
                    id="change",
                    kind="code",
                    title="Change",
                    content=after,
                    language="python",
                    path="a.py",
                    basis="verified_file",
                    source_ids=["source"],
                    nodes=[],
                    edges=[],
                    annotations=[
                        LineAnnotation(line=i, explanation="Explain this line")
                        for i in range(1, len(after.splitlines()) + 1)
                    ],
                )
                result = Assistance(
                    task="Change",
                    summary="Change",
                    conversation=[],
                    artifacts=[artifact],
                    observed_files=[],
                    open_questions=[],
                )
                patch_text = derive_patches(result, {"a.py": before}).artifacts[0].content
                with tempfile.TemporaryDirectory() as temp:
                    path = Path(temp) / "a.py"
                    path.write_text(before)
                    subprocess.run(
                        ["git", "apply", "--check", "-"],
                        input=patch_text,
                        text=True,
                        cwd=temp,
                        capture_output=True,
                        check=True,
                    )
                    self.assertEqual(path.read_text(), before)
                    subprocess.run(
                        ["git", "apply", "-"], input=patch_text, text=True, cwd=temp, capture_output=True, check=True
                    )
                    self.assertEqual(path.read_text(), after)

    def test_spoken_or_invented_code_cannot_be_promoted_to_observed_file(self):
        c = ContextStore()
        s = c.add("speech", "There is a helper called calculate", "system", "other_people")
        r = sample_response(c.snapshot())
        r.observed_files = [
            ObservedFile(path="file.py", content="return 42", first_line=None, source_ids=[s.id], confidence="high")
        ]
        with self.assertRaises(ValueError):
            r.validate_sources(c.observations)
        screen = c.add("screen", "file.py\nreturn 2", "screen")
        r.observed_files[0].source_ids = [screen.id]
        with self.assertRaises(ValueError):
            r.validate_sources(c.observations)

    def test_snapshot_files_do_not_change_under_an_in_flight_request(self):
        c = ContextStore()
        c.set_goal("Edit")
        c.set_repo("/selected")
        c.set_verified_files("/selected", {"a.py": "one"})
        snapshot = c.snapshot()
        c.set_verified_files("/selected", {"a.py": "two"})
        self.assertEqual(json.loads(snapshot.verified_files), {"a.py": "one"})
        self.assertNotEqual(snapshot.revision, c.revision)


class SettingsAudioTests(unittest.TestCase):
    def test_settings_are_private_and_contain_no_api_key_field(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = Settings(configured=True)
            save_settings(settings, root)
            self.assertEqual(load_settings(root), settings)
            self.assertEqual((root / "settings.json").stat().st_mode & 0o777, 0o600)
            self.assertNotIn("api_key", (root / "settings.json").read_text())
        with self.assertRaises(ValueError):
            ModelChoice(provider="compatible", base_url="http://remote.test/v1")
        with self.assertRaises(ValueError):
            ModelChoice(provider="compatible", base_url="https://user:secret@example.test/v1")

    def test_audio_payload_is_mono_16khz_and_transcription_uses_its_own_role(self):
        import numpy as np

        data = wav_bytes(np.array([-1.0, 0.0, 1.0], dtype=np.float32))
        with wave.open(io.BytesIO(data), "rb") as audio:
            self.assertEqual(
                (audio.getnchannels(), audio.getframerate(), audio.getsampwidth(), audio.getnframes()), (1, 16000, 2, 3)
            )
        seen = []

        class Credentials:
            def get(self, choice, role):
                seen.append(role)
                return "synthetic"

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def post(self, url, **kwargs):
                self_url = url
                self.assertion = kwargs
                if not self_url.endswith("/audio/transcriptions"):
                    raise AssertionError(self_url)
                return httpx.Response(200, json={"text": "Could we use a queue?"})

        with patch("httpx.Client", return_value=Client()):
            result = Transcriber(Settings(transcription="groq"), Credentials()).transcribe(np.zeros(16000))
        self.assertEqual(result, "Could we use a queue?")
        self.assertEqual(seen, ["transcription"])


if __name__ == "__main__":
    unittest.main()
