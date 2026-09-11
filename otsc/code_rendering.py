"""Conventional source comments and editor rows, separate from canonical code."""

import bisect
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import PurePosixPath

from pygments.lexers import get_lexer_by_name, get_lexer_for_filename
from pygments.token import Comment, String
from pygments.util import ClassNotFound

from otsc.diff_rendering import CodeDocument, CodeRow, parse_diff

STYLES = {
    **dict.fromkeys(("python", "py", "python3", "bash", "sh", "shell", "zsh", "ruby", "rb", "perl", "r", "yaml", "yml", "toml", "dockerfile", "makefile", "powershell", "ps1", "terraform", "hcl"), ("#", "")),
    **dict.fromkeys(("javascript", "js", "typescript", "ts", "java", "c", "cpp", "c++", "csharp", "c#", "cs", "go", "golang", "rust", "rs", "swift", "kotlin", "kt", "scala", "dart", "php", "jsonc"), ("//", "")),
    **dict.fromkeys(("sql", "postgresql", "postgres", "mysql", "lua", "haskell", "hs", "elm"), ("--", "")),
    **dict.fromkeys(("css", "scss", "sass"), ("/*", "*/")),
    **dict.fromkeys(("html", "xml", "svg"), ("<!--", "-->")),
    "matlab": ("%", ""), "elixir": ("#", ""), "erlang": ("%", ""),
    "clojure": (";", ""), "lisp": (";", ""), "scheme": (";", ""), "ini": (";", ""),
    "ocaml": ("(*", "*)"), "fsharp": ("//", ""), "vbnet": ("'", ""),
}
ALIASES = {"py": "python", "python3": "python", "js": "javascript", "ts": "typescript", "shell": "bash",
           "sh": "bash", "zsh": "bash", "rs": "rust", "golang": "go", "c++": "cpp", "cs": "csharp",
           "c#": "csharp", "yml": "yaml", "rb": "ruby", "hs": "haskell", "kt": "kotlin", "ps1": "powershell"}


@lru_cache(maxsize=64)
def language_info(language, path=""):
    name = language.strip().lower()
    name = ALIASES.get(name, name)
    style = STYLES.get(name)
    try:
        lexer = get_lexer_by_name(name, stripnl=False, ensurenl=False) if name else get_lexer_for_filename(path, stripnl=False, ensurenl=False)
        if not name:
            name = lexer.aliases[0]
            style = next((STYLES[key] for key in lexer.aliases if key in STYLES), None)
    except ClassNotFound:
        lexer = None
        if not name:
            name = PurePosixPath(path).suffix.lstrip(".").lower()
            style = STYLES.get(name)
    return name, style, lexer


def comment_lines(explanation, style, indent=""):
    if not style:
        return []
    start, end = style
    lines = explanation.splitlines() or [explanation]
    result = []
    for text in lines:
        text = text.strip()
        if end:
            text = text.replace(end, " ".join(end))
        if start == "<!--":
            text = text.replace("--", "- -")
        # C-family preprocessing can splice a // comment into the next line.
        if text.endswith("\\"):
            text += "."
        result.append(indent + start + (" " + text if text else "") + (" " + end if end else ""))
    return result


def comment_anchors(code, language, lexer):
    """Place notes outside multiline literals/comments and explicit continuations."""
    lines = code.splitlines(keepends=True)
    starts = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line)
    anchors = list(range(len(lines)))

    def protect(start, end):
        if not starts:
            return
        first = bisect.bisect_right(starts, start) - 1
        for line in range(first + 1, bisect.bisect_left(starts, end)):
            anchors[line] = min(anchors[line], first)

    if lexer:
        run_start = run_end = None
        for index, token, value in lexer.get_tokens_unprocessed(code):
            protected = token in String or token in Comment.Multiline
            if protected and (run_end is None or index == run_end):
                run_start = index if run_start is None else run_start
                run_end = index + len(value)
            else:
                if run_start is not None:
                    protect(run_start, run_end)
                run_start, run_end = (index, index + len(value)) if protected else (None, None)
        if run_start is not None:
            protect(run_start, run_end)
    for line in range(1, len(lines)):
        if lines[line - 1].rstrip("\r\n").endswith("\\"):
            anchors[line] = min(anchors[line], line - 1)
    if language in {"html", "xml", "svg"}:
        # Comments cannot be inserted in the middle of a multiline start tag.
        for match in re.finditer(r'''<(?:[^>"']|"[^"]*"|'[^']*')*>''', code):
            protect(match.start(), match.end())
    for line, anchor in enumerate(anchors):
        while anchor > anchors[anchor]:
            anchor = anchors[anchor]
        anchors[line] = anchor
    # Keep executable shebangs and Python encoding declarations in their original positions.
    headers = 1 if lines and lines[0].startswith("#!") else 0
    if language == "xml":
        declaration = re.match(r"<\?xml\b.*?\?>", code, re.S)
        if declaration:
            headers = code[:declaration.end()].count("\n") + 1
    if language == "python":
        for line in range(min(2, len(lines))):
            if re.match(r"\s*#.*coding[:=]\s*[-\w.]+", lines[line]):
                headers = max(headers, line + 1)
    for line in range(headers):
        anchors[line] = headers
    return anchors


def code_document(code, annotations=(), *, language="", path="", first_line=1, comments=True):
    name, style, lexer = language_info(language, path)
    notes = {note.line - 1: note.explanation for note in annotations} if comments and style else {}
    lines = code.splitlines()
    anchors = comment_anchors(code, name, lexer) if notes else list(range(len(lines)))
    inserted = defaultdict(list)
    for line, explanation in notes.items():
        if 0 <= line < len(lines):
            anchor = anchors[line]
            if anchor != line:
                explanation = f"Line {line + (first_line or 1)}: " + explanation
            indent = re.match(r"[ \t]*", lines[anchor] if anchor < len(lines) else "")[0]
            inserted[anchor].extend(CodeRow(text, "comment") for text in comment_lines(explanation, style, indent))
    rows = []
    for line, text in enumerate(lines):
        rows.extend(inserted[line])
        rows.append(CodeRow(text, new_line=line + (first_line or 1)))
    rows.extend(inserted[len(lines)])
    return CodeDocument(rows=rows, first_line=first_line)


def commented_code(code, annotations=(), *, language="", path="", first_line=1):
    """Copyable source with ordinary comments; clean source is never modified."""
    if not language_info(language, path)[1]:
        return code
    document = code_document(code, annotations, language=language, path=path, first_line=first_line)
    newline = "\r\n" if "\r\n" in code and "\n" not in code.replace("\r\n", "") else "\n"
    result = newline.join(row.text for row in document.rows)
    if code.endswith(("\n", "\r")) and result:
        result += newline
    return result


def artifact_document(artifact, *, comments=True, view="diff"):
    context = artifact.diff_context
    if artifact.kind == "code":
        return code_document(artifact.content, artifact.annotations, language=artifact.language, path=artifact.path,
                             first_line=None if artifact.basis == "observed_fragment" else 1, comments=comments)
    first_line = context.first_line if context else None if artifact.basis == "observed_fragment" else 1
    if context and view in {"current", "proposed"}:
        return code_document(context.before if view == "current" else context.after,
                             context.annotations if view == "proposed" else (), language=artifact.language, path=artifact.path,
                             first_line=first_line, comments=comments and view == "proposed")
    document = parse_diff(artifact.content, path=artifact.path, first_line=first_line)
    if comments:
        style = language_info(artifact.language, artifact.path)[1]
        notes = {note.line: note.explanation for note in artifact.annotations}
        rows = []
        for row in document.rows:
            if row.kind == "add" and row.new_line in notes:
                indent = re.match(r"[ \t]*", row.text)[0]
                rows.extend(CodeRow(text, "comment") for text in comment_lines(notes[row.new_line], style, indent))
            rows.append(row)
        document.rows = rows
    return document
