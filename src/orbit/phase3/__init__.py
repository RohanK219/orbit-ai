"""Optional Phase 3 capabilities.

The modules in this package are deliberately independent of the live audio
worker. Each feature can be enabled without changing the capture contract.
"""

from .diarization import SpeakerDiarizer
from .domain import DomainKnowledge
from .export import export_markdown, export_json
from .ocr import extract_text
from .translation import Translator

__all__ = [
    "DomainKnowledge",
    "SpeakerDiarizer",
    "Translator",
    "export_json",
    "export_markdown",
    "extract_text",
]
