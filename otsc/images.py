"""Generated diagram images: bounded provider calls and host-verified PNG assets."""

import base64
import hashlib
import io
import json
import re
import threading
import time
from dataclasses import dataclass
from uuid import uuid4

import httpx
from PIL import Image

from otsc.privacy import app_directory, private_directory, private_write, redact
from otsc.scheduler import Cancellation, Cancelled
from otsc.telemetry import Progress, digest, record_progress

MAX_IMAGE_BYTES = 30_000_000


def image_key(data, choice):
    return digest({"prompt": data.get("prompt", ""), "reference": data.get("reference"), "model": choice.model,
                   "endpoint": choice.endpoint()})


def image_path(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid generated image descriptor")
    identity, expected = data.get("asset_id", ""), data.get("sha256", "")
    if not re.fullmatch(r"[0-9a-f]{32}", identity) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("No verified generated image is attached")
    root = app_directory() / "generated-images"
    path = root / (identity + ".png")
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Generated image is unavailable")
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("Generated image exceeds the size limit")
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Generated image changed after publication")
    return path


def store_png(data):
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image provider returned an empty or oversized image")
    with Image.open(io.BytesIO(data)) as picture:
        if picture.format != "PNG" or picture.width * picture.height > 16_000_000:
            raise ValueError("Image provider must return a bounded PNG")
        size = picture.size
        picture.verify()
    identity = uuid4().hex
    root = private_directory(app_directory() / "generated-images")
    private_write(root / (identity + ".png"), data)
    return {"asset_id": identity, "sha256": hashlib.sha256(data).hexdigest(), "width": size[0], "height": size[1]}


class ImageProvider:
    def __init__(self, choice, credentials, *, transport=None):
        self.choice, self.credentials, self.transport = choice.model_copy(deep=True), credentials, transport

    def generate(self, request, token, progress):
        if self.choice.provider not in {"openai", "compatible"}:
            raise ValueError("Configure an OpenAI image provider in Settings")
        key = self.credentials.get(self.choice, "image")
        if not key and self.choice.provider == "openai":
            raise ValueError("Add the image API key in Settings")
        prompt = (
            "Create a professional, readable image that fulfills this task's visual deliverable. "
            "Use a coherent layout and clear hierarchy, short legible labels, generous spacing, and intentional connections. "
            "Avoid overlaps, tangled arrows, decorative clutter, or text walls. Preserve all specified names, directions, "
            "requirements and meaningful distinctions. Do not add an invented company logo or unrelated components.\n\n"
            + request["prompt"]
        )
        body = {"model": self.choice.model, "prompt": prompt, "size": "1536x1024", "quality": "high", "output_format": "png"}
        headers = {"Authorization": "Bearer " + key} if key else {}
        record_progress(progress, "provider_request", provider=self.choice.provider, model=self.choice.model, prompt_hash=digest(prompt))
        with httpx.Client(timeout=httpx.Timeout(600, connect=20), transport=self.transport, follow_redirects=False) as client:
            token.on_cancel(client.close)
            token.check()
            if request.get("reference"):
                reference = image_path(request["reference"])
                endpoint = "/images/edits"
                arguments = dict(data=body, files={"image": ("previous-design.png", reference.read_bytes(), "image/png")})
            else:
                endpoint, arguments = "/images/generations", dict(json=body)
            with client.stream("POST", self.choice.endpoint() + endpoint, headers=headers, **arguments) as response:
                content = bytearray()
                for chunk in response.iter_bytes():
                    token.check()
                    content.extend(chunk)
                    if len(content) > MAX_IMAGE_BYTES * 4 // 3 + 1_000_000:
                        raise ValueError("Image provider response exceeds the size limit")
            token.check()
            try:
                payload = json.loads(content)
            except ValueError:
                raise RuntimeError(f"Image generation HTTP {response.status_code}: invalid response") from None
            if response.status_code >= 300:
                message = payload.get("error", {}).get("message", "Image request failed") if isinstance(payload, dict) else "Image request failed"
                raise RuntimeError(f"Image generation HTTP {response.status_code}: {redact(message)[:450]}")
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list) or not data or not isinstance(data[0], dict):
                raise ValueError("Image provider did not return an image")
            encoded = data[0].get("b64_json", "")
            if not isinstance(encoded, str):
                raise ValueError("Image provider returned invalid image data")
            if len(encoded) > MAX_IMAGE_BYTES * 4 // 3 + 4:
                raise ValueError("Encoded image exceeds the size limit")
            result = store_png(base64.b64decode(encoded, validate=True))
            token.check()
            record_progress(progress, "image_generated", image_hash=result["sha256"])
            return result


@dataclass
class ImageJob:
    session_id: str
    section: str
    key: str
    request: dict
    choice: object
    token: Cancellation
    task_revision: int = 0


class ImageCoordinator:
    def __init__(self, events, choice, credentials, *, trace=None, provider_factory=ImageProvider):
        self.events, self.choice, self.credentials, self.trace = events, choice, credentials, trace
        self.provider_factory = provider_factory
        self.condition = threading.Condition()
        self.pending = {}
        self.active = None
        self.cache = {}
        self.closed = False
        threading.Thread(target=self._worker, name="otsc-image-generation", daemon=True).start()

    def submit(self, response, session_id, task_revision=0):
        image_sections = {artifact.id for artifact in response.artifacts if artifact.kind == "image"}
        with self.condition:
            for slot, job in list(self.pending.items()):
                if job.session_id != session_id or job.section not in image_sections:
                    job.token.cancel()
                    del self.pending[slot]
            if self.active and (self.active.session_id != session_id or self.active.section not in image_sections):
                self.active.token.cancel()
            for artifact in response.artifacts:
                if artifact.kind != "image":
                    continue
                try:
                    data = json.loads(artifact.content)
                except ValueError:
                    continue
                if not isinstance(data, dict) or data.get("status") != "pending" or not data.get("prompt"):
                    continue
                choice = self.choice().model_copy(deep=True)
                key = image_key(data, choice)
                slot = (session_id, artifact.id)
                if self.active and (self.active.session_id, self.active.section) == slot:
                    if self.active.key == key and not self.active.token.event.is_set():
                        # A newly accepted plan reaffirmed the same image brief.
                        # Keep its in-flight rendering through minor task edits.
                        self.active.task_revision = task_revision
                        continue
                    self.active.token.cancel()
                if slot in self.pending:
                    if self.pending[slot].key == key:
                        self.pending[slot].task_revision = task_revision
                        continue
                    self.pending[slot].token.cancel()
                job = ImageJob(session_id, artifact.id, key, data, choice, Cancellation(), task_revision)
                if key in self.cache:
                    self.events.put({"type": "image_ready", "job": job, "asset": self.cache[key], "seconds": 0})
                elif not self.closed:
                    self.pending[slot] = job
            self.condition.notify()

    def _worker(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending)
                if self.closed:
                    return
                slot = next(iter(self.pending))
                job = self.active = self.pending.pop(slot)
            start = time.monotonic()
            event = {"type": "image_ready", "job": job}
            try:
                job.token.check()
                progress = Progress(lambda text: None, self.trace, lane="image", session_id=job.session_id, request_id=job.key)
                event["asset"] = self.provider_factory(job.choice, self.credentials).generate(job.request, job.token, progress)
                job.token.check()
                with self.condition:
                    self.cache[job.key] = event["asset"]
                    if len(self.cache) > 64:
                        self.cache.pop(next(iter(self.cache)))
            except Cancelled:
                event["cancelled"] = True
            except Exception as error:
                event["error"] = redact(str(error))[:500]
            event["seconds"] = time.monotonic() - start
            with self.condition:
                self.active = None
            self.events.put(event)

    def cancel(self):
        with self.condition:
            if self.active:
                self.active.token.cancel()
            for job in self.pending.values():
                job.token.cancel()
            self.pending.clear()

    def close(self):
        self.cancel()
        with self.condition:
            self.closed = True
            self.condition.notify()


def resolve_image(artifacts, job, asset=None, error=None):
    """Update only the still-current image brief; never replace unrelated output."""
    changed = False
    result = [artifact.model_copy(deep=True) for artifact in artifacts]
    for artifact in result:
        if artifact.id != job.section or artifact.kind != "image":
            continue
        content = resolved_content(artifact.content, job, asset, error)
        if content is not None:
            artifact.content = content
            changed = True
    return result, changed


def resolved_content(content, job, asset=None, error=None):
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("status") != "pending" or image_key(data, job.choice) != job.key:
        return None
    data.update({"status": "ready", **asset} if asset else {"status": "failed", "error": error or "Image generation failed"})
    return json.dumps(data, ensure_ascii=False)


def update_image_memory(context, job, asset=None, error=None):
    """Memory intentionally omits code annotations; don't revalidate it as UI artifacts."""
    with context._lock:
        for field in ("previous_artifacts", "previous_answer"):
            value = json.loads(getattr(context, field))
            records = value if isinstance(value, list) else value.get("artifacts", [])
            changed = False
            for record in records:
                if record.get("id") == job.section and record.get("kind") == "image":
                    content = resolved_content(record.get("content"), job, asset, error)
                    if content is not None:
                        record["content"] = content
                        changed = True
            if changed:
                setattr(context, field, json.dumps(value, ensure_ascii=False))
