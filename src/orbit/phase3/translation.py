"""Text translation using the configured OpenAI chat model."""

from __future__ import annotations

from openai import OpenAI


class Translator:
    """Translate finalized text without changing the live answer pipeline."""

    def __init__(self, client: OpenAI, model: str = "gpt-4o-mini") -> None:
        self._client = client
        self._model = model

    def translate(self, text: str, target_language: str) -> str:
        if not text.strip() or not target_language.strip():
            return text
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Translate the user's text to the requested language. "
                        "Preserve code, names, numbers, and formatting. Return "
                        "only the translation."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Target language: {target_language}\n\n{text}",
                },
            ],
            max_completion_tokens=max(64, min(4000, len(text) * 2)),
        )
        return (response.choices[0].message.content or text).strip()
