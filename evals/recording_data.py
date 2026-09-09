"""Private, reproducible indexing of recorded inputs. No inference on import."""

import fcntl
import hashlib
import json
import os
import re
import wave
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from otsc.privacy import app_directory, atomic_private_write

VERSION = "recordings-2026-09-09.1"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def default_directory():
    return app_directory() / "evaluations" / VERSION


def read_json(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).exists() else default


def write_json(path, value):
    atomic_private_write(Path(path), json.dumps(value, indent=2, ensure_ascii=False))


def merge_json(path, changes):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = read_json(path, {})
        value.update(changes)
        write_json(path, value)
    return value


def file_hash(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def asset_record(path, source):
    relative = str(path.relative_to(source))
    fingerprint = file_hash(path)
    return {
        "id": "asset-" + hashlib.sha256((relative + fingerprint).encode()).hexdigest()[:16],
        "path": relative,
        "sha256": fingerprint,
        "bytes": path.stat().st_size,
    }


def seconds_between(value, origin):
    return max(0.0, (datetime.strptime(value, "%Y-%m-%d %H:%M:%S") - origin).total_seconds())


def image_time(path, origin):
    match = re.search(r"(?:capture|screenshot)_(\d{8})_(\d{6})_(\d{3})", path.stem)
    if not match:
        return None
    value = datetime.strptime(match[1] + match[2], "%Y%m%d%H%M%S")
    return max(0.0, (value - origin).total_seconds() + int(match[3]) / 1000)


def build_index(source, output=None):
    source = Path(source).resolve()
    output = Path(output or default_directory()).resolve()
    if output.is_relative_to(source):
        raise ValueError("Recorded data and evaluation output must remain outside the source checkout")
    sessions, assets = [], {}
    for folder in sorted((source / "lib").glob("*pair_session_*")):
        profile = folder / "pair_profile.jsonl"
        if not folder.is_dir() or folder.is_symlink() or not profile.exists():
            continue
        records = [json.loads(line) for line in profile.read_text().splitlines() if line.strip()]
        origin = datetime.strptime(records[0]["timestamp"], "%Y-%m-%d %H:%M:%S")
        session = {
            "id": folder.name,
            "source_folder": str(folder.relative_to(source)),
            "started": records[0]["timestamp"],
            "audio": [],
            "screens": [],
            "legacy": [],
            "checkpoints": [],
            "timing": "Relative event times; audio interval estimates are not sample-accurate.",
            "split": "discovery",
            "human_reviewed": False,
        }
        writes = {}
        foreign = 0
        requests = []
        for record in records:
            at = seconds_between(record["timestamp"], origin)
            if record["event"] == "codex_pair_prompt_build":
                requests.append(at)
            if record["event"] in {"audio_write_mic", "audio_write_system"}:
                path = Path(record["audio_path"])
                if path.parent.name == folder.name:
                    writes[path.name] = at
                else:
                    foreign += 1
        for path in sorted(folder.glob("audio_*.wav")):
            if path.is_symlink():
                continue
            asset = asset_record(path, source)
            with wave.open(str(path), "rb") as audio:
                asset.update(
                    rate=audio.getframerate(),
                    channels=audio.getnchannels(),
                    duration=audio.getnframes() / audio.getframerate(),
                    sample_width=audio.getsampwidth(),
                )
            channel = path.stem.rsplit("_", 1)[-1]
            end = writes.get(path.name)
            asset.update(
                kind="audio",
                session_id=session["id"],
                channel=channel,
                chunk=path.stem.split("_")[1],
                end=end,
                start=max(0, end - asset["duration"]) if end is not None else None,
                timing_source="audio_write_event" if end is not None else "unknown",
                speaker_hint="primary_user" if channel == "mic" else "other_people",
            )
            assets[asset["id"]] = asset
            session["audio"].append(asset["id"])
        for path in sorted(folder.iterdir()):
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            asset = asset_record(path, source)
            derived = path.stem.endswith("_boxes")
            asset.update(
                kind="debug_image" if derived else "image",
                session_id=session["id"],
                at=image_time(path, origin),
                derived=derived,
            )
            assets[asset["id"]] = asset
            if not derived:
                session["screens"].append(asset["id"])
        for path in sorted(folder.glob("pair_response_*.json")):
            asset = asset_record(path, source)
            asset.update(kind="legacy_response", session_id=session["id"])
            assets[asset["id"]] = asset
            session["legacy"].append(asset["id"])
        end = max(
            [seconds_between(records[-1]["timestamp"], origin)] + [assets[a]["end"] or 0 for a in session["audio"]]
        )
        points = {round(t, 3): "historical help request" for t in requests}
        # Bounded inspection points, not a claim to reproduce every 30-second live call.
        if end > 900:
            points.update({float(t): "continuity checkpoint" for t in range(900, int(end), 900)})
        points[round(end, 3)] = "session end"
        primary = max(requests) if requests else end
        for i, (at, reason) in enumerate(sorted(points.items())):
            session["checkpoints"].append(
                {
                    "id": session["id"] + f"-point-{i + 1}",
                    "at": at,
                    "reason": reason,
                    "primary": abs(at - primary) < 0.01,
                }
            )
        session.update(duration=end, foreign_audio_events_excluded=foreign)
        sessions.append(session)
    assigned = {asset["path"] for asset in assets.values()}
    loose = []
    for folder in (source, source / "lib"):
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.is_symlink() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            if str(path.relative_to(source)) in assigned:
                continue
            asset = asset_record(path, source)
            asset.update(kind="image", session_id=None, at=None, derived=False)
            assets[asset["id"]] = asset
            loose.append(asset["id"])
    duplicates = defaultdict(list)
    for asset in assets.values():
        duplicates[asset["sha256"]].append(asset["id"])
    index = {
        "version": VERSION,
        "source_root": str(source),
        "sessions": sessions,
        "assets": assets,
        "unassigned_screens": loose,
        "exact_duplicate_groups": [ids for ids in duplicates.values() if len(ids) > 1],
        "origin": "historical owner-provided recordings",
        "human_calibrated": False,
        "split_policy": "All eight sessions are discovery/review data. No held-out generalization claim.",
    }
    write_json(output / "index.json", index)
    return index


def source_path(index, asset_id, *, verify=False):
    asset = index["assets"][asset_id]
    root = Path(index["source_root"]).resolve()
    path = root / asset["path"]
    if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError("Recorded asset is outside the indexed source")
    if verify and file_hash(path) != asset["sha256"]:
        raise ValueError("Recorded asset changed after indexing")
    return path


def read_audio(path):
    import numpy as np

    with wave.open(str(path), "rb") as stream:
        if stream.getsampwidth() != 2 or stream.getnchannels() != 1 or stream.getframerate() != 16000:
            raise ValueError("The current replay supports recorded 16 kHz mono PCM16 audio")
        return np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2").astype(np.float32) / 32768


def words(text):
    return re.findall(r"\w+(?:['’-]\w+)*", text.lower())


def disagreement(candidate, reference):
    """Edit distance for triage; not accuracy without a verified transcript."""
    a, b = words(reference), words(candidate)
    previous = list(range(len(b) + 1))
    for i, first in enumerate(a, 1):
        current = [i]
        for j, second in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (first != second)))
        previous = current
    return {
        "word_edits": previous[-1],
        "reference_words": len(a),
        "word_disagreement": previous[-1] / max(1, len(a)),
        "meaning": "ASR disagreement against a provisional reference, not verified word error rate",
    }
