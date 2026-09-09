"""Two bounded worker lanes; only current, correctly ordered results can publish."""

import json
import queue
import threading
import time
from dataclasses import dataclass, field
from uuid import uuid4

from otsc.context import ContextStore, Snapshot
from otsc.models import Assistance
from otsc.privacy import redact
from otsc.telemetry import Progress, digest


class Cancelled(Exception):
    pass


class Cancellation:
    def __init__(self):
        self.event = threading.Event()
        self._callbacks = []
        self._lock = threading.Lock()

    def check(self):
        if self.event.is_set():
            raise Cancelled()

    def on_cancel(self, callback):
        with self._lock:
            if self.event.is_set():
                callback()
            else:
                self._callbacks.append(callback)

    def cancel(self):
        self.event.set()
        with self._lock:
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass


@dataclass
class Job:
    request_id: str
    snapshot: Snapshot
    lane: str
    token: Cancellation
    started: float = field(default_factory=time.monotonic)


class Coordinator:
    def __init__(self, context: ContextStore, provider_for_lane, *, hourly_limit=120, trace=None):
        self.context, self.provider_for_lane = context, provider_for_lane
        self.events = queue.Queue()
        self.queues = {lane: queue.Queue(maxsize=1) for lane in ("quick", "deep")}
        self.tokens = []
        self.request_id = ""
        self.published_lane = -1
        self.last_requested_revision = -1
        self.current: Assistance | None = None
        self.pending: Assistance | None = None
        self.pending_version = None
        self.pinned = False
        self.history = []
        self.closed = False
        self.calls = []
        self.hourly_limit = hourly_limit
        self.active_lanes = set()
        self.trace = trace
        self.artifact_bases = {}
        for lane in self.queues:
            threading.Thread(target=self._worker, args=(lane,), daemon=True, name="otsc-" + lane).start()

    def request(self, *, manual=False):
        if self.closed:
            return False
        snapshot = self.context.snapshot()
        if not snapshot.observations:
            self.events.put({"type": "notice", "message": "Add task context or capture the current work first."})
            return False
        if not manual and snapshot.revision == self.last_requested_revision:
            return False
        now = time.monotonic()
        self.calls = [t for t in self.calls if now - t < 3600]
        if len(self.calls) >= self.hourly_limit:
            self.events.put({"type": "notice", "message": "The configured hourly assistance limit has been reached."})
            return False
        self.cancel()
        self.request_id = uuid4().hex
        self.published_lane = -1
        self.last_requested_revision = snapshot.revision
        self.calls.append(now)
        self.active_lanes = set(self.queues)
        self.events.put({"type": "started", "request_id": self.request_id, "revision": snapshot.revision})
        if self.trace:
            self.trace.record(
                "request_started",
                request_id=self.request_id,
                session_id=snapshot.session_id,
                revision=snapshot.revision,
                observations=len(snapshot.observations),
                files=len(json.loads(snapshot.verified_files)),
                snapshot_hash=digest(snapshot.prompt_context()),
            )
        for lane, pending in self.queues.items():
            token = Cancellation()
            self.tokens.append(token)
            try:
                old = pending.get_nowait()
                if old:
                    old.token.cancel()
            except queue.Empty:
                pass
            pending.put_nowait(Job(self.request_id, snapshot, lane, token))
        return True

    def _worker(self, lane):
        while True:
            job = self.queues[lane].get()
            if job is None:
                return
            try:
                job.token.check()
                provider = self.provider_for_lane(lane)
                choice = getattr(provider, "choice", None)
                fields = {
                    "request_id": job.request_id,
                    "session_id": job.snapshot.session_id,
                    "revision": job.snapshot.revision,
                    "lane": lane,
                    "provider": getattr(choice, "provider", "synthetic"),
                    "model": getattr(choice, "model", ""),
                }
                if self.trace:
                    self.trace.record("generation_started", **fields)
                progress = Progress(
                    lambda text: self.events.put(
                        {"type": "progress", "request_id": job.request_id, "lane": lane, "message": redact(text)}
                    ),
                    self.trace,
                    **fields,
                )
                response = provider.generate(
                    job.snapshot,
                    lane,
                    job.token,
                    progress,
                )
                job.token.check()
                response.validate_sources(job.snapshot.observations, json.loads(job.snapshot.verified_files))
                if self.trace:
                    self.trace.record(
                        "generation_finished",
                        **fields,
                        outcome="success",
                        elapsed_ms=round((time.monotonic() - job.started) * 1000),
                        artifacts=len(response.artifacts),
                    )
                self.events.put(
                    {"type": "result", "job": job, "response": response, "elapsed": time.monotonic() - job.started}
                )
            except Cancelled:
                if self.trace:
                    self.trace.record(
                        "generation_finished",
                        request_id=job.request_id,
                        session_id=job.snapshot.session_id,
                        lane=lane,
                        outcome="cancelled",
                        elapsed_ms=round((time.monotonic() - job.started) * 1000),
                    )
                self.events.put({"type": "cancelled", "job": job})
            except Exception as error:
                if self.trace:
                    self.trace.record(
                        "generation_finished",
                        request_id=job.request_id,
                        session_id=job.snapshot.session_id,
                        lane=lane,
                        outcome="cancelled" if job.token.event.is_set() else "error",
                        error_type=type(error).__name__,
                        elapsed_ms=round((time.monotonic() - job.started) * 1000),
                    )
                if not job.token.event.is_set():
                    self.events.put({"type": "error", "job": job, "message": redact(str(error))[:600]})

    def accept(self, event):
        job = event.get("job")
        if not job or job.request_id != self.request_id or job.snapshot.session_id != self.context.session_id:
            if job and self.trace:
                self.trace.record(
                    "result_discarded", request_id=job.request_id, lane=job.lane, reason="superseded_request"
                )
            return False
        if event["type"] in {"result", "error", "cancelled"}:
            self.active_lanes.discard(job.lane)
        if job.snapshot.revision != self.context.revision or job.token.event.is_set():
            if self.trace:
                self.trace.record("result_discarded", request_id=job.request_id, lane=job.lane, reason="stale_context")
            return False
        if event["type"] != "result":
            return True
        rank = {"quick": 0, "deep": 1}[job.lane]
        if rank < self.published_lane:
            if self.trace:
                self.trace.record(
                    "result_discarded", request_id=job.request_id, lane=job.lane, reason="late_quick_response"
                )
            return False
        self.published_lane = rank
        response = event["response"]
        files = json.loads(job.snapshot.verified_files)
        for artifact in response.artifacts:
            if artifact.basis == "verified_file" and artifact.path in files:
                self.artifact_bases[digest(artifact.model_dump())] = {
                    "path": artifact.path,
                    "base_hash": digest(files[artifact.path]),
                }
        if len(self.artifact_bases) > 256:
            self.artifact_bases = dict(list(self.artifact_bases.items())[-256:])
        if self.current:
            self.history.append(self.current)
            self.history = self.history[-12:]
        self.context.integrate(response, replace_open_questions=job.lane == "deep")
        if self.pinned:
            self.pending = response
            self.pending_version = (job.request_id, job.snapshot.session_id, job.snapshot.revision)
        else:
            self.current = response
        if self.trace:
            self.trace.record(
                "result_accepted",
                request_id=job.request_id,
                session_id=job.snapshot.session_id,
                lane=job.lane,
                revision=job.snapshot.revision,
                outcome="held" if self.pinned else "displayed",
                result_hash=digest(response.model_dump()),
            )
            for artifact in response.artifacts:
                self.trace.record(
                    "artifact_published",
                    request_id=job.request_id,
                    session_id=job.snapshot.session_id,
                    lane=job.lane,
                    artifact_hash=digest(artifact.model_dump()),
                )
        return True

    def toggle_pin(self):
        self.pinned = not self.pinned
        if not self.pinned and self.pending:
            if self.pending_version == (self.request_id, self.context.session_id, self.context.revision):
                self.current = self.pending
            self.pending, self.pending_version = None, None

    def cancel(self):
        self.active_lanes.clear()
        for token in self.tokens:
            token.cancel()
        self.tokens.clear()

    def close(self):
        self.cancel()
        self.closed = True
        for pending in self.queues.values():
            try:
                pending.get_nowait()
            except queue.Empty:
                pass
            pending.put_nowait(None)
