"""One response contract for task, conversation, and artifact assistance."""

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def safe_relative_path(path: str) -> bool:
    p = PurePosixPath(path.replace("\\", "/"))
    return (
        bool(path) and not p.is_absolute() and ".." not in p.parts and not path.startswith("~") and "\x00" not in path
    )


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VisualFact(Record):
    description: str
    evidence: str
    certainty: str


class ScreenReading(Record):
    visible_text: str = Field(max_length=45000)
    facts: list[VisualFact] = Field(max_length=30)
    inferred_task: str
    uncertainties: list[str] = Field(max_length=20)
    important_details: list[str] = Field(max_length=20)


class Observation(Record):
    id: str
    kind: Literal["task", "screen", "speech", "note", "file"]
    text: str
    channel: Literal["screen", "microphone", "system", "typed", "filesystem"]
    speaker: Literal["primary_user", "other_people", "uncertain", "not_applicable"]
    at: float
    confidence: Literal["direct", "transcribed", "uncertain"] = "direct"
    image_path: str = ""
    reading: ScreenReading | None = None


class LineAnnotation(Record):
    line: int = Field(ge=1)
    explanation: str = Field(min_length=1)


class DiagramNode(Record):
    id: str
    label: str


class DiagramEdge(Record):
    source: str
    target: str
    label: str


class ArtifactContent(Record):
    id: str
    kind: Literal["code", "patch", "diagram", "explanation", "checklist", "structured", "image"]
    title: str
    content: str
    language: str
    path: str
    basis: Literal["example", "observed_fragment", "verified_file", "discussion"]
    source_ids: list[str]
    annotations: list[LineAnnotation]
    nodes: list[DiagramNode]
    edges: list[DiagramEdge]

    @model_validator(mode="after")
    def validate_artifact(self):
        if not self.id.strip() or not self.title.strip():
            raise ValueError("Artifacts need an identity and title")
        if self.path and not safe_relative_path(self.path):
            raise ValueError("Artifact path must stay inside its workspace")
        if self.kind == "code":
            if not self.content.strip():
                raise ValueError("Code content is empty")
        if self.kind == "patch":
            if not self.path or not re.search(r"^@@ -", self.content, re.M):
                raise ValueError("A patch needs a path and a unified-diff hunk")
        if self.kind == "diagram":
            ids = {n.id for n in self.nodes}
            if not self.nodes or len(ids) != len(self.nodes) or len(ids) > 24:
                raise ValueError("A diagram needs 1–24 uniquely identified nodes")
            if len(self.edges) > 48 or any(e.source not in ids or e.target not in ids for e in self.edges):
                raise ValueError("Diagram edges must reference its nodes")
        return self


class Artifact(ArtifactContent):
    @model_validator(mode="after")
    def validate_annotations(self):
        annotation_lines = [a.line for a in self.annotations]
        if len(annotation_lines) != len(set(annotation_lines)):
            raise ValueError("Duplicate line explanations")
        if self.kind == "code":
            lines = self.content.splitlines()
            required = {i for i, line in enumerate(lines, 1) if line.strip()}
            if not required <= set(annotation_lines):
                raise ValueError("Every nonblank code line needs its own teaching explanation")
            if any(line > len(lines) for line in annotation_lines):
                raise ValueError("Line explanations exceed the actual code and may be misaligned")
        if self.kind == "patch" and not patch_added_lines(self.content, include_blank=False) <= set(annotation_lines):
            raise ValueError("Every added code line needs an explanation indexed by its new-file line")
        return self

    def clean_text(self) -> str:
        return self.content

    def annotated_text(self) -> str:
        if self.kind == "structured":
            from otsc.planning import structured_text

            try:
                return structured_text(json.loads(self.content))
            except (ValueError, RecursionError):
                return self.content
        if self.kind == "image":
            try:
                data = json.loads(self.content)
                state = {"pending": "Generating image…", "ready": "Generated image", "failed": "Image generation failed"}.get(data.get("status"), "Image")
                return "\n\n".join(str(value) for value in (state, data.get("caption"), data.get("error"), data.get("prompt")) if value)
            except (ValueError, AttributeError):
                return self.content
        notes = {a.line: a.explanation for a in self.annotations}
        if self.kind == "code":
            return "\n\n".join(
                f"{i:>3}  {line}" + ("\n     " + notes[i] if i in notes else "")
                for i, line in enumerate(self.content.splitlines(), 1)
            )
        if self.kind == "patch":
            out, new_line = [], 0
            for line in self.content.splitlines():
                match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                if match:
                    new_line = int(match.group(1))
                out.append(line)
                if line.startswith("+") and not line.startswith("+++"):
                    if new_line in notes:
                        out.append("    → " + notes[new_line])
                    new_line += 1
                elif line.startswith(" "):
                    new_line += 1
            return "\n".join(out)
        return self.content


def patch_added_lines(patch: str, *, include_blank=True) -> set[int]:
    added, new_line, in_hunk = set(), 0, False
    for line in patch.splitlines():
        match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if match:
            new_line, in_hunk = int(match.group(1)), True
        elif in_hunk and line.startswith("+") and not line.startswith("+++"):
            if include_blank or line[1:].strip():
                added.add(new_line)
            new_line += 1
        elif in_hunk and line.startswith(" "):
            new_line += 1
        elif line.startswith(("--- ", "+++ ")):
            in_hunk = False
    return added


class ConversationResponse(Record):
    source_id: str
    action: Literal["answer", "acknowledge", "challenge", "clarify", "defer"]
    text: str
    artifact_effect: str


class ObservedFile(Record):
    path: str
    content: str
    first_line: int | None
    source_ids: list[str]
    confidence: Literal["high", "medium", "low"]


class ContextItem(Record):
    id: str = Field(min_length=1, max_length=100)
    kind: Literal["task", "requirement", "decision", "progress", "question", "hypothesis"]
    text: str = Field(min_length=1, max_length=2000)
    source_ids: list[str] = Field(min_length=1, max_length=8)
    basis: Literal["observed", "reported", "inferred"]


class ContextRemoval(Record):
    id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    source_ids: list[str] = Field(min_length=1, max_length=8)


class NoAnswerUpdate(Record):
    """A host-side decision to retain the last answer; never a new artifact."""

    reason: str = Field(min_length=1, max_length=3000)


class Assistance(Record):
    task: str
    summary: str
    conversation: list[ConversationResponse]
    artifacts: list[Artifact]
    observed_files: list[ObservedFile]
    open_questions: list[str]
    # Host diagnostics are not a model-output field and do not confer evidence authority.
    _delivery_notes: list[dict] = PrivateAttr(default_factory=list)
    _task_plan: dict | None = PrivateAttr(default=None)
    _refresh_reason: str = PrivateAttr(default="")

    def validate_sources(self, observations: list[Observation], verified_files: dict[str, str] | None = None):
        ids = {o.id for o in observations}
        observations_by_id = {o.id: o for o in observations}
        artifact_ids = [a.id for a in self.artifacts]
        if len(set(artifact_ids)) != len(artifact_ids):
            raise ValueError("Artifact IDs must be unique within a response")
        for item in self.conversation:
            if item.source_id not in ids:
                raise ValueError("Conversation response cites an unknown observation")
        for item in [*self.artifacts, *self.observed_files]:
            if not item.source_ids or not set(item.source_ids) <= ids:
                raise ValueError("Output needs evidence from this context snapshot")
        for item in self.observed_files:
            if not safe_relative_path(item.path) or not item.content.strip():
                raise ValueError("Observed file needs a safe path and visible content")
            if item.first_line is not None and item.first_line < 1:
                raise ValueError("Observed line positions must be positive")
            sources = [observations_by_id[s] for s in item.source_ids]
            if any(o.kind not in {"screen", "file"} for o in sources):
                raise ValueError("A spoken description is not an observed source file")
            visible = " ".join(" ".join(o.text.split()) for o in sources)
            if item.path not in visible:
                raise ValueError("An observed file path must be visible in its cited observations")
            if " ".join(item.content.split()) not in visible:
                raise ValueError("Observed file content includes text absent from its cited observations")
        for artifact in self.artifacts:
            if artifact.basis == "verified_file" and artifact.path not in (verified_files or {}):
                raise ValueError("This file was not verified in the connected project")
        return self


class QuickAssistance(Record):
    task: str
    summary: str
    conversation: list[ConversationResponse] = Field(max_length=1)
    open_questions: list[str] = Field(max_length=1)


def normalize_optional_file_metadata(response, observations, verified_files, progress=None):
    """Keep valid proposals when redundant file metadata has no valid file identity.

    This only omits entries whose content is already corroborated by supplied
    evidence. Unknown paths, invented contents, and unknown citations still fail.
    """
    from otsc.telemetry import record_progress

    sources = {o.id: o for o in observations}
    retained = []
    for item in response.observed_files:
        known = bool(item.source_ids) and set(item.source_ids) <= set(sources)
        cited = [sources[key] for key in item.source_ids] if known else []
        visual = bool(cited) and all(o.kind in {"screen", "file"} for o in cited)
        visible = " ".join(" ".join(o.text.split()) for o in cited)
        content = " ".join(item.content.split())
        reason = None
        if not item.path and visual and content and content in visible:
            reason = "unnamed_fragment_already_in_observations"
        elif known and not visual and item.path in verified_files and item.content == verified_files[item.path]:
            reason = "verified_file_already_supplied_separately"
        if reason:
            record_progress(progress, "file_metadata_omitted", reason=reason)
        else:
            retained.append(item)
    response.observed_files = retained
    return response


def response_schema(lane="deep") -> dict:
    return (QuickAssistance if lane == "quick" else Assistance).model_json_schema()


def parse_response(text: str, lane="deep") -> Assistance:
    data = decode_json(text)
    if lane == "quick":
        quick = QuickAssistance.model_validate(data)
        return Assistance(**quick.model_dump(), artifacts=[], observed_files=[])
    return Assistance.model_validate(data)


def decode_json(text: str) -> dict:
    # Accept a single fenced JSON object, not arbitrary prose containing convenient braces.
    text = text.strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Expected one structured response object")
    return data
