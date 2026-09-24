import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orbit.settings import Settings


class SettingsTests(unittest.TestCase):
    def test_missing_and_corrupt_files_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            with patch("orbit.settings.settings_path", return_value=path):
                self.assertEqual(Settings.load().stt_model, Settings().stt_model)
                path.write_text("{not json", encoding="utf-8")
                self.assertEqual(Settings.load().context_turns, Settings().context_turns)

    def test_unknown_keys_are_ignored_and_values_sanitised(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps({"opacity_percent": 999, "unknown": True}),
                encoding="utf-8",
            )
            with patch("orbit.settings.settings_path", return_value=path):
                settings = Settings.load()
            self.assertEqual(settings.opacity_percent, 100)
            self.assertFalse(hasattr(settings, "unknown"))


if __name__ == "__main__":
    unittest.main()
