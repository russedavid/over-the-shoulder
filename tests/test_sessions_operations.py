import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from otsc.context import ContextStore
from otsc.models import Artifact, Assistance, LineAnnotation, ObservedFile, normalize_optional_file_metadata
from otsc.scheduler import Cancellation, Coordinator, Job
from otsc.sessions import checkpoint, load_session, restore_context, save_session
from otsc.telemetry import TraceStore, digest


class SessionTests(unittest.TestCase):
    def test_only_corroborated_optional_file_metadata_can_be_omitted(self):
        c, k, a = self.scene()
        unknown = ObservedFile(
            path="", content="return 0", first_line=None, source_ids=[c.observations[1].id], confidence="high"
        )
        k.current.observed_files = [unknown]
        normalized = normalize_optional_file_metadata(k.current, c.observations, c.verified_files)
        self.assertEqual(normalized.observed_files, [])
        bad = unknown.model_copy(update={"path": "invented.py", "content": "unseen code"})
        normalized.observed_files = [bad]
        normalize_optional_file_metadata(normalized, c.observations, c.verified_files)
        with self.assertRaises(ValueError):
            normalized.validate_sources(c.observations, c.verified_files)

    def test_verified_file_metadata_is_redundant_but_spoken_inventions_still_fail(self):
        c, k, a = self.scene()
        item = ObservedFile(
            path="helper.py", content="return 0\n", first_line=1, source_ids=[c.observations[0].id], confidence="high"
        )
        k.current.observed_files = [item]
        self.assertEqual(
            normalize_optional_file_metadata(k.current, c.observations, c.verified_files).observed_files, []
        )
        k.current.observed_files = [item.model_copy(update={"content": "invented()"})]
        normalize_optional_file_metadata(k.current, c.observations, c.verified_files)
        with self.assertRaises(ValueError):
            k.current.validate_sources(c.observations, c.verified_files)

    def scene(self):
        context = ContextStore()
        context.set_goal("Finish the helper")
        context.set_repo("/explicit/project")
        context.set_verified_files("/explicit/project", {"helper.py": "return 0\n"})
        context.add("screen", "helper.py\nreturn 0", "screen", image_path="/private/capture.png")
        context.set_task_details(["Missing is distinct from zero"], ["Use None for missing data"])
        artifact = Artifact(
            id="helper",
            kind="code",
            title="Helper",
            content="return None\n",
            language="python",
            path="helper.py",
            basis="verified_file",
            source_ids=[context.observations[0].id],
            annotations=[LineAnnotation(line=1, explanation="Represent missing data")],
            nodes=[],
            edges=[],
        )
        response = Assistance(
            task=context.goal,
            summary="Use None",
            conversation=[],
            artifacts=[artifact],
            observed_files=[],
            open_questions=[],
        )
        coordinator = SimpleNamespace(current=response, history=[response], pinned=True)
        return context, coordinator, artifact

    def test_round_trip_preserves_work_but_not_capture_or_file_authority(self):
        context, coordinator, artifact = self.scene()
        document = checkpoint(context, coordinator, displayed_artifacts=[artifact], selected_id=artifact.id)
        with tempfile.TemporaryDirectory() as directory:
            path = save_session(document, Path(directory) / "session.json")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            restored = load_session(path)
        current = restore_context(restored)
        self.assertNotEqual(current.session_id, context.session_id)
        self.assertEqual(current.repo_root, "")
        self.assertEqual(current.verified_files, {})
        self.assertTrue(current.restored)
        self.assertTrue(all(not o.image_path for o in current.observations))
        self.assertEqual(restored.displayed_artifacts[0].clean_text(), artifact.clean_text())
        self.assertEqual(current.constraints, context.constraints)
        self.assertTrue(restored.pinned)
        self.assertEqual(
            restore_context(restored, authorized_project="/explicit/project").repo_root, "/explicit/project"
        )

    def test_import_cannot_introduce_settings_or_image_read_paths(self):
        c, k, a = self.scene()
        doc = checkpoint(c, k, displayed_artifacts=[a]).model_dump()
        doc["context"]["observations"][0]["image_path"] = "/etc/not-an-approved-image.png"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "edited.json"
            path.write_text(json.dumps(doc))
            self.assertEqual(load_session(path).context.observations[0].image_path, "")
            doc["settings"] = {"capture_screen": True, "project_folder": "/unauthorized"}
            path.write_text(json.dumps(doc))
            with self.assertRaises(ValueError):
                load_session(path)

    def test_saved_proposal_keeps_its_generation_base_after_source_changes(self):
        context, fake, artifact = self.scene()
        coordinator = Coordinator(context, lambda lane: None)
        try:
            coordinator.request_id = "original-request"
            job = Job(coordinator.request_id, context.snapshot(), "deep", Cancellation())
            self.assertTrue(coordinator.accept({"type": "result", "job": job, "response": fake.current}))
            context.set_verified_files("/explicit/project", {"helper.py": "return 42\n"})
            document = checkpoint(context, coordinator, displayed_artifacts=[artifact])
            expected = {"path": "helper.py", "base_hash": digest("return 0\n")}
            self.assertEqual(document.artifact_bases[digest(artifact.model_dump())], expected)
            with tempfile.TemporaryDirectory() as directory:
                path = save_session(document, Path(directory) / "session.json")
                loaded = load_session(path)
            self.assertEqual(loaded.artifact_bases[digest(artifact.model_dump())], expected)
            self.assertNotEqual(expected["base_hash"], digest(context.verified_files["helper.py"]))
        finally:
            coordinator.close()

    def test_model_response_cannot_change_user_confirmed_details(self):
        c, k, a = self.scene()
        c.integrate(k.current)
        self.assertEqual(c.constraints, ("Missing is distinct from zero",))
        self.assertEqual(c.decisions, ("Use None for missing data",))
        c.clear()
        self.assertEqual(c.constraints, ())
        self.assertEqual(c.decisions, ())


class TraceTests(unittest.TestCase):
    def test_metadata_never_serializes_supplied_content_credentials_or_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = TraceStore(Path(directory))
            trace.record(
                "generation_finished",
                request_id="request-1",
                lane="deep",
                outcome="success",
                elapsed_ms=100,
                transcript="PRIVATE CONVERSATION",
                content="PRIVATE CODE",
                api_key="PRIVATE KEY",
                model="api_key=PRIVATE-CREDENTIAL",
                reason={"text": "PRIVATE OBJECT"},
            )
            raw = (Path(directory) / "events.jsonl").read_text()
            self.assertNotIn("PRIVATE", raw)
            self.assertEqual(trace.summary()["successful"], 1)
            self.assertFalse(trace.summary()["content_logging"])

    def test_rotation_is_bounded_and_a_partial_line_does_not_break_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = TraceStore(Path(directory), max_bytes=600, max_files=3)
            for i in range(20):
                trace.record("generation_finished", request_id=str(i), lane="quick", outcome="success", elapsed_ms=i)
            self.assertLessEqual(len(list(Path(directory).glob("*.jsonl"))), 3)
            with (Path(directory) / "events.jsonl").open("a") as stream:
                stream.write('{"truncated":')
            self.assertGreater(trace.summary()["successful"], 0)

    def test_logging_failure_does_not_raise_into_the_product(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "not-a-directory"
            path.write_text("occupied")
            trace = TraceStore(path)
            trace.record("request_started", request_id="example")
            self.assertIsNotNone(trace.last_error)


if __name__ == "__main__":
    unittest.main()
