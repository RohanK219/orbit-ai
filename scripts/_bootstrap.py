"""Makes ``src/`` importable when running scripts directly.

Avoids requiring ``pip install -e .`` just to try Phase 0. Import this first in
any script under ``scripts/``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
