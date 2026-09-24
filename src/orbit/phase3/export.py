"""Explicit, user-triggered session export."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping


def _payload(turns: Iterable[Mapping[str, str]]) -> dict[str, object]:
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "turns": [dict(turn) for turn in turns],
    }


def export_json(path: str | Path, turns: Iterable[Mapping[str, str]]) -> Path:
    target = Path(path).expanduser()
    target.write_text(json.dumps(_payload(turns), indent=2), encoding="utf-8")
    return target


def export_markdown(path: str | Path, turns: Iterable[Mapping[str, str]]) -> Path:
    target = Path(path).expanduser()
    lines = ["# Orbit session export", "", f"Exported: {_payload(turns)['exported_at']}", ""]
    for index, turn in enumerate(turns, 1):
        lines.extend(
            [
                f"## Turn {index}",
                "",
                f"**{turn.get('speaker', 'Participant')} — Question**",
                "",
                turn.get("question", ""),
                "",
                "**Answer**",
                "",
                turn.get("answer", ""),
                "",
            ]
        )
    target.write_text("\n".join(lines), encoding="utf-8")
    return target
