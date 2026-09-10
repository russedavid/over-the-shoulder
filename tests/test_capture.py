import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(sys.platform == "darwin", "Native capture adapters require macOS")
class CaptureTests(unittest.TestCase):
    def test_voice_finish_stops_hardware_before_finishing_transcription(self):
        from types import SimpleNamespace

        import numpy as np

        from otsc.capture import AudioCapture
        from otsc.settings import Settings

        events = queue.Queue()
        started = threading.Event()
        release = threading.Event()
        closed = []

        class Transcriber:
            def transcribe(self, samples):
                started.set()
                release.wait(2)
                return "A completed question"

        capture = AudioCapture(Settings(transcription="local"), events)
        capture.transcriber = Transcriber()
        capture.mic = SimpleNamespace(stop=lambda: closed.append("stopped"), close=lambda: closed.append("closed"))
        try:
            with capture.mic_lock:
                capture.mic_chunks.append(np.full(8000, 0.1, dtype=np.float32))
            capture.start()
            request = capture.flush_for_help(stop_capture=True)
            self.assertTrue(started.wait(1))
            self.assertEqual(closed, ["stopped", "closed"])
            release.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                event = events.get(timeout=2)
                if event["type"] == "audio_flushed":
                    self.assertEqual(event["flush_id"], request)
                    self.assertTrue(event["complete"])
                    break
            else:
                self.fail("Voice completion was not delivered")
            self.assertTrue(capture.stop_event.wait(1))
        finally:
            release.set()
            capture.stop()

    def test_help_flush_delivers_current_speech_before_signalling_readiness(self):
        import numpy as np

        from otsc.capture import AudioCapture
        from otsc.settings import Settings

        events = queue.Queue()
        started, release = threading.Event(), threading.Event()

        class Transcriber:
            def transcribe(self, samples):
                started.set()
                release.wait(2)
                return "What about the question I just asked?"

        capture = AudioCapture(Settings(transcription="local"), events)
        capture.transcriber = Transcriber()
        try:
            capture.start()
            with capture.mic_lock:
                capture.mic_chunks.append(np.full(8000, 0.1, dtype=np.float32))
            request = capture.flush_for_help()
            self.assertTrue(started.wait(1))
            received = []
            while not events.empty():
                received.append(events.get_nowait())
            self.assertFalse(any(e["type"] == "audio_flushed" for e in received))
            release.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                event = events.get(timeout=2)
                received.append(event)
                if event["type"] == "audio_flushed":
                    self.assertEqual(event["flush_id"], request)
                    self.assertTrue(event["complete"])
                    break
            types = [e["type"] for e in received]
            self.assertLess(types.index("speech"), types.index("audio_flushed"))
        finally:
            release.set()
            capture.stop()

    def test_ocr_separates_editor_line_numbers_from_the_actual_code(self):
        from PIL import Image, ImageDraw, ImageFont

        from otsc.capture import ScreenCapture

        image = Image.new("RGB", (1000, 250), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 25)
        for text, x, y in [
            ("stats.py", 24, 15),
            ("1", 24, 65),
            ("def mean(values):", 90, 65),
            ("2", 24, 110),
            ("return sum(values) / len(values)", 150, 110),
        ]:
            draw.text((x, y), text, font=font, fill="black")
        text, _ = ScreenCapture().interpret(image)
        self.assertIn("def mean(values):", text)
        self.assertNotIn("1 def", text)
        self.assertIn("Detected editor line numbers: 1–2", text)

    def test_actual_ocr_on_synthetic_code_without_desktop_capture(self):
        from PIL import Image, ImageDraw, ImageFont

        from otsc.capture import ScreenCapture

        image = Image.new("RGB", (1000, 240), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 25)
        draw.text(
            (24, 25),
            "stats.py\ndef mean(values):\n    return sum(values) / len(values)",
            font=font,
            fill="black",
            spacing=12,
        )
        text, path = ScreenCapture().interpret(image)
        self.assertIn("mean(values)", text)
        self.assertIn("return sum", text)
        self.assertEqual(path, "")

    def test_detected_secret_is_redacted_in_text_and_in_the_saved_vision_frame(self):
        from PIL import Image

        from otsc.capture import ScreenCapture

        data = {
            "text": ["api_key=synthetic-secret"],
            "block_num": [1],
            "par_num": [1],
            "line_num": [1],
            "left": [20],
            "top": [20],
            "width": [100],
            "height": [25],
        }
        with tempfile.TemporaryDirectory() as temp:
            capture = ScreenCapture()
            with (
                patch("otsc.capture.app_directory", return_value=Path(temp)),
                patch("pytesseract.image_to_data", return_value=data),
            ):
                text, path = capture.interpret(Image.new("RGB", (200, 100), "white"), keep_image=True)
            self.assertNotIn("synthetic-secret", text)
            with Image.open(path) as image:
                self.assertEqual(image.getpixel((40, 30)), (0, 0, 0))
            self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)
            capture.close()
            self.assertFalse(Path(path).exists())

    def test_native_audio_buffer_copy_keeps_pcm_samples_without_starting_capture(self):
        import numpy as np
        import ScreenCaptureKit as SCK

        from otsc.capture import make_system_audio

        samples = np.array([0.25, -0.5, 0.0], dtype=np.float32)
        source = make_system_audio("screencapturekit")
        with (
            patch("CoreMedia.CMSampleBufferGetDataBuffer", return_value="synthetic"),
            patch("CoreMedia.CMBlockBufferGetDataLength", return_value=len(samples.tobytes())),
            patch("CoreMedia.CMBlockBufferCopyDataBytes", return_value=(0, samples.tobytes())),
        ):
            source.delegate.stream_didOutputSampleBuffer_ofType_(None, None, SCK.SCStreamOutputTypeAudio)
        np.testing.assert_array_equal(source.drain(), samples)


if __name__ == "__main__":
    unittest.main()
