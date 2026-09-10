import ctypes as ct
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from otsc.audio_tap import AudioBuffer, AudioBuffers, SystemAudioTap, decode_buffers


@unittest.skipUnless(sys.platform == "darwin", "Optional native audio dependencies")
class AudioTapTests(unittest.TestCase):
    def test_interleaved_float_audio_is_mixed_to_mono(self):
        import numpy as np

        data = np.array([.6, .2, -.2, -.6], dtype="<f4")
        buffer = AudioBuffers(1, (AudioBuffer * 1)(AudioBuffer(2, data.nbytes, data.ctypes.data)))
        result = decode_buffers(ct.pointer(buffer), SimpleNamespace(flags=1, bits=32))
        np.testing.assert_allclose(result, [.4, -.4])

    def test_planar_signed_audio_is_scaled_and_mixed(self):
        import numpy as np

        class StereoBuffers(ct.Structure):
            _fields_ = [("count", ct.c_uint32), ("buffers", AudioBuffer * 2)]

        left, right = np.array([16384, -16384], dtype="<i2"), np.array([0, 16384], dtype="<i2")
        buffers = StereoBuffers(2, (AudioBuffer * 2)(AudioBuffer(1, left.nbytes, left.ctypes.data),
                                                   AudioBuffer(1, right.nbytes, right.ctypes.data)))
        result = decode_buffers(ct.cast(ct.pointer(buffers), ct.POINTER(AudioBuffers)), SimpleNamespace(flags=4, bits=16))
        np.testing.assert_allclose(result, [.25, 0])

    def test_drain_resamples_the_actual_device_rate(self):
        import numpy as np

        tap = SystemAudioTap()
        tap.format = SimpleNamespace(rate=48000)
        tap.chunks.append(np.ones(48000, dtype=np.float32))
        tap.sample_count = 48000
        self.assertEqual(len(tap.drain()), 16000)
        self.assertEqual(len(tap.drain()), 0)
        self.assertEqual(tap.sample_count, 0)

    def test_stopping_after_partial_start_releases_resources_without_stopping_unstarted_io(self):
        import CoreAudio

        tap = SystemAudioTap()
        tap.native = Mock()
        tap.tap_id, tap.device_id, tap.io_id = 7, 8, ct.c_void_p(9)
        with patch.object(CoreAudio, "AudioHardwareDestroyAggregateDevice") as device, patch.object(CoreAudio, "AudioHardwareDestroyProcessTap") as process:
            tap.stop()
            tap.stop()
        tap.native.AudioDeviceStop.assert_not_called()
        tap.native.AudioDeviceDestroyIOProcID.assert_called_once()
        device.assert_called_once_with(8)
        process.assert_called_once_with(7)

    def test_default_audio_backend_constructs_without_opening_any_stream(self):
        from otsc.capture import make_system_audio
        from otsc.settings import Settings

        with patch.object(SystemAudioTap, "start") as start, patch("otsc.capture.make_screen_audio") as legacy:
            tap = make_system_audio(Settings().system_audio_backend)
            self.assertIsInstance(tap, SystemAudioTap)
            start.assert_not_called()
            legacy.assert_not_called()
