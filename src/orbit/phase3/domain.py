"""Local domain-knowledge retrieval with no database or network calls."""

from __future__ import annotations

from pathlib import Path


class DomainKnowledge:
    """Load text/markdown references and return small relevant snippets."""

    _EXTENSIONS = {".txt", ".md", ".rst", ".py", ".json", ".yaml", ".yml"}

    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory).expanduser() if directory else None

    def search(self, query: str, max_chars: int = 5000) -> str:
        if self.directory is None or not self.directory.is_dir():
            return ""
        terms = {word.lower() for word in query.split() if len(word) > 2}
        matches: list[str] = []
        for path in sorted(self.directory.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in self._EXTENSIONS:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if not terms:
                score = 1
            else:
                score = sum(text.lower().count(term) for term in terms)
            if score:
                matches.append(f"## {path.name}\n{text[:1500]}")
        return "\n\n".join(matches)[:max_chars]
