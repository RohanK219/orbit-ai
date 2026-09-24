import unittest

import numpy as np

from orbit.audio.vad import UtteranceSegmenter
from orbit.config import VadSettings


class VadTests(unittest.TestCase):
    def test_splits_speech_after_configured_silence(self):
        settings = VadSettings(
            calibration_seconds=0.02,
            speech_frames_to_start=1,
            silence_frames_to_end=2,
            min_utterance_seconds=0.02,
        )
        segmenter = UtteranceSegmenter(settings)
        frame = np.full(320, 0.1, dtype=np.float32)
        silence = np.zeros(320, dtype=np.float32)
        segmenter.push(np.zeros(320, dtype=np.float32))
        segmenter.push(frame)
        result = segmenter.push(np.concatenate((silence, silence)))
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0].truncated)
        self.assertGreater(result[0].speech_seconds, 0)

    def test_flush_marks_unfinished_utterance_truncated(self):
        settings = VadSettings(
            calibration_seconds=0.02,
            speech_frames_to_start=1,
            min_utterance_seconds=0.02,
        )
        segmenter = UtteranceSegmenter(settings)
        segmenter.push(np.zeros(320, dtype=np.float32))
        segmenter.push(np.full(320, 0.1, dtype=np.float32))
        result = segmenter.flush()
        self.assertIsNotNone(result)
        self.assertTrue(result.truncated)


if __name__ == "__main__":
    unittest.main()
