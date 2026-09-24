import sys
import types
import unittest
from unittest.mock import patch

import numpy as np

from orbit.stt.local_whisper import (
    LocalWhisperDependencyError,
    LocalWhisperModelError,
    LocalWhisperTranscriber,
)


class LocalWhisperTests(unittest.TestCase):
    def test_missing_dependency_is_actionable(self):
        with patch.dict(sys.modules, {"faster_whisper": None}):
            with self.assertRaisesRegex(LocalWhisperDependencyError, "faster-whisper"):
                LocalWhisperTranscriber()

    def test_transcribes_segments_and_forwards_prompt(self):
        calls = {}

        class FakeModel:
            def transcribe(self, audio, **kwargs):
                calls["audio"] = audio
                calls["kwargs"] = kwargs
                return iter([types.SimpleNamespace(text=" hello "), types.SimpleNamespace(text="world")]), None

        module = types.SimpleNamespace(WhisperModel=lambda *args, **kwargs: FakeModel())
        with patch.dict(sys.modules, {"faster_whisper": module}):
            transcriber = LocalWhisperTranscriber("tiny", language=None, device="cpu")
            result = transcriber.transcribe(np.zeros(10, dtype=np.float32), prompt="API")
        self.assertEqual(result.text, "hello world")
        self.assertEqual(result.model, "tiny")
        self.assertEqual(calls["kwargs"]["initial_prompt"], "API")
        self.assertIsNone(calls["kwargs"]["language"])

    def test_model_load_error_is_wrapped(self):
        def fail(*args, **kwargs):
            raise OSError("download failed")

        module = types.SimpleNamespace(WhisperModel=fail)
        with patch.dict(sys.modules, {"faster_whisper": module}):
            with self.assertRaisesRegex(LocalWhisperModelError, "download failed"):
                LocalWhisperTranscriber("tiny")


if __name__ == "__main__":
    unittest.main()
