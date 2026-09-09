import json
import tempfile
import threading
import unittest
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from evals.recording_data import build_index, read_json, source_path, write_json
from evals.recording_replay import recorded_events
from evals.recording_review import ReviewStore, make_handler


class RecordingReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.output = self.root / "private-review"
        folder = self.source / "lib/pair_session_20260101_120000"
        folder.mkdir(parents=True)
        audio = folder / "audio_0001_mic.wav"
        with wave.open(str(audio), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(16000)
            stream.writeframes(b"\0\0" * 16000)
        events = [
            {"event": "pair_start_session", "timestamp": "2026-01-01 12:00:00"},
            {"event": "audio_write_mic", "timestamp": "2026-01-01 12:00:02", "audio_path": str(audio)},
            {"event": "codex_pair_prompt_build", "timestamp": "2026-01-01 12:00:03"},
        ]
        (folder / "pair_profile.jsonl").write_text("\n".join(json.dumps(x) for x in events))
        self.index = build_index(self.source, self.output)
        self.asset_id = next(iter(self.index["assets"]))

    def test_replay_uses_new_transcripts_and_never_adds_a_future_clip(self):
        write_json(
            self.output / "audio" / (self.asset_id + ".json"),
            {
                "candidate": {"completed": True, "text": "new transcription"},
                "reference": {"completed": True, "text": "independent reference"},
            },
        )
        session = self.index["sessions"][0]
        events = recorded_events(self.output, session)
        self.assertEqual(events[0]["text"], "new transcription")
        self.assertEqual(events[0]["id"], self.asset_id)
        self.assertFalse([e for e in events if e["at"] <= 1])
        self.assertEqual(recorded_events(self.output, session, reference=True)[0]["text"], "independent reference")

    def test_index_cannot_authorize_an_outside_file_or_changed_recording(self):
        asset = self.index["assets"][self.asset_id]
        original = asset["path"]
        asset["path"] = "../../outside.wav"
        with self.assertRaises(ValueError):
            source_path(self.index, self.asset_id)
        asset["path"] = original
        path = source_path(self.index, self.asset_id)
        path.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            source_path(self.index, self.asset_id, verify=True)
        with self.assertRaises(ValueError):
            build_index(self.source, self.source / "outputs")

    def test_reviews_persist_without_modifying_recorded_or_model_data(self):
        store = ReviewStore(self.output)
        row = {
            "target_id": self.asset_id,
            "component": "transcript",
            "status": "approved",
            "candidate_verdict": "fail",
            "corrected_text": "A corrected reference",
            "notes": "missed negation",
            "reference_hash": "reference-version",
            "reference_edited": True,
        }
        before = source_path(self.index, self.asset_id).read_bytes()
        store.save(row)
        saved = ReviewStore(self.output).reviews()[self.asset_id + ":transcript"]
        self.assertEqual(saved["candidate_verdict"], "fail")
        self.assertEqual(saved["origin"], "manual browser review")
        self.assertEqual(source_path(self.index, self.asset_id).read_bytes(), before)
        self.assertEqual((self.output / "human-reviews.jsonl").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            store.save({**row, "target_id": "unknown"})

    def test_local_server_supports_audio_seeking_and_rejects_forged_review_posts(self):
        store = ReviewStore(self.output)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(Request(base + "/media/" + self.asset_id, headers={"Range": "bytes=0-43"})) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(len(response.read()), 44)
        data = json.load(urlopen(base + "/api/index"))
        self.assertEqual(len(data["sessions"]), 1)
        correction = {
            "target_id": self.asset_id,
            "component": "transcript",
            "status": "approved",
            "candidate_verdict": "fail",
            "corrected_text": "",
            "reference_edited": True,
            "notes": "No speech is audible",
            "reference_hash": "draft-hash",
        }
        request = Request(
            base + "/api/reviews",
            data=json.dumps(correction).encode(),
            headers={"Content-Type": "application/json", "X-Review-Token": data["csrf_token"]},
        )
        with urlopen(request) as response:
            self.assertEqual(response.status, 200)
        saved = ReviewStore(self.output).reviews()[self.asset_id + ":transcript"]
        self.assertEqual(saved["corrected_text"], "")
        self.assertTrue(saved["reference_edited"])
        with self.assertRaises(HTTPError) as denied:
            urlopen(Request(base + "/api/reviews", data=b"{}", headers={"Content-Type": "application/json"}))
        self.assertEqual(denied.exception.code, 403)
        with self.assertRaises(HTTPError):
            urlopen(base + "/media/../../outside")
        self.assertEqual(read_json(self.output / "index.json")["source_root"], str(self.source.resolve()))


if __name__ == "__main__":
    unittest.main()
