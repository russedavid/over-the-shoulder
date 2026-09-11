"""Independent version histories for named outputs, with per-type viewing cursors."""

import json
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from otsc.models import Assistance, Observation, Record
from otsc.output_history import MAX_OUTPUTS
from otsc.telemetry import digest

GUIDANCE = "output:guidance"
REPLIES = "output:replies"
DETAILS = "debug:details"
CONTEXT = "debug:context"
FILES = "debug:files"
EVENTS = "debug:events"
LIVE_DEBUG = {CONTEXT: "Context", FILES: "Observed files", EVENTS: "Events"}
MAX_TYPES = 64


def artifact_key(identity):
    return "artifact:" + identity


class OutputVersion(Record):
    id: str
    parent_id: str
    response: Assistance
    sources: list[Observation] = Field(default_factory=list)
    session_id: str
    goal: str
    at: float
    signature: str

    @property
    def artifact(self):
        return self.response.artifacts[0] if self.response.artifacts else None


class OutputType(Record):
    key: str
    label: str
    kind: Literal["artifact", "guidance", "replies", "details"]
    debug: bool = False
    versions: list[OutputVersion] = Field(default_factory=list, max_length=MAX_OUTPUTS + 1)
    cursor: str | None = None
    following: bool = False
    status: str = ""
    error: str = ""

    @property
    def position(self):
        return next((i for i, version in enumerate(self.versions) if version.id == self.cursor), -1)

    @property
    def visible(self):
        position = self.position
        return self.versions[position] if position >= 0 else None

    @property
    def newer_count(self):
        return len(self.versions) - self.position - 1

    @model_validator(mode="after")
    def valid_cursor(self):
        ids = [version.id for version in self.versions]
        if len(ids) != len(set(ids)) or self.cursor is not None and self.cursor not in ids:
            raise ValueError("Invalid output-type viewing position")
        return self


class BrowserState(Record):
    types: dict[str, OutputType] = Field(default_factory=dict, max_length=MAX_TYPES)
    active_key: str = ""
    last_actionable: str = ""
    last_artifact: str = ""
    auto_pick: bool = True

    @model_validator(mode="after")
    def valid_types(self):
        if any(key != value.key for key, value in self.types.items()):
            raise ValueError("Output-type keys do not match their records")
        if self.active_key and self.active_key not in self.types and self.active_key not in LIVE_DEBUG:
            raise ValueError("Unknown selected output type")
        if set(self.types) & LIVE_DEBUG.keys():
            raise ValueError("Live debug views cannot masquerade as output histories")
        for key, stream in self.types.items():
            if stream.kind == "artifact":
                if any(not v.artifact or len(v.response.artifacts) != 1 or artifact_key(v.artifact.id) != key for v in stream.versions):
                    raise ValueError("An artifact history must keep one stable output identity")
                if key == artifact_key("_assistance_plan") and not stream.debug:
                    raise ValueError("The assistance plan belongs in Debug")
        return self


class OutputBrowser:
    def __init__(self, state=None):
        self.state = state.model_copy(deep=True) if state else BrowserState()

    @property
    def active_key(self):
        return self.state.active_key

    @property
    def current(self):
        return self.state.types.get(self.active_key)

    @property
    def visible(self):
        return self.current.visible if self.current else None

    @property
    def frozen(self):
        return bool(self.current and not self.current.following)

    @property
    def position(self):
        return self.current.position if self.current else -1

    @property
    def newer_count(self):
        return self.current.newer_count if self.current else 0

    def rows(self, debug=False):
        groups = {"artifact": 0, "replies": 1, "guidance": 2, "details": 3}
        available = [value for value in self.state.types.values() if debug or not value.debug]
        available.sort(key=lambda value: (value.debug, groups[value.kind]))
        result = [(value.key, value.label, value.newer_count, value.status) for value in available]
        if debug:
            result.extend((key, title, 0, "") for key, title in LIVE_DEBUG.items())
        return result

    def _record(self, entry, key, label, kind, response, *, debug=False, signature=None):
        stream = self.state.types.get(key)
        if stream is None:
            if len(self.state.types) >= MAX_TYPES:
                # Keep the active output; bound very long sessions with many
                # retired task-defined names by evicting the oldest inactive type.
                candidates = [value for value in self.state.types.values() if value.key != self.active_key]
                oldest = min(candidates, key=lambda value: value.versions[-1].at if value.versions else 0)
                del self.state.types[oldest.key]
            stream = self.state.types[key] = OutputType(key=key, label=label, kind=kind, debug=debug)
        stream.label, stream.status, stream.error = label, "", ""
        content = response.model_dump(exclude={"observed_files"})
        if signature is None:
            for artifact in content["artifacts"]:
                artifact.pop("source_ids", None)
                artifact.pop("title", None)
                if artifact["kind"] in {"image", "structured"}:
                    try:
                        artifact["content"] = json.loads(artifact["content"])
                    except ValueError:
                        pass
            if kind == "artifact":
                content = content["artifacts"]
            elif kind == "replies":
                source_map = {source.id: source for source in entry.sources}
                content = [{**reply.model_dump(exclude={"source_id"}),
                            "source": source_map[reply.source_id].model_dump(exclude={"id", "at", "image_path"})
                            if reply.source_id in source_map else None} for reply in response.conversation]
            elif kind == "guidance":
                content = {"summary": response.summary, "open_questions": response.open_questions}
            signature = digest(content)
        if stream.versions and stream.versions[-1].signature == signature:
            return False
        cited = {reply.source_id for reply in response.conversation}
        version = OutputVersion(id=uuid4().hex, parent_id=entry.id, response=response.model_copy(deep=True),
                                session_id=entry.session_id, goal=entry.goal, at=entry.at, signature=signature,
                                sources=[source.model_copy(update={"image_path": ""}, deep=True) for source in entry.sources if source.id in cited])
        held = stream.visible
        stream.versions.append(version)
        if key == self.active_key and (stream.following or stream.cursor is None):
            stream.cursor = version.id
        stream.versions = stream.versions[-MAX_OUTPUTS:]
        if held and stream.cursor == held.id and all(item.id != held.id for item in stream.versions):
            stream.versions.insert(0, held)
        return True

    def ingest(self, entry):
        """Project changed outputs only; carried code/images do not gain versions."""
        source = entry.response
        primary = []
        for artifact in entry.artifacts:
            key = artifact_key(artifact.id)
            debug = artifact.id == "_assistance_plan"
            response = Assistance(task=source.task, summary=source.summary, artifacts=[artifact], conversation=[],
                                  observed_files=[], open_questions=[])
            if artifact.kind == "image":
                try:
                    data = json.loads(artifact.content)
                except ValueError:
                    data = {}
                if not isinstance(data, dict) or data.get("status") != "ready":
                    stream = self.state.types.get(key)
                    if stream is None and len(self.state.types) < MAX_TYPES:
                        stream = self.state.types[key] = OutputType(key=key, label=artifact.title, kind="artifact")
                    if stream:
                        stream.label = artifact.title
                        stream.status = "failed" if isinstance(data, dict) and data.get("status") == "failed" else "pending"
                        stream.error = str(data.get("error", "")) if isinstance(data, dict) else ""
                    primary.append(key)
                    continue
            self._record(entry, key, artifact.title, "artifact", response, debug=debug)
            if not debug:
                primary.append(key)
        if source.conversation:
            response = source.model_copy(update={"artifacts": [], "observed_files": [], "summary": "", "open_questions": []}, deep=True)
            self._record(entry, REPLIES, "Suggested replies", "replies", response)
        if source.summary and (entry.lane == "quick" or not primary and not source.conversation):
            response = source.model_copy(update={"artifacts": [], "observed_files": [], "conversation": []}, deep=True)
            self._record(entry, GUIDANCE, "Quick guidance" if entry.lane == "quick" else "Guidance", "guidance", response)
        detail = source.model_copy(update={"artifacts": [], "observed_files": [], "conversation": []}, deep=True)
        if source.task or source.summary:
            self._record(entry, DETAILS, "Response details", "details", detail, debug=True)
        rows = self.rows()
        if not self.active_key and rows:
            initial = primary[0] if primary else GUIDANCE if GUIDANCE in self.state.types else rows[0][0]
            self.select_type(initial, follow=True, automatic=True)
        elif self.state.auto_pick and primary and self.active_key == GUIDANCE:
            self.select_type(primary[0], follow=True, automatic=True)
        if primary:
            self.state.auto_pick = False

    def select_type(self, key, *, follow=False, automatic=False):
        if key not in self.state.types and key not in LIVE_DEBUG:
            return False
        if self.current and key != self.active_key:
            self.current.following = False
        self.state.active_key = key
        stream = self.current
        if stream:
            if stream.versions and (follow or stream.cursor is None):
                stream.cursor = stream.versions[-1].id
            stream.following = follow
            if not stream.debug:
                self.state.last_actionable = key
                if stream.kind == "artifact":
                    self.state.last_artifact = key
        if not automatic:
            self.state.auto_pick = False
        return True

    def freeze(self):
        if self.current:
            self.current.following = False
        self.state.auto_pick = False

    def move(self, step):
        stream = self.current
        if not stream or not 0 <= stream.position + step < len(stream.versions):
            return False
        stream.cursor = stream.versions[stream.position + step].id
        self.freeze()
        return True

    def resume(self):
        if self.current:
            return self.select_type(self.active_key, follow=True)
        return False

    def cycle_type(self, step, *, debug=False):
        keys = [row[0] for row in self.rows(debug)]
        if not keys:
            return False
        position = keys.index(self.active_key) if self.active_key in keys else 0
        return self.select_type(keys[(position + step) % len(keys)])

    def actionable(self, *, artifacts=False):
        preferred = self.state.last_artifact if artifacts else self.state.last_actionable
        keys = [row[0] for row in self.rows() if not artifacts or self.state.types[row[0]].kind == "artifact"]
        return preferred if preferred in keys else keys[0] if keys else ""

    def clear(self):
        self.state = BrowserState()
