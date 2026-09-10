"""Explicit live acceptance run: staged screen/speech -> real Codex -> AppKit output.

Run with .venv/bin/python tests/live_workflow.py. Uses Codex CLI sign-in and local
ASR. Only the staged window region is read; generated speech is transcribed from
files through the normal audio queues. Native hardware routing is checked separately.
All captures, responses, and reports stay in a temporary directory.
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import AppKit as A
import numpy as np
import objc
import Quartz
from Foundation import NSMakeRect, NSObject, NSTimer
from PIL import Image
from PyObjCTools import AppHelper

from otsc.app import Controller
from otsc.capture import AudioCapture
from otsc.models import Assistance
from otsc.native import FlippedView, frame, label
from otsc.privacy import private_write
from otsc.settings import Settings

GOAL = "Make the mean helper handle empty input without confusing missing data with zero."


class LiveRun(NSObject):
    def initWithDirectory_renderedScreen_(self, directory, rendered_screen):
        self = objc.super(LiveRun, self).init()
        self.directory = Path(directory)
        self.rendered_screen = bool(rendered_screen)
        self.frame_patch = None
        self.started = time.monotonic()
        self.requested = None
        self.quick_seen = False
        self.timings = {}
        self.finished = False
        self.capture_pending = True
        settings = Settings(
            configured=True,
            goal=GOAL,
            transcription="local",
        )
        self.fixture = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(100, 180, 850, 240), A.NSWindowStyleMaskTitled, A.NSBackingStoreBuffered, False
        )
        self.fixture.setTitle_("Staged source for live acceptance")
        self.fixture.setReleasedWhenClosed_(False)
        self.fixture.setLevel_(A.NSFloatingWindowLevel)
        root = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, 850, 240))
        self.fixture.setContentView_(root)
        for text, x, y in [
            ("stats.py", 24, 18),
            ("1", 24, 75),
            ("def mean(values):", 90, 75),
            ("2", 24, 116),
            ("return sum(values) / len(values)", 146, 116),
        ]:
            text_field = label(root, text, size=25)
            text_field.setFont_(A.NSFont.monospacedSystemFontOfSize_weight_(25, A.NSFontWeightRegular))
            frame(text_field, x, y, 680, 34)
        origin = self.fixture.convertPointToScreen_((0, 0))
        screen_height = A.NSScreen.mainScreen().frame().size.height
        settings.screen_region = (int(origin.x), int(screen_height - origin.y - 240), 850, 240)
        with patch("otsc.app.load_settings", return_value=settings):
            self.controller = Controller.alloc().initWithOptions_(
                SimpleNamespace(
                    demo=False, design_demo=False, smoke_test=None, disable_midi=True, trace_source="synthetic-live"
                )
            )
        self.fixture.orderFront_(None)
        audio = AudioCapture(settings, self.controller.events)
        self.controller.audio = audio
        audio.start()
        for channel, who, voice, sentence in [
            ("system", "other", "Samantha", "Could we just return zero when the input is empty?"),
            ("microphone", "user", "Alex", "Zero is a real measurement here. Missing data needs a different result."),
        ]:
            path = self.directory / (who + ".wav")
            subprocess.run(["say", "-v", voice, "-o", str(path), "--data-format=LEI16@16000", sentence], check=True)
            with wave.open(str(path), "rb") as source:
                samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32) / 32768
            audio.queues[channel].put((samples, time.time()))
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.1, self, "tick:", None, True
        )
        return self

    def tick_(self, timer):
        if self.finished:
            return
        try:
            if self.capture_pending:
                self.capture_pending = False
                self.fixture.setHidesOnDeactivate_(False)
                self.fixture.makeKeyAndOrderFront_(None)
                self.fixture.orderFrontRegardless()
                self.fixture.display()
                A.NSApp.activateIgnoringOtherApps_(True)
                if self.rendered_screen:
                    # Explicit fixture mode: still run real OCR, vision, ASR, and
                    # inference, but never claim this is a desktop capture.
                    root = self.fixture.contentView()
                    bitmap = root.bitmapImageRepForCachingDisplayInRect_(root.bounds())
                    root.cacheDisplayInRect_toBitmapImageRep_(root.bounds(), bitmap)
                    data = bitmap.representationUsingType_properties_(A.NSBitmapImageFileTypePNG, {})
                    self.fixture_image = Image.open(io.BytesIO(bytes(data))).convert("RGB")
                    self.frame_patch = patch(
                        "pyautogui.screenshot", side_effect=lambda **kwargs: self.fixture_image.copy()
                    )
                    self.frame_patch.start()
                info = Quartz.CGWindowListCopyWindowInfo(
                    Quartz.kCGWindowListOptionIncludingWindow, self.fixture.windowNumber()
                )[0]
                bounds = info[Quartz.kCGWindowBounds]
                self.controller.settings.screen_region = tuple(int(bounds[k]) for k in ("X", "Y", "Width", "Height"))
                private_write(
                    self.directory / "capture-bounds.json",
                    json.dumps(
                        {
                            "region": self.controller.settings.screen_region,
                            "onscreen": bool(info.get(Quartz.kCGWindowIsOnscreen)),
                        }
                    ),
                )
                self.controller.capture_context()
            if time.monotonic() - self.started > 110:
                raise RuntimeError("Live workflow timed out: " + str(self.controller.status.stringValue()))
            context = self.controller.context
            if self.requested is None:
                speech = [o for o in context.observations if o.kind == "speech"]
                screens = [o for o in context.observations if o.kind == "screen"]
                if len(speech) == 2 and screens and not self.controller.capture_busy:
                    assert {o.speaker for o in speech} == {"primary_user", "other_people"}
                    private_write(
                        self.directory / "observations.json",
                        json.dumps([o.model_dump(exclude={"image_path"}) for o in context.observations], indent=2),
                    )
                    if screens[-1].image_path:
                        private_write(self.directory / "captured-frame.png", Path(screens[-1].image_path).read_bytes())
                    assert re.search(r"mean\s*\(\s*values", screens[-1].text), (
                        "The captured region did not contain the staged function"
                    )
                    self.controller.helpNow_(None)
                    self.requested = time.monotonic()
                return
            if self.controller.manual_help_pending:
                return
            coordinator = self.controller.coordinator
            if coordinator.published_lane == 0 and not self.quick_seen:
                self.quick_seen = True
                self.timings["quick_seconds"] = round(time.monotonic() - self.requested, 2)
                private_write(self.directory / "quick.json", coordinator.current.model_dump_json(indent=2))
            if coordinator.published_lane == 1 and coordinator.current:
                response = coordinator.current
                self.timings["deep_seconds"] = round(time.monotonic() - self.requested, 2)
                private_write(self.directory / "deep.json", response.model_dump_json(indent=2))
                delivered = [*coordinator.history, response]
                private_write(self.directory / "delivered-history.json", json.dumps([r.model_dump() for r in delivered], indent=2))
                assert response.artifacts, "Deep response supplied no artifact"
                # A fresh OCR result may preserve the proposal without repeating
                # an already-addressed question. Check the delivered conversation.
                other_sources = {o.id for o in context.observations if o.speaker == "other_people"}
                assert any(c.source_id in other_sources and c.action in {"challenge", "answer"}
                           for r in delivered for c in r.conversation), "The other speaker's question was never addressed"
                assert any(a.kind == "code" and "None" in a.content for a in response.artifacts)
                self.controller.save_view_image(self.directory / "window.png")
                current_code = self.controller.copy_text()
                # A short follow-up must leave the existing artifact available.
                coordinator.current = Assistance(
                    task=response.task,
                    summary="The existing helper remains appropriate.",
                    conversation=[],
                    artifacts=[],
                    observed_files=[],
                    open_questions=[],
                )
                self.controller.output_history.append(
                    coordinator.current, session_id=context.session_id, goal=context.goal, lane="synthetic follow-up",
                )
                self.controller.render_response()
                assert self.controller.copy_text() == current_code
                self.finish(
                    {
                        "passed": True,
                        **self.timings,
                        "quick_seen_before_deep": self.quick_seen,
                        "real_model": True,
                        "real_local_asr": True,
                        "real_screen_ocr": True,
                        "real_screen_capture": not self.rendered_screen,
                        "screen_scope": "rendered owned fixture" if self.rendered_screen else "staged code window only",
                        "speech": "generated acceptance sentences",
                    }
                )
            elif not coordinator.active_lanes:
                raise RuntimeError("No deep artifact: " + str(self.controller.status.stringValue()))
        except Exception as error:
            import traceback

            private_write(self.directory / "failure.txt", traceback.format_exc())
            self.finish({"passed": False, "error": str(error) or type(error).__name__, **self.timings})

    @objc.python_method
    def finish(self, result):
        self.finished = True
        private_write(self.directory / "result.json", json.dumps(result, indent=2))
        print(json.dumps({"directory": str(self.directory), **result}), flush=True)
        self.timer.invalidate()
        self.controller.shutdown()
        if self.frame_patch:
            self.frame_patch.stop()
        self.fixture.orderOut_(None)
        if not result["passed"]:
            os._exit(1)
        A.NSApp.terminate_(None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rendered-screen",
        action="store_true",
        help="Use an app-rendered fixture instead of desktop capture; real OCR, ASR, and models still run",
    )
    args = parser.parse_args()
    if not args.rendered_screen and Quartz.CGGetActiveDisplayList(16, None, None)[2] == 0:
        parser.error(
            "macOS reports no active display. Use --rendered-screen for an explicitly rendered-fixture run, or rerun desktop capture with an active display."
        )
    directory = Path(tempfile.mkdtemp(prefix="otsc-live-workflow-"))
    os.environ["OTSC_DATA_DIR"] = str(directory / "preferences")
    os.environ["HF_HUB_OFFLINE"] = "1"
    app = A.NSApplication.sharedApplication()
    app.setActivationPolicy_(A.NSApplicationActivationPolicyRegular)
    run = LiveRun.alloc().initWithDirectory_renderedScreen_(str(directory), args.rendered_screen)
    app.setDelegate_(run.controller)
    print("Live workflow started: " + str(directory), flush=True)
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
