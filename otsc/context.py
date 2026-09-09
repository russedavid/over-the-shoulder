"""Bounded observations and an explicitly partial observed filesystem."""

import json
import re
import threading
import time
from dataclasses import dataclass, field
from uuid import uuid4

from otsc.models import Assistance, Observation, ObservedFile, text_hash
from otsc.privacy import redact


def normalize_screen(text: str) -> str:
    text = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)\b", "[clock]", text, flags=re.I)
    text = re.sub(r"(?im)(\bclock\s+)\d{1,2}:\d{2}(?::\d{2})?\b", r"\1[clock]", text)
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


@dataclass(frozen=True)
class Snapshot:
    session_id: str
    revision: int
    goal: str
    observations: tuple[Observation, ...]
    workspace: str
    repo_root: str
    previous_task: str
    previous_summary: str
    verified_files: str = "{}"
    previous_artifacts: str = "[]"

    def prompt_context(self) -> dict:
        return {
            "goal": self.goal,
            "context_revision": self.revision,
            "observations": [o.model_dump(exclude={"image_path"}) for o in self.observations],
            "observed_workspace": self.workspace,
            "previous_task": self.previous_task,
            "previous_summary": self.previous_summary,
            "previous_artifacts": json.loads(self.previous_artifacts),
        }


@dataclass
class ContextStore:
    session_id: str = field(default_factory=lambda: uuid4().hex)
    revision: int = 0
    goal: str = ""
    repo_root: str = ""
    observations: list[Observation] = field(default_factory=list)
    files: dict[str, list[ObservedFile]] = field(default_factory=dict)
    verified_files: dict[str, str] = field(default_factory=dict)
    previous_task: str = ""
    previous_summary: str = ""
    previous_artifacts: str = "[]"
    _screen_fingerprint: str = ""
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def set_goal(self, goal: str) -> bool:
        with self._lock:
            goal = redact(goal.strip())
            if goal == self.goal:
                return False
            self.goal = goal
            if not self.add("task", goal, "typed", "primary_user"):
                self.revision += 1
            return True

    def set_repo(self, root: str):
        with self._lock:
            if root != self.repo_root:
                self.repo_root = root
                self.verified_files = {}
                self.revision += 1

    def set_verified_files(self, root: str, files: dict[str, str]):
        with self._lock:
            if root != self.repo_root or files == self.verified_files:
                return False
            self.verified_files = dict(files)
            self.revision += 1
            return True

    def add(self, kind, text, channel, speaker="not_applicable", *, image_path="", at=None, confidence="direct"):
        text = redact(text).strip()
        if not text:
            return None
        with self._lock:
            if kind == "screen":
                fingerprint = text_hash(normalize_screen(text))
                if fingerprint == self._screen_fingerprint:
                    return None
                self._screen_fingerprint = fingerprint
            if kind == "speech" and self.observations:
                previous = self.observations[-1]
                # Same-channel duplicate delivery is safe to coalesce; cross-channel speech retains attribution.
                if (
                    previous.kind == "speech"
                    and previous.channel == channel
                    and previous.text == text
                    and (at or time.time()) - previous.at < 2
                ):
                    return None
            observation = Observation(
                id="obs-" + uuid4().hex[:12],
                kind=kind,
                text=text[:10000],
                channel=channel,
                speaker=speaker,
                at=at or time.time(),
                confidence=confidence,
                image_path=image_path,
            )
            self.observations.append(observation)
            self.observations = self.observations[-100:]
            self.revision += 1
            return observation

    def integrate(self, response: Assistance):
        with self._lock:
            self.previous_task, self.previous_summary = response.task, response.summary
            artifacts = []
            for item in response.artifacts:
                record = item.model_dump(exclude={"annotations"})
                if len(json.dumps([*artifacts, record])) < 30000:
                    artifacts.append(record)
            if artifacts:
                self.previous_artifacts = json.dumps(artifacts)
            for item in response.observed_files:
                versions = self.files.setdefault(item.path, [])
                if not any(v.content == item.content and v.first_line == item.first_line for v in versions):
                    versions.append(item.model_copy(deep=True))
                self.files[item.path] = versions[-8:]
            if len(self.files) > 60:
                self.files = dict(list(self.files.items())[-60:])

    def workspace_text(self) -> str:
        records = []
        for path, fragments in reversed(list(self.files.items())):
            record = {
                "path": path,
                "completeness": "observed_fragments_not_verified_files",
                "fragments": [x.model_dump() for x in fragments[-3:]],
            }
            if len(json.dumps([*records, record], ensure_ascii=False)) <= 18000:
                records.append(record)
        return json.dumps(records, ensure_ascii=False)

    def snapshot(self) -> Snapshot:
        with self._lock:
            selected, budget = [], 24000
            for observation in reversed(self.observations):
                if len(observation.text) > budget:
                    continue
                selected.append(observation.model_copy(deep=True))
                budget -= len(observation.text)
            selected.reverse()
            return Snapshot(
                self.session_id,
                self.revision,
                self.goal,
                tuple(selected),
                self.workspace_text(),
                self.repo_root,
                self.previous_task,
                self.previous_summary,
                json.dumps(self.verified_files, ensure_ascii=False),
                self.previous_artifacts,
            )

    def clear(self):
        with self._lock:
            self.session_id = uuid4().hex
            self.revision += 1
            self.observations.clear()
            self.files.clear()
            self.previous_task = self.previous_summary = self._screen_fingerprint = ""
            self.previous_artifacts = "[]"
            if self.goal:
                self.add("task", self.goal, "typed", "primary_user")
