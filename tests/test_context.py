import types
import unittest

from orbit.config import ModelSettings
from orbit.llm.openai_llm import AnswerGenerator


class _FakeClient:
    def __init__(self):
        self.calls = []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        event = types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    delta=types.SimpleNamespace(content="answer")
                )
            ]
        )
        return [event]


class ContextTests(unittest.TestCase):
    def test_context_is_trimmed_to_configured_number_of_turns(self):
        client = _FakeClient()
        generator = AnswerGenerator(client, ModelSettings(context_turns=2))
        for question in ("one", "two", "three", "four"):
            for _ in generator.answer(question):
                pass

        self.assertEqual(generator.context_size, 4)
        messages = client.calls[-1]["messages"]
        contents = [message["content"] for message in messages]
        self.assertNotIn("one", contents)
        self.assertIn("three", contents)
        self.assertIn("four", contents)


if __name__ == "__main__":
    unittest.main()
