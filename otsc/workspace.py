"""Read bounded source snapshots from an explicitly selected folder; never edit it."""

import difflib
import json
import os
import re
import stat
import subprocess
from pathlib import Path

from otsc.models import Assistance, LineAnnotation, patch_added_lines, safe_relative_path
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
    """The host computes verified diffs from complete replacements and the exact input snapshot."""
    fragment_diffs = []
    for artifact in list(response.artifacts):
        if artifact.kind == "patch" and artifact.basis == "verified_file":
            raise ValueError("Return complete annotated replacement code; the app computes the verified diff")
        if artifact.kind == "code" and artifact.basis == "observed_fragment":
            fragment = next(
                (
                    f
                    for f in response.observed_files
                    if f.path == artifact.path and set(f.source_ids) <= set(artifact.source_ids)
                ),
                None,
            )
            if fragment and fragment.content != artifact.content:
                before = fragment.content.splitlines(keepends=True)
                after = artifact.content.splitlines(keepends=True)
                raw = difflib.unified_diff(before, after, fromfile="a/" + fragment.path, tofile="b/" + fragment.path)
                excerpt = "".join(
                    line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in raw
                )
                offset = (fragment.first_line or 1) - 1
                if offset:
                    excerpt = re.sub(
                        r"(?m)^@@ -(\d+)(,\d+)? \+(\d+)(,\d+)? @@",
                        lambda m: f"@@ -{int(m[1]) + offset}{m[2] or ''} +{int(m[3]) + offset}{m[4] or ''} @@",
                        excerpt,
                    )
                notes = {n.line + offset: n.explanation for n in artifact.annotations}
                from otsc.models import Artifact

                fragment_diffs.append(
                    Artifact(
                        id=artifact.id + "-observed-diff",
                        kind="patch",
                        title="Changes to observed excerpt"
                        + (" (excerpt-relative lines)" if fragment.first_line is None else ""),
                        content=excerpt,
                        language=artifact.language,
                        path=artifact.path,
                        basis="observed_fragment",
                        source_ids=artifact.source_ids,
                        annotations=[
                            LineAnnotation(line=i, explanation=notes[i]) for i in sorted(patch_added_lines(excerpt))
                        ],
                        nodes=[],
                        edges=[],
                    )
                )
        if artifact.kind != "code" or artifact.basis != "verified_file":
            continue
        if artifact.path not in files:
            raise ValueError("Replacement refers to a file outside the verified snapshot")
        before, after = files[artifact.path], artifact.content
        if before == after:
            artifact.basis = "example"
            continue
        # A final newline avoids malformed concatenated diff headers/lines.
        before_lines = before.splitlines(keepends=True)
        after_lines = after.splitlines(keepends=True)
        raw = list(
            difflib.unified_diff(before_lines, after_lines, fromfile="a/" + artifact.path, tofile="b/" + artifact.path)
        )
        patch = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in raw)
        notes = {n.line: n.explanation for n in artifact.annotations}
        replacement = artifact.model_dump()
        replacement.update(
            kind="patch",
            content=patch,
            annotations=[
                LineAnnotation(line=i, explanation=notes[i]).model_dump() for i in sorted(patch_added_lines(patch))
            ],
        )
        from otsc.models import Artifact

        artifact_index = response.artifacts.index(artifact)
        response.artifacts[artifact_index] = Artifact.model_validate(replacement)
    response.artifacts.extend(fragment_diffs)
    return response


def verified_from_snapshot(snapshot):
    return json.loads(snapshot.verified_files)
