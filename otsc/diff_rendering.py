"""Deterministic text comparison and numbered diff rows; never applies a change."""

import difflib
import re
from dataclasses import dataclass, field

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)$")


@dataclass(frozen=True)
class CodeRow:
    text: str
    kind: str = "context"
    old_line: int | None = None
    new_line: int | None = None
    marker: str = ""


@dataclass
class CodeDocument:
    rows: list[CodeRow] = field(default_factory=list)
    diff: bool = False
    first_line: int | None = 1

    @property
    def text(self):
        return "\n".join(row.text for row in self.rows)

    @property
    def added(self):
        return sum(row.kind == "add" for row in self.rows)

    @property
    def removed(self):
        return sum(row.kind == "remove" for row in self.rows)


def unified_patch(before, after, path, first_line=1):
    """Compare supplied bytes and retain real newlines and excerpt coordinates."""
    raw = difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                               fromfile="a/" + path, tofile="b/" + path)
    result = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in raw)
    offset = (first_line or 1) - 1
    if offset:
        result = re.sub(r"(?m)^@@ -(\d+)(,\d+)? \+(\d+)(,\d+)? @@",
                        lambda m: f"@@ -{int(m[1]) + offset}{m[2] or ''} +{int(m[3]) + offset}{m[4] or ''} @@", result)
    return result


def parse_diff(text, *, path=None, first_line=1):
    """Parse one unified file diff, checking both hunk counts and line coordinates."""
    rows = []
    old = new = 0
    old_left = new_left = 0
    in_hunk = False
    previous_old_end = previous_new_end = -1
    headers = []
    for line in text.splitlines():
        match = HUNK.match(line)
        if match:
            if old_left or new_left:
                raise ValueError("Diff hunk counts do not match its lines")
            old, new = int(match[1]), int(match[3])
            old_left = int(match[2]) if match[2] is not None else 1
            new_left = int(match[4]) if match[4] is not None else 1
            if old < previous_old_end or new < previous_new_end:
                raise ValueError("Diff hunks overlap or run backwards")
            previous_old_end, previous_new_end = old + old_left, new + new_left
            if old_left and old < 1 or new_left and new < 1:
                raise ValueError("Nonempty diff ranges need positive line numbers")
            in_hunk = True
            rows.append(CodeRow(line, "hunk"))
        elif line == "\\ No newline at end of file":
            rows.append(CodeRow(line, "note"))
        elif not in_hunk and line.startswith(("--- ", "+++ ")):
            headers.append(line)
        elif in_hunk and line.startswith(" "):
            if not old_left or not new_left:
                raise ValueError("Context line exceeds the diff range")
            rows.append(CodeRow(line[1:], "context", old, new))
            old += 1
            new += 1
            old_left -= 1
            new_left -= 1
        elif in_hunk and line.startswith("-"):
            if not old_left:
                raise ValueError("Removed line exceeds the diff range")
            rows.append(CodeRow(line[1:], "remove", old_line=old, marker="−"))
            old += 1
            old_left -= 1
        elif in_hunk and line.startswith("+"):
            if not new_left:
                raise ValueError("Added line exceeds the diff range")
            rows.append(CodeRow(line[1:], "add", new_line=new, marker="+"))
            new += 1
            new_left -= 1
        else:
            raise ValueError("Unsupported or malformed unified diff")
    if not in_hunk or old_left or new_left:
        raise ValueError("Incomplete diff")
    if path is not None and headers != ["--- a/" + path, "+++ b/" + path]:
        raise ValueError("Diff headers do not match the target file")
    return CodeDocument(rows=rows, diff=True, first_line=first_line)


def render_diff(before, after, *, path, first_line=1):
    """Rendering tool for anchored proposals: canonical patch plus numbered rows."""
    patch = unified_patch(before, after, path, first_line)
    return patch, parse_diff(patch, path=path, first_line=first_line) if patch else CodeDocument(diff=True, first_line=first_line)
