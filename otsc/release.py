"""Prepare a source-only review archive without publishing historical captures."""

import json
import subprocess
import time
import zipfile
from pathlib import Path

from otsc.privacy import SECRET_PATTERNS, app_directory
from otsc.telemetry import digest, release_manifest


def export_source(output=None):
    root = Path(__file__).resolve().parents[1]
    manifest = release_manifest()
    destination = (
        Path(output) if output else app_directory() / "releases" / ("otsc-source-" + manifest["release_id"] + ".zip")
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    names = (
        subprocess.check_output(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
        )
        .decode()
        .split("\0")
    )
    allowed_roots = {"otsc", "evals", "tests", "docs", ".github"}
    allowed_files = {
        "ots.py",
        "test.py",
        "lib/main.py",
        "README.md",
        "AGENTS.md",
        "pyproject.toml",
        "uv.lock",
        ".gitignore",
    }
    extensions = {".py", ".md", ".json", ".jsonl", ".csv", ".html", ".css", ".js", ".svg", ".yml", ".yaml", ".txt"}
    sources = {}
    for name in sorted(set(names)):
        if not name:
            continue
        relative = Path(name)
        path = root / relative
        if name not in allowed_files and not (relative.parts[0] in allowed_roots and relative.suffix in extensions):
            continue
        if any(part in {"runs", "runtime", "captures", "recordings", "__pycache__"} for part in relative.parts):
            continue
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
            continue
        data = path.read_bytes()
        text = data.decode("utf-8")
        if SECRET_PATTERNS[0].search(text):
            raise ValueError("A potential credential was found in source: " + name)
        sources[name] = data
    manifest.update(
        {
            "kind": "source-only review archive",
            "published": False,
            "history_included": False,
            "generated_at": time.time(),
            "files": {name: digest(data.decode()) for name, data in sources.items()},
        }
    )
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sources.items():
            archive.writestr("over-the-shoulder-coder/" + name, data)
        archive.writestr("over-the-shoulder-coder/RELEASE-MANIFEST.json", json.dumps(manifest, indent=2))
    return destination
