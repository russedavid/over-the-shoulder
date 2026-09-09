"""Redaction and bounded, private runtime storage."""

import os
import re
import tempfile
from pathlib import Path

SECRET_PATTERNS = (
    re.compile(
        r"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}|gsk_[A-Za-z0-9_-]{16,}|AIza[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,})"
    ),
    re.compile(r"(?im)((?:api[_ -]?key|access[_ -]?token|password|secret)\s*[=:]\s*)[^\s,;]+"),
)


def redact(text: str) -> str:
    text = SECRET_PATTERNS[0].sub("[REDACTED]", str(text))
    return SECRET_PATTERNS[1].sub(lambda m: m.group(1) + "[REDACTED]", text)


def private_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def app_directory() -> Path:
    override = os.getenv("OTSC_DATA_DIR")
    return private_directory(
        Path(override).expanduser() if override else Path.home() / "Library/Application Support/Over The Shoulder Coder"
    )


def private_write(path: Path, data: str | bytes) -> None:
    private_directory(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data.encode() if isinstance(data, str) else data)
    path.chmod(0o600)


def atomic_private_write(path: Path, data: str | bytes) -> None:
    """Replace an app-owned checkpoint only after its complete contents reach disk."""
    private_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".otsc-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data.encode() if isinstance(data, str) else data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        Path(temporary).unlink(missing_ok=True)
