"""Retained display snapshots, independent of the live assistance/context state."""

import time
from uuid import uuid4

from pydantic import Field

from otsc.models import Artifact, Assistance, Observation, Record

MAX_OUTPUTS = 24


class OutputSnapshot(Record):
    id: str
    response: Assistance
    artifacts: list[Artifact]
    sources: list[Observation] = Field(default_factory=list)
    session_id: str
    goal: str
    lane: str
    at: float
    selected_artifact_id: str = ""

    def title(self):
        return f"{time.strftime('%H:%M:%S', time.localtime(self.at))} · {self.lane.capitalize()} · {self.response.task[:65]}"


class OutputHistory:
    def __init__(self, *, limit=MAX_OUTPUTS):
        self.limit = limit
        self.entries = []
        self.frozen = False
        self.selected_id = None

    @property
    def latest(self):
        return self.entries[-1] if self.entries else None

    @property
    def visible(self):
        if not self.frozen:
            return self.latest
        return next((item for item in self.entries if item.id == self.selected_id), None)

    @property
    def position(self):
        visible = self.visible
        return next((i for i, item in enumerate(self.entries) if visible and item.id == visible.id), -1)

    @property
    def newer_count(self):
        return len(self.entries) - self.position - 1 if self.frozen else 0

    def _trim(self):
        selected = self.visible if self.frozen else None
        self.entries = self.entries[-self.limit:]
        # Holding an old output never expires it. Keep that one anchor alongside
        # the bounded recent timeline until the user moves away from it.
        if selected and all(item.id != selected.id for item in self.entries):
            self.entries.insert(0, selected)

    def append(self, response, *, session_id, goal, lane, sources=(), identity=None, artifacts=None, at=None):
        identity = identity or uuid4().hex
        if any(item.id == identity for item in self.entries):
            return self.latest
        prior = self.latest
        if artifacts is None:
            artifacts = response.artifacts
            if not artifacts and prior and (prior.session_id, prior.goal) == (session_id, goal):
                artifacts = prior.artifacts
        cited = {reply.source_id for reply in response.conversation}
        item = OutputSnapshot(
            id=identity, response=response.model_copy(deep=True), artifacts=[a.model_copy(deep=True) for a in artifacts],
            sources=[o.model_copy(update={"image_path": ""}, deep=True) for o in sources if o.id in cited],
            session_id=session_id, goal=goal, lane=lane, at=time.time() if at is None else at,
        )
        self.entries.append(item)
        self._trim()
        return item

    def freeze(self):
        if not self.frozen:
            self.selected_id = self.latest.id if self.latest else None
            self.frozen = True

    def select(self, identity):
        if not any(item.id == identity for item in self.entries):
            return False
        self.frozen, self.selected_id = True, identity
        self._trim()
        return True

    def move(self, step):
        target = self.position + step
        if not 0 <= target < len(self.entries):
            return False
        # Reaching the newest entry still holds it. Only Latest resumes updates.
        return self.select(self.entries[target].id)

    def resume(self):
        self.frozen, self.selected_id = False, None
        self._trim()

    def clear(self):
        self.entries.clear()
        self.frozen, self.selected_id = False, None
