import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(sys.platform == "darwin", "Native capture adapters require macOS")
class CaptureTests(unittest.TestCase):
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
        source = make_system_audio()
        with (
            patch("CoreMedia.CMSampleBufferGetDataBuffer", return_value="synthetic"),
            patch("CoreMedia.CMBlockBufferGetDataLength", return_value=len(samples.tobytes())),
            patch("CoreMedia.CMBlockBufferCopyDataBytes", return_value=(0, samples.tobytes())),
        ):
            source.delegate.stream_didOutputSampleBuffer_ofType_(None, None, SCK.SCStreamOutputTypeAudio)
        np.testing.assert_array_equal(source.drain(), samples)


if __name__ == "__main__":
    unittest.main()
