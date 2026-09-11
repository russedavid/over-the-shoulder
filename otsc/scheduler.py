"""Two bounded worker lanes; only current, correctly ordered results can publish."""

import json
import queue
import threading
import time
from dataclasses import dataclass, field, replace
from functools import wraps
from uuid import uuid4

from otsc.context import ContextStore, Snapshot
from otsc.models import Assistance, NoAnswerUpdate
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


def serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._state_lock:
            return method(self, *args, **kwargs)
    return call


def answer_fingerprint(response, observations=()):
    """Ignore citation churn and the planner's bookkeeping, not actual output changes."""
    data = response.model_dump() if isinstance(response, Assistance) else response
    sources = {o.id: o for o in observations}
    conversation = []
    for reply in data.get("conversation", []):
        item = {key: value for key, value in reply.items() if key != "source_id"}
        source = sources.get(reply.get("source_id"))
        item["utterance"] = ({"text": source.text, "speaker": source.speaker, "channel": source.channel,
                              "confidence": source.confidence} if source else None)
        conversation.append(item)
    artifacts = []
    for artifact in data.get("artifacts", []):
        if artifact.get("id") == "_assistance_plan":
            continue
        item = {key: value for key, value in artifact.items() if key not in {"source_ids", "diff_context"}}
        if item.get("kind") in {"structured", "image"}:
            try:
                item["content"] = json.loads(item["content"])
            except (ValueError, TypeError):
                pass
        artifacts.append(item)
    return digest({
        "task": data.get("task"), "summary": data.get("summary"), "artifacts": artifacts,
        "conversation": conversation,
        "open_questions": data.get("open_questions", []),
    })


class Coordinator:
    def __init__(self, context: ContextStore, provider_for_lane, *, hourly_limit=120, trace=None):
        self._state_lock = threading.RLock()
        self.context, self.provider_for_lane = context, provider_for_lane
        self.events = queue.Queue()
        self.queues = {lane: queue.Queue(maxsize=1) for lane in ("quick", "deep")}
        self.tokens = []
        self.request_id = ""
        self.published_lane = -1
        self.last_requested_revision = -1
        self.last_requested_task_revision = -1
        self.last_evidence_key = None
        self.last_refresh_reason = ""
        self.last_quick_publication = None
        self.quick_started = False
        self.current: Assistance | None = None
        self.pending: Assistance | None = None
        self.pending_version = None
        self.pinned = False
        self.history = []
        self.closed = False
        self.calls = []
        self.limit_notice_at = 0
        self.hourly_limit = hourly_limit
        self.active_lanes = set()
        self.trace = trace
        self.artifact_bases = {}
        for lane in self.queues:
            threading.Thread(target=self._worker, args=(lane,), daemon=True, name="otsc-" + lane).start()

    @serialized
    def request(self, *, manual=False):
        if self.closed:
            return False
        if not manual and self.active_lanes:
            return False
        snapshot = replace(self.context.snapshot(), automatic_refresh=not manual)
        if not snapshot.observations:
            if manual:
                self.events.put({"type": "notice", "message": "Add task context or capture the current work first."})
            return False
        evidence_key = (snapshot.session_id, snapshot.evidence_revision)
        if not manual and evidence_key == self.last_evidence_key:
            # Memory rewrites are interpretations of already-reviewed evidence,
            # not permission to start a new answer feedback loop.
            self.last_requested_revision = snapshot.revision
            return False
        now = time.monotonic()
        self.calls = [t for t in self.calls if now - t < 3600]
        if len(self.calls) >= self.hourly_limit:
            if manual or now >= self.limit_notice_at:
                self.events.put({"type": "notice", "message": "The configured hourly assistance limit has been reached."})
                self.limit_notice_at = now + 60
            return False
        self.cancel()
        self.request_id = uuid4().hex
        self.published_lane = -1
        self.last_requested_revision = snapshot.revision
        self.last_requested_task_revision = snapshot.task_revision
        self.last_evidence_key = evidence_key
        self.calls.append(now)
        reviewing = not manual and bool(json.loads(snapshot.previous_answer))
        self.quick_started = not reviewing
        self.active_lanes = {"deep"} if reviewing else set(self.queues)
        self.events.put({"type": "started", "request_id": self.request_id, "revision": snapshot.revision,
                         "reviewing": reviewing})
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
            try:
                old = pending.get_nowait()
                if old:
                    old.token.cancel()
            except queue.Empty:
                pass
            if lane not in self.active_lanes:
                continue
            token = Cancellation()
            self.tokens.append(token)
            pending.put_nowait(Job(self.request_id, snapshot, lane, token))
        return True

    @serialized
    def allow_quick(self, job):
        """Only the still-current approved refresh can release its quick worker."""
        if self.closed or job.token.event.is_set():
            return
        if (job.request_id != self.request_id or job.snapshot.session_id != self.context.session_id
                or job.snapshot.task_revision != self.context.task_revision):
            job.token.cancel()
            return
        if self.quick_started:
            return
        token = Cancellation()
        self.tokens.append(token)
        self.quick_started = True
        self.active_lanes.add("quick")
        self.queues["quick"].put_nowait(Job(job.request_id, job.snapshot, "quick", token))

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
                progress.allow_quick = lambda current_job=job: self.allow_quick(current_job)
                if lane == "deep" and not getattr(provider, "gates_automatic_refresh", False):
                    self.allow_quick(job)
                job.token.check()
                response = provider.generate(
                    job.snapshot,
                    lane,
                    job.token,
                    progress,
                )
                job.token.check()
                if isinstance(response, NoAnswerUpdate):
                    if self.trace:
                        self.trace.record("generation_finished", **fields, outcome="unchanged",
                                          elapsed_ms=round((time.monotonic() - job.started) * 1000))
                    self.events.put({"type": "unchanged", "job": job, "reason": response.reason})
                    continue
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
                    {
                        "type": "result",
                        "job": job,
                        "response": response,
                        "elapsed": time.monotonic() - job.started,
                        "delivery_notes": response._delivery_notes,
                    }
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
                if lane == "deep":
                    # A failed review is not evidence that nothing changed.
                    # Preserve quick assistance when the deeper provider fails.
                    self.allow_quick(job)
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

    @serialized
    def accept(self, event):
        job = event.get("job")
        if not job or job.request_id != self.request_id or job.snapshot.session_id != self.context.session_id:
            if job and self.trace:
                self.trace.record(
                    "result_discarded", request_id=job.request_id, lane=job.lane, reason="superseded_request"
                )
            return False
        if event["type"] in {"result", "error", "cancelled", "unchanged"}:
            self.active_lanes.discard(job.lane)
        # New passive evidence is for the next request. It must not starve the
        # current answer. Explicit task/project edits and newer requests still
        # invalidate old work; each delivered artifact keeps its immutable base.
        if job.snapshot.task_revision != self.context.task_revision or job.token.event.is_set():
            if self.trace:
                self.trace.record("result_discarded", request_id=job.request_id, lane=job.lane, reason="stale_context")
            return False
        if event["type"] == "unchanged":
            self.last_refresh_reason = event["reason"]
            if self.pending:
                self.pending_version = (job.request_id, job.snapshot.session_id, job.snapshot.task_revision)
            if self.trace:
                self.trace.record("answer_retained", request_id=job.request_id, lane=job.lane,
                                  reason="no_substantive_change", result_hash=digest(event["reason"]))
            return True
        if event["type"] != "result":
            if event["type"] == "error" and job.lane == "deep":
                self.last_refresh_reason = "The refresh could not complete: " + event["message"]
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
        fingerprint = answer_fingerprint(response, job.snapshot.observations)
        prior_answer = json.loads(self.context.previous_answer) if job.lane == "deep" else {}
        prior = ((job.snapshot.session_id, answer_fingerprint(prior_answer, job.snapshot.observations)) if prior_answer else None) \
            if job.lane == "deep" else self.last_quick_publication
        duplicate = job.snapshot.automatic_refresh and prior == (job.snapshot.session_id, fingerprint)
        self.context.integrate(response, replace_open_questions=job.lane == "deep", sources=job.snapshot.observations)
        if job.lane == "quick":
            self.last_quick_publication = (job.snapshot.session_id, fingerprint)
        if response._refresh_reason:
            self.last_refresh_reason = response._refresh_reason
        files = json.loads(job.snapshot.verified_files)
        for artifact in response.artifacts:
            if artifact.basis == "verified_file" and artifact.path in files:
                self.artifact_bases[digest(artifact.model_dump())] = {
                    "path": artifact.path,
                    "base_hash": digest(files[artifact.path]),
                }
        if len(self.artifact_bases) > 256:
            self.artifact_bases = dict(list(self.artifact_bases.items())[-256:])
        if duplicate:
            event["duplicate"] = True
            self.last_refresh_reason = "The delivered answer matches the existing output; only its evidence references or plan review changed."
            if self.pending:
                self.pending_version = (job.request_id, job.snapshot.session_id, job.snapshot.task_revision)
            if self.trace:
                self.trace.record("answer_retained", request_id=job.request_id, lane=job.lane,
                                  reason="duplicate_output", result_hash=fingerprint)
            return True
        if self.current:
            self.history.append(self.current)
            self.history = self.history[-12:]
        if self.pinned:
            self.pending = response
            self.pending_version = (job.request_id, job.snapshot.session_id, job.snapshot.task_revision)
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

    @serialized
    def toggle_pin(self):
        self.pinned = not self.pinned
        if not self.pinned and self.pending:
            if self.pending_version == (self.request_id, self.context.session_id, self.context.task_revision):
                self.current = self.pending
            self.pending, self.pending_version = None, None

    @serialized
    def cancel(self):
        self.active_lanes.clear()
        for token in self.tokens:
            token.cancel()
        self.tokens.clear()

    @serialized
    def close(self):
        self.cancel()
        self.closed = True
        for pending in self.queues.values():
            try:
                pending.get_nowait()
            except queue.Empty:
                pass
            pending.put_nowait(None)
