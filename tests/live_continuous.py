"""Explicit, finite live check of visual reading, audio, memory and diagram revisions.

Uses generated fixtures, actual local ASR and configured Codex models. It never
captures the desktop or opens audio hardware. Outputs remain outside the repo.
Run: .venv/bin/python tests/live_continuous.py
"""

import argparse
import html
import json
import queue
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from otsc.capture import AudioCapture, ScreenCapture
from otsc.context import ContextStore
from otsc.context_builder import ContextBuilder
from otsc.diagram import diagram_svg
from otsc.perception import read_screen
from otsc.privacy import app_directory, private_directory, private_write
from otsc.providers import provider_for
from otsc.scheduler import Cancellation, Coordinator
from otsc.settings import Credentials, Settings
from otsc.telemetry import Progress, TraceStore, release_manifest


def run(directory):
    directory = Path(directory)
    if directory.exists():
        raise ValueError("Use a new output directory to preserve previous results")
    private_directory(directory)
    settings = Settings(configured=True, transcription="local")
    report = {"release": release_manifest(), "models": {k: getattr(settings, k).model_dump() for k in ("ocr", "context_builder", "quick", "deep")},
              "synthetic_inputs": True, "hardware_capture": False, "human_reviewed": False, "stages": [], "completed": False}
    trace = TraceStore(directory / "traces", source="synthetic-continuous-live")
    events = queue.Queue()
    context = ContextStore()
    scanner = ScreenCapture()
    audio = AudioCapture(settings, events)
    builder = ContextBuilder(context, lambda: provider_for(settings.context_builder, Credentials()), trace=trace)
    captured = {}

    class AnswerProvider:
        def __init__(self, lane):
            self.base = provider_for(getattr(settings, lane), Credentials())
            self.choice = self.base.choice

        def generate(self, snapshot, lane, token, progress):
            try:
                response = self.base.generate(snapshot, lane, token, progress)
                captured[lane] = {"input": snapshot.prompt_context(), "response": response.model_dump(),
                                  "delivery_notes": response._delivery_notes}
                return response
            finally:
                captured.setdefault(lane, {})["raw_text"] = self.base.last_raw_text

    answers = Coordinator(context, AnswerProvider, trace=trace)
    image = Image.new("RGB", (1700, 1000), "#fffdfa")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 27)
    requirements = """Design task: draw a multi-tenant webhook delivery system.

Requirements
1. Receive events over HTTPS and authenticate the sender.
2. Acknowledge only after the event is stored durably.
3. Deliver events to tenant-configured HTTPS endpoints.
4. Preserve event ordering within each tenant.
5. Receiver outages must not lose events or block other tenants.
6. Label retry and failure paths in the system design diagram.

Operations note
Two tenants may use the same destination service.
An acknowledgment can be lost even when delivery succeeds.
"""
    draw.multiline_text((35, 35), requirements, font=font, fill="#30291f", spacing=14)
    image.save(directory / "source.png")
    token = Cancellation()

    def screen_work():
        started = time.monotonic()
        at = time.time()
        try:
            _, path = scanner.interpret(image.copy(), keep_image=True, full_resolution=True)
            result = read_screen(path, provider_for(settings.ocr, Credentials()), token,
                                 Progress(lambda text: None, trace, lane="ocr"), at=at)
            events.put({"type": "screen", "reading": result, "path": path, "at": at, "seconds": time.monotonic() - started})
        except Exception as error:
            events.put({"type": "screen_error", "error": str(error)})

    def speak(channel, name, sentence):
        path = directory / (name + ".wav")
        subprocess.run(["say", "-v", "Samantha", "-o", str(path), "--data-format=LEI16@16000", sentence], check=True)
        with wave.open(str(path), "rb") as source:
            samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32) / 32768
        audio.queues[channel].put((samples, time.time()))

    def ingest(event):
        if event["type"] == "speech":
            context.add("speech", event["text"], event["channel"], event["speaker"], at=event["at"], confidence="transcribed")
        elif event["type"] == "screen":
            value = event["reading"]
            context.add("screen", value.visible_text, "screen", at=event["at"], image_path=event["path"], reading=value,
                        confidence="uncertain", keep_repeats=True)
            report["ocr"] = {"reading": value.model_dump(), "seconds": event["seconds"]}
        elif event["type"] in {"audio_error", "screen_error"}:
            raise RuntimeError(event.get("message", event.get("error")))

    def save():
        private_write(directory / "report.json", json.dumps(report, indent=2))

    try:
        audio.start()  # Both hardware flags are false; only generated PCM enters its queues.
        threading.Thread(target=screen_work, daemon=True).start()
        speak("microphone", "user", "Please draw the system design for the requirements on screen.")
        speak("system", "other", "Could we just retry immediately whenever the destination fails?")
        deadline = time.monotonic() + 240
        while len([o for o in context.observations if o.kind == "speech"]) < 2 or "ocr" not in report:
            if time.monotonic() > deadline:
                raise TimeoutError("Perception did not finish")
            try:
                ingest(events.get(timeout=0.1))
            except queue.Empty:
                pass
        report["observations"] = [o.model_dump(exclude={"image_path"}) for o in context.observations]
        print(json.dumps({"stage": "perception", "ocr_seconds": report["ocr"]["seconds"]}), flush=True)
        save()
        for phase in ("initial-design", "revised-design"):
            if phase == "revised-design":
                speak("microphone", "followup", "Keep the existing design, but stop retrying after five failed delivery attempts and send that event to a dead letter queue.")
                deadline = time.monotonic() + 30
                while True:
                    if time.monotonic() > deadline:
                        raise TimeoutError("Follow-up transcription did not finish")
                    event = events.get(timeout=2)
                    ingest(event)
                    if event["type"] == "speech":
                        break
            captured.clear()
            started = time.monotonic()
            builder.request()
            answers.request(manual=True)
            record = {"phase": phase, "events": []}
            deadline = started + 270
            while answers.active_lanes or builder.job:
                if time.monotonic() > deadline:
                    raise TimeoutError("Live answer or context update timed out")
                for source in (builder.events, answers.events):
                    try:
                        event = source.get_nowait()
                    except queue.Empty:
                        continue
                    if event["type"] == "context_built":
                        builder.accept(event)
                        record["context_update"] = event["update"].model_dump() if "update" in event else {"error": event.get("error")}
                    elif event["type"] in {"result", "error", "cancelled"}:
                        accepted = answers.accept(event)
                        record["events"].append({"type": event["type"], "lane": event["job"].lane, "accepted": accepted,
                                                 "seconds": time.monotonic() - started, "error": event.get("message")})
                time.sleep(0.02)
            record["generations"] = dict(captured)
            record["context_after"] = context.snapshot().prompt_context()
            report["stages"].append(record)
            save()
            if not captured.get("deep", {}).get("response"):
                raise AssertionError("No deep answer was delivered")
            diagrams = [a for a in answers.current.artifacts if a.kind == "diagram"]
            if not diagrams:
                raise AssertionError("The design request did not produce a diagram")
            for index, artifact in enumerate(diagrams):
                private_write(directory / f"{phase}-{index}.svg", diagram_svg(artifact))
            print(json.dumps({"stage": phase, "seconds": time.monotonic() - started, "diagrams": len(diagrams)}), flush=True)
        report["completed"] = True
        report["source_unchanged"] = release_manifest()["code_hash"] == report["release"]["code_hash"]
        save()
        body = '<h1>Continuous pipeline: design review</h1><p>Generated screen and speech; real Astra and local ASR. No hardware capture. Assistant review is provisional.</p><img width="850" src="source.png"><h2>OCR</h2><pre>' + html.escape(report["ocr"]["reading"]["visible_text"]) + '</pre>'
        for row in report["stages"]:
            phase = row["phase"]
            body += '<h2>' + phase + '</h2><img width="900" src="' + phase + '-0.svg"><details><summary>Inputs, outputs, memory delta, timings</summary><pre>' + html.escape(json.dumps(row, indent=2)) + '</pre></details>'
        body += '<p>Speech: <a href="user.wav">primary user</a> · <a href="other.wav">other speaker</a> · <a href="followup.wav">follow-up</a> · <a href="report.json">full report</a></p>'
        private_write(directory / "index.html", '<!doctype html><meta charset="utf-8"><title>OTSC continuous pipeline</title><style>body{max-width:1100px;margin:32px auto;padding:20px;background:#f8f4ec;color:#30291f;font:16px/1.5 system-ui}img{max-width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:16px}</style>' + body)
        return report
    finally:
        token.cancel()
        audio.stop()
        builder.close()
        answers.close()
        scanner.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=app_directory() / "evaluations" / time.strftime("continuous-live-%Y%m%d-%H%M%S"))
    args = parser.parse_args()
    print("Review directory:", args.output, flush=True)
    run(args.output)
