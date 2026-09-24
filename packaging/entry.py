"""Frozen-app entry point.

PyInstaller freezes a single script, and importing a package's ``__main__`` as
the entry does not always resolve cleanly once frozen. A tiny top-level script
that adds ``src`` to the path and calls :func:`orbit.app.main` is the reliable
form, and it works identically whether run from source or from the bundled exe.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _ensure_src_on_path() -> None:
    """Make ``orbit`` importable when running from source.

    When frozen, PyInstaller has already placed the package on ``sys.path``, so
    this only matters for running ``python packaging/entry.py`` directly.
    """
    if getattr(sys, "frozen", False):
        return
    src = Path(__file__).resolve().parent.parent / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


def main() -> int:
    _ensure_src_on_path()
    # Qt on Windows picks the right platform plugin on its own; setting this
    # guards against a stale value in the inherited environment.
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    from orbit.app import main as app_main

    return app_main()


if __name__ == "__main__":
    raise SystemExit(main())
