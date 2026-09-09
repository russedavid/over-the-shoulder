"""Explicit, versioned task checkpoints. Loading cannot authorize capture or file access."""

import json
import os
import time
from pathlib import Path
from typing import Literal

from pydantic import Field

from otsc.context import ContextStore
from otsc.models import Artifact, Assistance, ContextItem, Observation, ObservedFile, Record, safe_relative_path
from otsc.privacy import app_directory, atomic_private_write, redact

MAX_CHECKPOINT_BYTES = 8_000_000


class ContextCheckpoint(Record):
    goal: str = Field(max_length=10000)
    project_hint: str = Field(max_length=2000)
    observations: list[Observation] = Field(max_length=1000)
    files: dict[str, list[ObservedFile]]
    previous_task: str
    previous_summary: str
    open_questions: list[str]
    constraints: list[str] = Field(default_factory=list, max_length=20)
    decisions: list[str] = Field(default_factory=list, max_length=20)
    context_items: list[ContextItem] = Field(default_factory=list, max_length=80)
    retained_sources: list[Observation] = Field(default_factory=list, max_length=1000)
    retired_files: dict[str, dict] = Field(default_factory=dict)
    previous_answer: str = Field(default="{}", max_length=300000)


class SessionDocument(Record):
    version: Literal[1] = 1
    saved_at: float
    context: ContextCheckpoint
    current: Assistance | None
    history: list[Assistance] = Field(max_length=12)
    displayed_artifacts: list[Artifact]
    selected_id: str
    selected_view: str
    pinned: bool
    base_hashes: dict[str, str]
    artifact_bases: dict[str, dict[str, str]] = Field(default_factory=dict)


def checkpoint(context, coordinator, *, displayed_artifacts=(), selected_id="", selected_view="Artifact"):
    with context._lock:
        observations = [o.model_copy(update={"image_path": ""}, deep=True) for o in context.observations]
        state = ContextCheckpoint(
            goal=context.goal,
            project_hint=context.repo_root,
            observations=observations,
            files=context.files,
            previous_task=context.previous_task,
            previous_summary=context.previous_summary,
            open_questions=list(context.open_questions),
            constraints=list(context.constraints),
            decisions=list(context.decisions),
            context_items=list(context.context_items.values()),
            retained_sources=[o.model_copy(update={"image_path": ""}, deep=True) for o in context.retained_sources.values()],
            retired_files=context.retired_files,
            previous_answer=context.previous_answer,
        )
        return SessionDocument(
            saved_at=time.time(),
            context=state,
            current=coordinator.current,
            history=coordinator.history[-12:],
            displayed_artifacts=list(displayed_artifacts),
            selected_id=selected_id,
            selected_view=selected_view,
            pinned=coordinator.pinned,
            base_hashes={},
            artifact_bases=getattr(coordinator, "artifact_bases", {}),
        )


def save_session(document, path=None):
    target = (
        Path(path)
        if path
        else app_directory() / "sessions" / f"task-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1000000:06d}.json"
    )
    payload = document.model_dump_json(indent=2)
    # Field-level content was already redacted at intake. Preserve code/annotations
    # exactly instead of running a JSON-breaking regex across the serialized object.
    if len(payload.encode()) > MAX_CHECKPOINT_BYTES:
        raise ValueError("This session exceeds the 8 MB checkpoint limit")
    atomic_private_write(target, payload)
    return target


def load_session(path):
    path = Path(path)
    if path.stat().st_size > MAX_CHECKPOINT_BYTES:
        raise ValueError("Session file exceeds the 8 MB limit")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        data = stream.read(MAX_CHECKPOINT_BYTES + 1)
    if len(data) > MAX_CHECKPOINT_BYTES:
        raise ValueError("Session file exceeds the 8 MB limit")
    document = SessionDocument.model_validate_json(data)
    if len(document.context.files) > 60 or any(len(items) > 8 for items in document.context.files.values()):
        raise ValueError("Session contains too many observed file versions")
    for name, fragments in document.context.files.items():
        if not safe_relative_path(name) or any(f.path != name for f in fragments):
            raise ValueError("Session contains an invalid observed path")
    if any(
        not safe_relative_path(name) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
        for name, value in document.base_hashes.items()
    ):
        raise ValueError("Session contains invalid base hashes")
    if len(document.artifact_bases) > 256:
        raise ValueError("Session contains too many proposal bases")
    for key, base in document.artifact_bases.items():
        if (
            len(key) != 64
            or set(base) != {"path", "base_hash"}
            or not safe_relative_path(base["path"])
            or len(base["base_hash"]) != 64
        ):
            raise ValueError("Session contains an invalid proposal base")
    # Paths in an imported file are data, never permission to open a local image.
    for observation in [*document.context.observations, *document.context.retained_sources]:
        observation.image_path = ""
        observation.text = redact(observation.text)
    if not isinstance(json.loads(document.context.previous_answer), dict):
        raise ValueError("Saved previous answer must be an object")
    return document


def restore_context(document, *, authorized_project=""):
    saved = document.context
    context = ContextStore()  # Fresh session ID makes all old running jobs stale.
    context.goal = redact(saved.goal)
    context.observations = [o.model_copy(deep=True) for o in saved.observations]
    context.files = {name: [f.model_copy(deep=True) for f in fragments] for name, fragments in saved.files.items()}
    context.previous_task, context.previous_summary = saved.previous_task, saved.previous_summary
    context.open_questions = tuple(saved.open_questions[:20])
    context.constraints, context.decisions = tuple(saved.constraints), tuple(saved.decisions)
    context.previous_artifacts = json.dumps(
        [a.model_dump(exclude={"annotations"}) for a in document.displayed_artifacts]
    )
    context.repo_root = authorized_project if authorized_project and authorized_project == saved.project_hint else ""
    context.verified_files = {}  # Always re-read authorized source before treating it as current.
    context.restored = True
    context.revision = 1
    context.task_revision = context.evidence_revision = 1
    context.context_items = {item.id: item.model_copy(deep=True) for item in saved.context_items}
    context.retained_sources = {o.id: o.model_copy(deep=True) for o in saved.retained_sources}
    context.retired_files = dict(saved.retired_files)
    context.previous_answer = saved.previous_answer
    return context
