import base64
import io
import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from PIL import Image

from otsc.images import (
    ImageCoordinator,
    ImageJob,
    ImageProvider,
    image_key,
    image_path,
    resolve_image,
    store_png,
    update_image_memory,
)
from otsc.models import Artifact, Assistance
from otsc.output_history import OutputHistory
from otsc.scheduler import Cancellation
from otsc.settings import ModelChoice


def png():
    output = io.BytesIO()
    Image.new("RGB", (80, 60), "wheat").save(output, "PNG")
    return output.getvalue()


def draft(prompt="Draw the delivery architecture."):
    return Artifact(id="architecture", kind="image", title="System architecture", language="", path="",
                    content=json.dumps(dict(status="pending", action="generate", prompt=prompt, caption="A proposed design")),
                    basis="discussion", source_ids=["source"], annotations=[], nodes=[], edges=[])


def response(artifact):
    return Assistance(task="Design webhooks", summary="A proposed design", artifacts=[artifact], conversation=[],
                      observed_files=[], open_questions=[])


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch("otsc.images.app_directory", return_value=self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.temp.cleanup)
        self.choice = ModelChoice(provider="openai", model="gpt-image-2")

    def test_actual_png_validation_and_path_integrity(self):
        asset = store_png(png())
        path = image_path(asset)
        self.assertEqual(path.read_bytes(), png())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        path.write_bytes(b"modified")
        with self.assertRaises(ValueError):
            image_path(asset)
        for data in ({"asset_id": "../../outside", "sha256": "a" * 64}, [], {}):
            with self.assertRaises(ValueError):
                image_path(data)
        with self.assertRaises(Exception):
            store_png(b"<svg>not an image</svg>")

    def test_provider_generates_and_edits_with_a_trusted_reference(self):
        calls = []

        def server(request):
            calls.append((request.url.path, request.headers.get("content-type"), request.read()))
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]})

        provider = ImageProvider(self.choice, Mock(get=lambda *args: "synthetic-test-key"), transport=httpx.MockTransport(server))
        asset = provider.generate({"prompt": "Queue to worker"}, Cancellation(), lambda text: None)
        self.assertTrue(image_path(asset).exists())
        provider.generate({"prompt": "Add delayed retries", "reference": asset}, Cancellation(), lambda text: None)
        self.assertEqual([item[0] for item in calls], ["/v1/images/generations", "/v1/images/edits"])
        self.assertIn("multipart/form-data", calls[1][1])
        self.assertIn(b"previous-design.png", calls[1][2])

    def test_missing_image_or_provider_error_is_explicit(self):
        for payload, status in [({"data": []}, 200), ({"error": {"message": "quota exhausted"}}, 429)]:
            provider = ImageProvider(self.choice, Mock(get=lambda *args: "synthetic"),
                                     transport=httpx.MockTransport(lambda request: httpx.Response(status, json=payload)))
            with self.assertRaises((ValueError, RuntimeError)):
                provider.generate({"prompt": "A system design"}, Cancellation(), lambda text: None)

    def test_image_publication_preserves_pinned_history_and_rejects_stale_briefs(self):
        artifact = draft()
        request = json.loads(artifact.content)
        job = ImageJob("session", artifact.id, image_key(request, self.choice), request, self.choice, Cancellation())
        history = OutputHistory()
        history.append(response(artifact), session_id="session", goal="Design", lane="deep")
        history.freeze()
        held = history.visible
        asset = store_png(png())
        artifacts, changed = resolve_image(history.latest.artifacts, job, asset)
        self.assertTrue(changed)
        history.append(response(artifacts[0]), session_id="session", goal="Design", lane="image")
        self.assertIs(history.visible, held)
        self.assertEqual(json.loads(held.artifacts[0].content)["status"], "pending")
        history.resume()
        self.assertEqual(json.loads(history.visible.artifacts[0].content)["status"], "ready")
        stale, changed = resolve_image([draft("A different design")], job, asset)
        self.assertFalse(changed)
        self.assertEqual(json.loads(stale[0].content)["status"], "pending")

    def test_worker_coalesces_identical_briefs_and_reuses_completed_images(self):
        events = queue.Queue()
        started, release = threading.Event(), threading.Event()
        calls = []

        class Provider:
            def __init__(self, *args):
                pass

            def generate(self, request, token, progress):
                calls.append(request)
                started.set()
                release.wait(2)
                token.check()
                return {"asset_id": "a" * 32, "sha256": "b" * 64}

        worker = ImageCoordinator(events, lambda: self.choice, Mock(), provider_factory=Provider)
        self.addCleanup(worker.close)
        try:
            answer = response(draft())
            worker.submit(answer, "session", 1)
            self.assertTrue(started.wait(1))
            worker.submit(answer, "session", 2)
            release.set()
            event = events.get(timeout=2)
            self.assertIn("asset", event)
            self.assertEqual(event["job"].task_revision, 2)
            worker.submit(answer, "session", 2)
            self.assertEqual(events.get(timeout=2)["seconds"], 0)
            self.assertEqual(len(calls), 1)
        finally:
            release.set()

    def test_old_task_completion_cannot_publish(self):
        events = queue.Queue()
        started, release = threading.Event(), threading.Event()

        class Provider:
            def __init__(self, *args):
                pass

            def generate(self, request, token, progress):
                started.set()
                release.wait(2)
                token.check()
                return {}

        worker = ImageCoordinator(events, lambda: self.choice, Mock(), provider_factory=Provider)
        self.addCleanup(worker.close)
        worker.submit(response(draft()), "old-task")
        self.assertTrue(started.wait(1))
        worker.cancel()
        release.set()
        self.assertTrue(events.get(timeout=2)["cancelled"])

    def test_live_memory_omits_code_annotations_and_still_receives_completed_images(self):
        from otsc.context import ContextStore
        from otsc.models import LineAnnotation

        context = ContextStore()
        context.set_goal("A design with an example")
        artifact = draft()
        artifact.source_ids = [context.snapshot().observations[0].id]
        code = artifact.model_copy(update={"id": "example", "kind": "code", "content": "x = 1\n",
                                           "annotations": [LineAnnotation(line=1, explanation="Assign one.")]})
        answer = response(artifact)
        answer.artifacts.append(code)
        context.integrate(answer)
        before = json.loads(context.previous_artifacts)[1]
        self.assertNotIn("annotations", before)
        request = json.loads(artifact.content)
        job = ImageJob(context.session_id, artifact.id, image_key(request, self.choice), request, self.choice, Cancellation())
        update_image_memory(context, job, store_png(png()))
        self.assertEqual(json.loads(context.previous_artifacts)[1], before)
        self.assertEqual(json.loads(json.loads(context.previous_artifacts)[0]["content"])["status"], "ready")
        self.assertEqual(json.loads(json.loads(context.previous_answer)["artifacts"][0]["content"])["status"], "ready")
