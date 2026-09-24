"""Best-effort speaker labels for system-audio transcripts.

A mono loopback stream has no reliable speaker identity metadata. This module
therefore provides stable participant labels for turn export and leaves room
for a future embedding-based diarizer. It never claims labels are verified.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class SpeakerTurn:
    speaker: str
    text: str


class SpeakerDiarizer:
    """Assign stable, explicitly approximate labels to consecutive turns."""

    def __init__(self) -> None:
        self._next = 0
        self._last_text = ""

    def label(self, text: str) -> SpeakerTurn:
        # Repeated/continued fragments remain with the same speaker.
        if self._last_text and text.startswith(self._last_text[:24]):
            speaker = f"Participant {max(1, self._next)}"
        else:
            self._next = (self._next % 2) + 1
            speaker = f"Participant {self._next}"
        self._last_text = text
        return SpeakerTurn(speaker, text)

    def reset(self) -> None:
        self._next = 0
        self._last_text = ""
