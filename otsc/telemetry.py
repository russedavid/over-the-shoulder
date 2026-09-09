"""Bounded local operational metadata. Captured content and credentials are excluded."""

import hashlib
import importlib.metadata
import json
import math
import os
import platform
import subprocess
import threading
import time
from pathlib import Path

from otsc.privacy import app_directory, private_directory, redact

FIELDS = {
    "request_id",
    "session_id",
    "revision",
    "lane",
    "provider",
    "model",
    "outcome",
    "reason",
    "elapsed_ms",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "observations",
    "files",
    "artifacts",
    "snapshot_hash",
    "prompt_hash",
    "schema_hash",
    "tool",
    "step",
    "result_hash",
    "result_chars",
    "first_output_ms",
    "artifact_hash",
    "rating",
    "source",
    "error_type",
    "inspection",
    "duration_ms",
    "steps",
    "deadline_ms",
    "limit",
    "event_count",
    "available",
    "release_id",
    "config_hash",
    "reasoning",
    "image_hash",
}


def digest(value):
    raw = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def release_manifest():
    from otsc import __version__
    from otsc.models import response_schema
    from otsc.prompts import SYSTEM

    root = Path(__file__).resolve().parents[1]
    commit, dirty = "unavailable", None
    try:
        commit = (
            subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, timeout=2)
            .decode()
            .strip()
        )
        dirty = bool(
            subprocess.check_output(
                ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], timeout=2
            )
        )
    except (OSError, subprocess.SubprocessError):
        pass
    dependencies = {}
    for name in ("pydantic", "httpx", "Pillow", "mlx-audio", "mido"):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    result = {
        "app_version": __version__,
        "git_commit": commit,
        "dirty": dirty,
        "python": platform.python_version(),
        "machine": platform.machine(),
        "prompt_hash": digest(SYSTEM),
        "schema_hash": digest(response_schema()),
        "dependencies": dependencies,
    }
    result["code_hash"] = digest(
        {str(path.relative_to(root)): digest(path.read_text()) for path in sorted((root / "otsc").glob("*.py"))}
    )
    result["release_id"] = digest(result)[:16]
    return result


class TraceStore:
    def __init__(self, directory=None, *, enabled=True, source="interactive", max_bytes=2_000_000, max_files=8):
        self.enabled, self.source = enabled, source
        self.directory = Path(directory) if directory else app_directory() / "traces"
        self.max_bytes, self.max_files = max_bytes, max_files
        self.lock = threading.Lock()
        self.last_error = ""
        self.release = release_manifest() if enabled else {}

    def record(self, event, **fields):
        if not self.enabled:
            return
        # Only known scalar metadata is eligible. Never serialize arbitrary provider objects.
        clean = {
            key: value
            for key, value in fields.items()
            if key in FIELDS
            and (value is None or isinstance(value, (bool, int, float)) or isinstance(value, str) and len(value) <= 180)
        }
        for key, value in list(clean.items()):
            if isinstance(value, float) and not math.isfinite(value):
                clean.pop(key)
                continue
            if isinstance(value, str) and ("\n" in value or "\r" in value or redact(value) != value):
                clean.pop(key)
        if not str(event).replace("_", "").isalnum() or len(str(event)) > 60:
            return
        row = {
            "version": 1,
            "at": time.time(),
            "event": str(event),
            "source": self.source,
            "release_id": self.release.get("release_id"),
            **clean,
        }
        data = (json.dumps(row, ensure_ascii=False) + "\n").encode()
        try:
            with self.lock:
                private_directory(self.directory)
                active = self.directory / "events.jsonl"
                if active.exists() and active.stat().st_size + len(data) > self.max_bytes:
                    os.replace(active, self.directory / f"events-{time.time_ns()}.jsonl")
                flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(active, flags, 0o600)
                with os.fdopen(fd, "ab") as stream:
                    stream.write(data)
                old = sorted(self.directory.glob("events-*.jsonl"), key=lambda p: p.name, reverse=True)
                for path in old[max(0, self.max_files - 1) :]:
                    path.unlink()
        except OSError as error:
            # Diagnostics must never take down capture, rendering, or inference.
            self.last_error = type(error).__name__

    def events(self):
        rows = []
        if not self.directory.exists():
            return rows
        with self.lock:
            paths = sorted(self.directory.glob("events*.jsonl"))[-self.max_files :]
            for path in paths:
                if path.is_symlink() or path.stat().st_size > self.max_bytes + 4096:
                    continue
                for line in path.read_text().splitlines():
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        return sorted(rows, key=lambda row: row.get("at", 0))

    def summary(self):
        rows = self.events()
        completed = [r for r in rows if r.get("event") == "generation_finished"]
        durations = sorted(r["elapsed_ms"] for r in completed if r.get("outcome") == "success")

        def percentile(fraction):
            return durations[min(len(durations) - 1, int((len(durations) - 1) * fraction))] if durations else None

        return {
            "release": self.release,
            "metadata_events": len(rows),
            "generations": len(completed),
            "successful": sum(r.get("outcome") == "success" for r in completed),
            "failed": sum(r.get("outcome") == "error" for r in completed),
            "cancelled": sum(r.get("outcome") == "cancelled" for r in completed),
            "p50_ms": percentile(0.50),
            "p95_ms": percentile(0.95),
            "reported_input_tokens": sum(r.get("input_tokens", 0) for r in rows if r.get("event") == "provider_usage"),
            "reported_output_tokens": sum(
                r.get("output_tokens", 0) for r in rows if r.get("event") == "provider_usage"
            ),
            "cost": "Not inferred from subscription access or unconfigured token prices.",
            "storage_error": self.last_error or None,
            "content_logging": False,
        }


class Progress:
    def __init__(self, callback, trace=None, **fields):
        self.callback, self.trace, self.fields = callback, trace, fields
        self.started = time.monotonic()
        self.first_output = False

    def __call__(self, message):
        self.callback(message)
        if str(message).startswith("Draft: ") and not self.first_output:
            self.first_output = True
            self.record("first_output", first_output_ms=round((time.monotonic() - self.started) * 1000))

    def record(self, event, **fields):
        if self.trace:
            self.trace.record(event, **{**self.fields, **fields})


def record_progress(progress, event, **fields):
    if hasattr(progress, "record"):
        progress.record(event, **fields)
