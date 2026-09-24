import json
import tempfile
import unittest
from pathlib import Path

from orbit.phase3.diarization import SpeakerDiarizer
from orbit.phase3.domain import DomainKnowledge
from orbit.phase3.export import export_json, export_markdown


class Phase3Tests(unittest.TestCase):
    def test_export_json_and_markdown(self):
        turns = [{"speaker": "Participant 1", "question": "Hi", "answer": "Hello"}]
        with tempfile.TemporaryDirectory() as directory:
            json_path = export_json(Path(directory) / "session.json", turns)
            markdown_path = export_markdown(Path(directory) / "session.md", turns)
            self.assertEqual(json.loads(json_path.read_text())["turns"], turns)
            self.assertIn("Participant 1", markdown_path.read_text())

    def test_domain_search_returns_matching_text(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "notes.md").write_text("Python deployment checklist", encoding="utf-8")
            self.assertIn("deployment", DomainKnowledge(directory).search("deployment"))

    def test_diarizer_alternates_and_continues(self):
        diarizer = SpeakerDiarizer()
        first = diarizer.label("hello there")
        continued = diarizer.label("hello there, more")
        second = diarizer.label("different thought")
        self.assertEqual(first.speaker, continued.speaker)
        self.assertNotEqual(first.speaker, second.speaker)


if __name__ == "__main__":
    unittest.main()
