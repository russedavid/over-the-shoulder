"""Local review of indexed recordings; explicit, versioned human annotations."""

import argparse
import hmac
import json
import mimetypes
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from evals.recording_data import default_directory, read_json, source_path
from otsc.diagram import diagram_svg
from otsc.models import Artifact
from otsc.privacy import private_directory
from otsc.telemetry import digest


class ReviewStore:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.index = read_json(self.directory / "index.json")
        self.lock = threading.Lock()
        self.sessions = {s["id"]: s for s in self.index["sessions"]}
        self.points = {p["id"]: p for s in self.sessions.values() for p in s["checkpoints"]}
        self.token = secrets.token_urlsafe(32)

    def reviews(self):
        latest = {}
        path = self.directory / "human-reviews.jsonl"
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    row = json.loads(line)
                    latest[row["target_id"] + ":" + row["component"]] = row
                except (ValueError, KeyError, TypeError):
                    continue
        return latest

    def save(self, data):
        allowed = {
            "target_id",
            "component",
            "status",
            "candidate_verdict",
            "corrected_text",
            "notes",
            "reference_hash",
            "reference_edited",
        }
        if set(data) != allowed:
            raise ValueError("Unsupported review fields")
        target = data["target_id"]
        if target not in self.sessions and target not in self.points and target not in self.index["assets"]:
            raise ValueError("Review target is not in this recording set")
        if data["component"] not in {"transcript", "ocr", "vlm", "assistance", "session"}:
            raise ValueError("Unsupported review component")
        if data["status"] not in {"approved", "needs_work", "uncertain", "unreviewed"}:
            raise ValueError("Unsupported review status")
        if data["candidate_verdict"] not in {"pass", "fail", "uncertain", "unreviewed"}:
            raise ValueError("Unsupported candidate verdict")
        if any(not isinstance(data[key], str) for key in allowed - {"reference_edited"}):
            raise ValueError("Review fields must be text")
        if not isinstance(data["reference_edited"], bool):
            raise ValueError("Reference edit flag must be boolean")
        if len(data["corrected_text"]) > 100000 or len(data["notes"]) > 12000:
            raise ValueError("Review text is too large")
        row = {**data, "at": time.time(), "origin": "manual browser review", "version": 1}
        private_directory(self.directory)
        with self.lock:
            fd = os.open(self.directory / "human-reviews.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        return row

    def summary(self):
        sessions = []
        for session in self.sessions.values():
            replay = read_json(self.directory / "replays" / (session["id"] + ".json"), {})
            references = [
                read_json(self.directory / "references" / (p["id"] + ".json"), {}) for p in session["checkpoints"]
            ]
            primary = next((r for p, r in zip(session["checkpoints"], references) if p["primary"]), {})
            sessions.append(
                {
                    **session,
                    "title": primary.get("reference", {}).get("title", "Recorded task session"),
                    "replayed": replay.get("completed", False),
                    "reference_count": sum(bool(r.get("reference")) for r in references),
                }
            )
        return {
            "run_label": self.index.get("run_label", "Recorded sessions"),
            "sessions": sessions,
            "assets": self.index["assets"],
            "unassigned_screens": self.index["unassigned_screens"],
            "reviews": self.reviews(),
            "csrf_token": self.token,
            "version": self.index["version"],
        }

    def session(self, identity):
        session = self.sessions[identity]
        audio = {a: read_json(self.directory / "audio" / (a + ".json"), {}) for a in session["audio"]}
        images = {a: read_json(self.directory / "images" / (a + ".json"), {}) for a in session["screens"]}
        references = {
            p["id"]: read_json(self.directory / "references" / (p["id"] + ".json"), {}) for p in session["checkpoints"]
        }
        assessments = {
            p["id"]: read_json(self.directory / "assessments" / (p["id"] + ".json"), {}) for p in session["checkpoints"]
        }
        for value in [*audio.values(), *images.values(), *references.values()]:
            value["reference_hash"] = digest(value.get("reference"))
        return {
            "session": session,
            "audio": audio,
            "images": images,
            "references": references,
            "assessments": assessments,
            "replay": read_json(self.directory / "replays" / (identity + ".json"), {}),
            "reviews": self.reviews(),
        }


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def local_request(self):
            hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            return self.headers.get("Host") in hosts and (
                not self.headers.get("Origin") or self.headers["Origin"] in {"http://" + h for h in hosts}
            )

        def write_headers(self, status, kind, length):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()

        def payload(self, value, status=200):
            body = json.dumps(value, ensure_ascii=False).encode()
            self.write_headers(status, "application/json; charset=utf-8", len(body))
            self.wfile.write(body)

        def stream(self, path):
            size = path.stat().st_size
            start, end = 0, size - 1
            partial = self.headers.get("Range")
            if partial:
                match = re.fullmatch(r"bytes=(\d+)-(\d*)", partial)
                if not match:
                    return self.payload({"error": "Unsupported media range"}, 416)
                start = int(match[1])
                end = min(end, int(match[2])) if match[2] else end
                if start > end or start >= size:
                    return self.payload({"error": "Invalid media range"}, 416)
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with path.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    block = stream.read(min(65536, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)

        def do_GET(self):
            if not self.local_request():
                return self.payload({"error": "Local review only"}, 403)
            path = unquote(urlsplit(self.path).path)
            try:
                if path == "/api/index":
                    return self.payload(store.summary())
                if path.startswith("/api/session/"):
                    return self.payload(store.session(path.removeprefix("/api/session/")))
                if path.startswith("/api/image/"):
                    asset = path.removeprefix("/api/image/")
                    if store.index["assets"][asset]["kind"] != "image":
                        raise KeyError(asset)
                    row = read_json(store.directory / "images" / (asset + ".json"), {})
                    row["reference_hash"] = digest(row.get("reference"))
                    return self.payload(row)
                if path.startswith("/media/"):
                    asset = path.removeprefix("/media/")
                    return self.stream(source_path(store.index, asset, verify=True))
                if path.startswith("/diagram/"):
                    _, _, point_id, lane, number = path.split("/")
                    session_id = next(
                        s["id"] for s in store.sessions.values() if any(p["id"] == point_id for p in s["checkpoints"])
                    )
                    replay = read_json(store.directory / "replays" / (session_id + ".json"))
                    point = next(p for p in replay["checkpoints"] if p["id"] == point_id)
                    artifact = Artifact.model_validate(point[lane]["response"]["artifacts"][int(number)])
                    if artifact.kind != "diagram":
                        raise ValueError("Not a diagram")
                    body = diagram_svg(artifact).encode()
                    self.write_headers(200, "image/svg+xml", len(body))
                    return self.wfile.write(body)
                resources = {
                    "/": "recording_review.html",
                    "/review.js": "recording_review.js",
                    "/review.css": "recording_review.css",
                }
                if path in resources:
                    resource = Path(__file__).parent / "assets" / resources[path]
                    body = resource.read_bytes()
                    self.write_headers(200, mimetypes.guess_type(resource.name)[0] + "; charset=utf-8", len(body))
                    return self.wfile.write(body)
                return self.payload({"error": "Not found"}, 404)
            except (KeyError, ValueError, StopIteration, IndexError, OSError):
                return self.payload({"error": "Recorded item unavailable"}, 404)

        def do_POST(self):
            if not self.local_request() or not hmac.compare_digest(self.headers.get("X-Review-Token", ""), store.token):
                return self.payload({"error": "Invalid review origin"}, 403)
            if self.path != "/api/reviews":
                return self.payload({"error": "Not found"}, 404)
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 150000:
                    raise ValueError("Review exceeds the size limit")
                return self.payload(store.save(json.loads(self.rfile.read(length))))
            except (ValueError, KeyError, TypeError):
                return self.payload({"error": "Invalid review submission"}, 400)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default=str(default_directory()))
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(ReviewStore(args.directory)))
    print(f"Private recordings review: http://127.0.0.1:{server.server_port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
