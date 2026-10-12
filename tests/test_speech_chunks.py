import unittest

import numpy as np

from otsc.speech_chunks import SpeechChunks


class SpeechChunkTests(unittest.TestCase):
    def test_short_complete_utterance_flushes_on_pause_before_fixed_deadline(self):
        chunks = SpeechChunks(max_seconds=5)
        self.assertEqual(chunks.feed(np.ones(16000, dtype=np.float32) * .1), [])
        result = chunks.feed(np.zeros(4800, dtype=np.float32))
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]), 20800)

    def test_continuous_speech_is_not_cut_at_two_seconds_and_stays_bounded(self):
        chunks = SpeechChunks(max_seconds=5)
        self.assertEqual(chunks.feed(np.ones(32000) * .1), [])
        result = chunks.feed(np.ones(64000) * .1)
        self.assertEqual([len(x) for x in result], [80000])
        self.assertEqual(len(chunks.buffer), 16000)

    def test_force_flush_keeps_short_no_and_two_channels_do_not_mix(self):
        mic, system = SpeechChunks(), SpeechChunks()
        mic.feed(np.ones(1600) * .1)
        system.feed(np.ones(8000) * .2)
        a, b = mic.feed([], flush=True), system.feed([], flush=True)
        self.assertEqual(len(a[0]), 4000)
        self.assertEqual(len(b[0]), 8000)
        self.assertTrue(np.all(a[0][:1600] == np.float32(.1)))
        self.assertTrue(np.all(b[0] == np.float32(.2)))
        self.assertEqual(mic.feed([], flush=True), [])

    def test_silence_is_bounded_and_ceiling_prefers_a_quiet_word_boundary(self):
        chunks = SpeechChunks()
        self.assertEqual(chunks.feed(np.zeros(160000)), [])
        self.assertLessEqual(chunks.buffer.size, 1920)
        chunks = SpeechChunks()
        audio = np.ones(80000, dtype=np.float32) * .1
        audio[72000:74560] = 0
        result = chunks.feed(audio)
        self.assertEqual(len(result), 1)
        self.assertTrue(72000 <= len(result[0]) <= 74560)
