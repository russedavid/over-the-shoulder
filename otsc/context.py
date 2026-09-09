"""Bounded observations and an explicitly partial observed filesystem."""

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable
from uuid import uuid4

from otsc.models import Assistance, ContextItem, Observation, ObservedFile, ScreenReading, text_hash
from otsc.privacy import redact


def normalize_screen(text: str) -> str:
    text = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)\b", "[clock]", text, flags=re.I)
    text = re.sub(r"(?im)(\bclock\s+)\d{1,2}:\d{2}(?::\d{2})?\b", r"\1[clock]", text)
    return "\n".join(
        " " * (len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip())) + " ".join(line.split())
        for line in text.splitlines()
        if line.strip()
    )


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
    open_questions: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    restored: bool = False
    inspection_results: str = "[]"
    task_revision: int = 0
    evidence_revision: int = 0
    memory_revision: int = 0
    context_items: str = "[]"
    previous_answer: str = "{}"
    as_of: float = field(default=0, compare=False)
    recent_observation_ids: tuple[str, ...] = ()
    omitted_observation_ids: tuple[str, ...] = ()
    context_updated_through: int = -1

    def prompt_context(self) -> dict:
        observations = []
        for o in self.observations:
            record = o.model_dump(exclude={"image_path"})
            if record.get("reading"):
                # Source text is in text; interpretation stays separate and is not
                # eligible to corroborate literal observed file contents.
                record["reading"].pop("visible_text", None)
            observations.append(record)
        return {
            "goal": self.goal,
            "context_revision": self.revision,
            "observations": observations,
            "recent_window": {
                "start": self.as_of - 300, "end": self.as_of,
                "observation_ids": list(self.recent_observation_ids),
                "omitted_ids_due_to_size_limit": list(self.omitted_observation_ids),
            },
            "accumulated_context": json.loads(self.context_items),
            "memory_revision": self.memory_revision,
            "context_updated_through_evidence_revision": self.context_updated_through,
            "evidence_revision": self.evidence_revision,
            "previous_answer": json.loads(self.previous_answer),
            "observed_workspace": self.workspace,
            "previous_task": self.previous_task,
            "previous_summary": self.previous_summary,
            "previous_artifacts": json.loads(self.previous_artifacts),
            "unresolved_questions": list(self.open_questions),
            "user_constraints": list(self.constraints),
            "user_decisions": list(self.decisions),
            "restored_history_requires_fresh_evidence": self.restored,
            "inspection_results": json.loads(self.inspection_results),
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
    open_questions: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    restored: bool = False
    task_revision: int = 0
    evidence_revision: int = 0
    memory_revision: int = 0
    context_updated_through: int = -1
    context_items: dict[str, ContextItem] = field(default_factory=dict)
    retained_sources: dict[str, Observation] = field(default_factory=dict)
    retired_files: dict[str, dict] = field(default_factory=dict)
    previous_answer: str = "{}"
    clock: Callable[[], float] = field(default=time.time, repr=False)
    _screen_fingerprint: str = ""
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def changed(self, *, task=False):
        self.revision += 1
        self.evidence_revision += 1
        if task:
            self.task_revision += 1

    def set_goal(self, goal: str) -> bool:
        with self._lock:
            goal = redact(goal.strip())
            if goal == self.goal:
                return False
            self.goal = goal
            if not self.add("task", goal, "typed", "primary_user"):
                self.changed(task=True)
            return True

    def set_repo(self, root: str):
        with self._lock:
            if root != self.repo_root:
                self.repo_root = root
                self.verified_files = {}
                self.changed(task=True)

    def set_task_details(self, constraints, decisions):
        constraints = tuple(redact(line.strip()) for line in constraints if line.strip())
        decisions = tuple(redact(line.strip()) for line in decisions if line.strip())
        if max(len(constraints), len(decisions)) > 20 or any(len(x) > 1000 for x in (*constraints, *decisions)):
            raise ValueError("Use up to 20 entries per field, each under 1000 characters")
        with self._lock:
            if (constraints, decisions) == (self.constraints, self.decisions):
                return False
            self.constraints, self.decisions = constraints, decisions
            self.add(
                "note",
                "User-maintained task details:\nConstraints:\n"
                + "\n".join(constraints)
                + "\nDecisions:\n"
                + "\n".join(decisions),
                "typed",
                "primary_user",
            )
            return True

    def set_verified_files(self, root: str, files: dict[str, str]):
        with self._lock:
            if root != self.repo_root or files == self.verified_files:
                return False
            self.verified_files = dict(files)
            self.changed(task=True)
            return True

    def add(self, kind, text, channel, speaker="not_applicable", *, image_path="", at=None,
            confidence="direct", reading=None, keep_repeats=False, interrupting=False):
        text = redact(text).strip()
        if not text and reading is None:
            return None
        with self._lock:
            duplicate_screen = False
            if kind == "screen":
                visual = ScreenReading.model_validate(reading) if reading is not None else None
                fingerprint = text_hash(normalize_screen(text) + (
                    json.dumps(visual.model_dump(exclude={"visible_text", "inferred_task"}), sort_keys=True) if visual else ""
                ))
                if fingerprint == self._screen_fingerprint:
                    if not keep_repeats:
                        return None
                    duplicate_screen = True
                self._screen_fingerprint = fingerprint
            if kind == "speech" and self.observations:
                previous = self.observations[-1]
                # Same-channel duplicate delivery is safe to coalesce; cross-channel speech retains attribution.
                if (
                    previous.kind == "speech"
                    and previous.channel == channel
                    and previous.text == text
                    and 0 <= (at if at is not None else self.clock()) - previous.at < 2
                ):
                    return None
            observation = Observation(
                id="obs-" + uuid4().hex[:12],
                kind=kind,
                text=text[:45000] if kind == "screen" else text[:10000],
                channel=channel,
                speaker=speaker,
                at=at if at is not None else self.clock(),
                confidence=confidence,
                image_path=image_path,
                reading=ScreenReading.model_validate(reading) if reading is not None else None,
            )
            self.observations.append(observation)
            cutoff = max(o.at for o in self.observations) - 300
            self.observations = [o for o in self.observations if o.kind not in {"speech", "screen"} or o.at >= cutoff]
            self.observations = self.observations[-1000:]
            if not duplicate_screen:
                self.changed(task=channel == "typed" or interrupting)
            return observation

    def integrate(self, response: Assistance, *, replace_open_questions=True, sources=None):
        with self._lock:
            sources = self.observations if sources is None else sources
            evidence = {o.id: o for o in [*self.retained_sources.values(), *sources, *self.observations]}
            self.previous_task, self.previous_summary = response.task, response.summary
            if replace_open_questions:
                self.open_questions = tuple(response.open_questions[:20])
                # Preserve the last substantive answer, including diagram nodes and
                # conversation replies. A quick acknowledgment must not replace it.
                self.previous_answer = json.dumps(response.model_dump(exclude={"observed_files"}), ensure_ascii=False)
            else:
                self.open_questions = tuple(dict.fromkeys([*self.open_questions, *response.open_questions]))[-20:]
            artifacts = []
            for item in response.artifacts:
                record = item.model_dump(exclude={"annotations"})
                if len(json.dumps([*artifacts, record])) < 30000:
                    artifacts.append(record)
            if artifacts:
                self.previous_artifacts = json.dumps(artifacts)
            for item in response.observed_files:
                if item.path in self.retired_files:
                    continue
                versions = self.files.setdefault(item.path, [])
                if not any(v.content == item.content and v.first_line == item.first_line for v in versions):
                    versions.append(item.model_copy(deep=True))
                versions.sort(key=lambda item: max((evidence[s].at for s in item.source_ids if s in evidence), default=0))
                self.files[item.path] = versions[-8:]
            if len(self.files) > 60:
                self.files = dict(list(self.files.items())[-60:])
            self.retain_sources(list(evidence.values()))

    def retain_sources(self, observations):
        needed = {sid for item in self.context_items.values() for sid in item.source_ids}
        needed.update(sid for items in self.files.values() for item in items[-3:] for sid in item.source_ids)
        sources = {**self.retained_sources, **{o.id: o for o in observations}}
        self.retained_sources = {sid: sources[sid].model_copy(deep=True) for sid in needed if sid in sources}

    def workspace_text(self) -> str:
        records = []
        for path, fragments in reversed(list(self.files.items())):
            if path in self.retired_files:
                continue
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
            now = self.clock()
            recent = [o for o in self.observations if o.kind in {"speech", "screen"} and now - 300 <= o.at <= now]
            persistent = [o for o in self.observations if o.kind not in {"speech", "screen"}][-40:]
            # Restored text remains historical context; it is never represented as
            # current capture and loading still cannot authorize image/file access.
            if self.restored:
                persistent = self.observations[-100:]
            records = {o.id: o for o in [*self.retained_sources.values(), *persistent, *recent]}
            selected, omitted, budget = [], [], 800000
            # Prioritize the entire five-minute stream over older retained sources.
            ordered = [*recent, *persistent, *self.retained_sources.values()]
            seen = set()
            for observation in ordered:
                if observation.id in seen:
                    continue
                seen.add(observation.id)
                if len(observation.text) > budget:
                    omitted.append(observation.id)
                    continue
                selected.append(records[observation.id].model_copy(deep=True))
                budget -= len(observation.text)
            selected.sort(key=lambda o: (o.at, o.id))
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
                self.open_questions,
                self.constraints,
                self.decisions,
                self.restored,
                task_revision=self.task_revision,
                evidence_revision=self.evidence_revision,
                memory_revision=self.memory_revision,
                context_items=json.dumps([x.model_dump() for x in self.context_items.values()], ensure_ascii=False),
                previous_answer=self.previous_answer,
                as_of=now,
                recent_observation_ids=tuple(o.id for o in recent if o.id not in omitted),
                omitted_observation_ids=tuple(omitted),
                context_updated_through=self.context_updated_through,
            )

    def clear(self):
        with self._lock:
            self.session_id = uuid4().hex
            self.revision += 1
            self.evidence_revision += 1
            self.task_revision += 1
            self.observations.clear()
            self.files.clear()
            self.previous_task = self.previous_summary = self._screen_fingerprint = ""
            self.previous_artifacts = "[]"
            self.open_questions = ()
            self.constraints = self.decisions = ()
            self.restored = False
            self.context_items.clear()
            self.retained_sources.clear()
            self.retired_files.clear()
            self.memory_revision = 0
            self.context_updated_through = -1
            self.previous_answer = "{}"
            if self.goal:
                self.add("task", self.goal, "typed", "primary_user")
