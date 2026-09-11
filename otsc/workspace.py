"""Read bounded source snapshots from an explicitly selected folder; never edit it."""

import json
import os
import stat
import subprocess
from pathlib import Path

from otsc.models import Assistance, LineAnnotation, safe_relative_path
from otsc.privacy import private_write, redact

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".next",
    "dist",
    "build",
    "captures",
    "screenshots",
    "recordings",
    "audio",
    "runtime",
    ".secrets",
    ".ssh",
    ".aws",
}
SOURCE_SUFFIXES = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".cs",
    ".rb",
    ".swift",
    ".kt",
    ".scala",
    ".sql",
    ".tf",
    ".html",
    ".css",
    ".scss",
    ".md",
    ".txt",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".sh",
    ".xml",
    ".vue",
    ".svelte",
    ".ex",
    ".exs",
}


def allowed_path(relative: str) -> bool:
    if not safe_relative_path(relative):
        return False
    path = Path(relative)
    lower = path.name.lower()
    if any(part in SKIP_DIRS or part.startswith("pair_session_") for part in path.parts):
        return False
    if lower.startswith((".env", "credentials", "secrets", "capture_", "screenshot_")):
        return False
    if lower in {"auth.json", "config.json", "package-lock.json", "yarn.lock", "uv.lock"}:
        return False
    return path.suffix.lower() in SOURCE_SUFFIXES or lower in {"dockerfile", "makefile", "justfile"}


def read_project(root: str, *, max_files=30, max_bytes=180000, focus="") -> tuple[dict[str, str], str]:
    base = Path(root).expanduser().resolve(strict=True)
    if not base.is_dir():
        raise ValueError("Select a project folder")
    try:
        result = subprocess.run(
            ["git", "-C", str(base), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            timeout=5,
            check=True,
        )
        names = result.stdout.decode().split("\0")
    except (subprocess.SubprocessError, FileNotFoundError):
        names = []
        for directory, dirs, filenames in os.walk(base, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            names.extend(str((Path(directory) / f).relative_to(base)) for f in filenames)
            if len(names) > 5000:
                break
    candidates = sorted(set(n for n in names if allowed_path(n)))
    words = set(focus.lower().split())
    candidates.sort(key=lambda p: (-sum(w in p.lower() for w in words if len(w) > 2), len(Path(p).parts), p))
    files, size, skipped = {}, 0, 0
    for name in candidates:
        if len(files) >= max_files:
            break
        path = base / name
        if any(part.is_symlink() for part in [path, *list(path.parents)[: len(Path(name).parts) - 1]]):
            skipped += 1
            continue
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 40000:
                    skipped += 1
                    continue
                data = handle.read(40001)
            text = data.decode("utf-8")
            if "\0" in text or redact(text) != text or size + len(data) > max_bytes:
                skipped += 1
                continue
            files[name] = text
            size += len(data)
        except (OSError, UnicodeError):
            skipped += 1
    return (
        files,
        f"{len(files)} source files verified; {max(0, len(candidates) - len(files))} omitted by filters or size limits.",
    )


def materialize_snapshot(directory: Path, files: dict[str, str]):
    for name, content in files.items():
        if not allowed_path(name):
            raise ValueError("Unsafe snapshot path")
        private_write(directory / name, content)


def derive_patches(response: Assistance, files: dict[str, str]) -> Assistance:
    """Call the diff renderer with a verified file or one source-backed excerpt."""
    from otsc.diff_rendering import render_diff
    from otsc.models import Artifact, DiffContext

    companions = []
    for index, artifact in enumerate(list(response.artifacts)):
        if artifact.kind == "patch" and artifact.basis == "verified_file":
            raise ValueError("Return complete replacement code; the host computes the verified diff")
        if artifact.kind != "code" or artifact.basis not in {"verified_file", "observed_fragment"}:
            continue
        first_line = 1
        if artifact.basis == "verified_file":
            if artifact.path not in files:
                raise ValueError("Replacement refers to a file outside the verified snapshot")
            before = files[artifact.path]
        else:
            matches = [fragment for fragment in response.observed_files if fragment.path == artifact.path
                       and set(fragment.source_ids) <= set(artifact.source_ids)]
            distinct = {(fragment.content, fragment.first_line) for fragment in matches}
            if len(distinct) != 1:
                continue  # Never guess which of several partial regions is the base.
            fragment = matches[0]
            before, first_line = fragment.content, fragment.first_line
        if before == artifact.content:
            continue
        patch, document = render_diff(before, artifact.content, path=artifact.path, first_line=first_line)
        offset = (first_line or 1) - 1
        notes = {note.line + offset: note.explanation for note in artifact.annotations}
        added = {row.new_line for row in document.rows if row.kind == "add"}
        data = artifact.model_dump()
        data.update(kind="patch", content=patch,
                    annotations=[LineAnnotation(line=line, explanation=notes[line]).model_dump()
                                 for line in sorted(added) if line in notes],
                    diff_context=DiffContext(before=before, after=artifact.content, first_line=first_line,
                                             annotations=artifact.annotations).model_dump())
        if artifact.basis == "observed_fragment":
            data.update(id=artifact.id + "-observed-diff", title="Changes to observed excerpt" +
                        (" (excerpt-relative lines)" if first_line is None else ""))
            companions.append(Artifact.model_validate(data))
        else:
            response.artifacts[index] = Artifact.model_validate(data)
    response.artifacts.extend(companions)
    return response


def cached_diff_base(snapshot, artifact):
    """Resolve a literal cached excerpt without treating an inferred repo as a real one."""
    from otsc.models import ObservedFile

    candidates = []
    try:
        workspace = json.loads(snapshot.workspace)
        for record in workspace:
            if record.get("path") != artifact.path:
                continue
            for raw in record.get("fragments", []):
                try:
                    fragment = ObservedFile.model_validate(raw)
                    if fragment.path != artifact.path:
                        continue
                    Assistance(task="", summary="", artifacts=[], conversation=[], observed_files=[fragment], open_questions=[]).validate_sources(
                        snapshot.observations, json.loads(snapshot.verified_files))
                    candidates.append(fragment)
                except ValueError:
                    continue
    except (ValueError, TypeError, AttributeError):
        return None
    cited = [fragment for fragment in candidates if set(fragment.source_ids) <= set(artifact.source_ids)]
    candidates = cited or candidates
    if len({(fragment.content, fragment.first_line) for fragment in candidates}) != 1:
        return None
    return candidates[-1] if candidates else None


def verified_from_snapshot(snapshot):
    return json.loads(snapshot.verified_files)
