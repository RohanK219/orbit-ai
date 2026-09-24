"""Launch the orbit-ai desktop application.

    python scripts/run_app.py

Equivalent to ``python -m orbit`` once the package is installed.
"""

from __future__ import annotations

import _bootstrap  # noqa: F401  (sys.path side effect)

from orbit.app import main

if __name__ == "__main__":
    raise SystemExit(main())
