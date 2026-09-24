"""Opt-in OCR for a shared screenshot or code image."""

from __future__ import annotations

from pathlib import Path


def extract_text(image_path: str | Path) -> str:
    """Extract text using optional pytesseract, with an actionable error."""
    try:
        from PIL import Image
        import pytesseract
    except ImportError as exc:
        raise RuntimeError(
            "OCR requires optional dependencies: pip install pillow pytesseract "
            "and install the Tesseract Windows package."
        ) from exc
    path = Path(image_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"OCR image does not exist: {path}")
    return pytesseract.image_to_string(Image.open(path)).strip()
