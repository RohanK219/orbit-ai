"""Fast, dependency-light checks for the PyInstaller packaging inputs.

This intentionally does not run PyInstaller.  It catches missing package
markers, malformed metadata, and accidental inclusion of optional backends in
the spec before a potentially slow build starts.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OPTIONAL_MODULES = {
    "PIL": ("phase3", "pillow"),
    "pytesseract": ("phase3", "pytesseract"),
    "sounddevice": ("phase3", "sounddevice"),
    "faster_whisper": ("local-stt", "faster-whisper"),
}
PHASE3_MODULES = {
    "__init__.py",
    "diarization.py",
    "domain.py",
    "export.py",
    "ocr.py",
    "translation.py",
}


def fail(message: str) -> None:
    print(f"packaging validation: ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    pyproject_path = ROOT / "pyproject.toml"
    spec_path = ROOT / "packaging" / "orbit-ai.spec"
    source_root = ROOT / "src" / "orbit"

    try:
        metadata = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        fail(f"cannot read {pyproject_path}: {exc}")

    project = metadata.get("project", {})
    optional = project.get("optional-dependencies", {})
    for group in ("phase3", "local-stt", "build"):
        if group not in optional:
            fail(f"pyproject.toml is missing optional dependency group [{group}]")

    for module, (group, requirement_name) in OPTIONAL_MODULES.items():
        if not any(requirement_name in requirement.lower() for requirement in optional[group]):
            fail(f"optional dependency group {group!r} does not declare {module}")

    phase3 = source_root / "phase3"
    if not (phase3 / "__init__.py").is_file():
        fail("src/orbit/phase3 is not a Python package")
    actual_modules = {path.name for path in phase3.glob("*.py")}
    missing_modules = PHASE3_MODULES - actual_modules
    if missing_modules:
        fail(f"Phase 3 package modules are missing: {', '.join(sorted(missing_modules))}")

    try:
        ast.parse(spec_path.read_text(encoding="utf-8"), filename=str(spec_path))
    except (OSError, SyntaxError) as exc:
        fail(f"invalid PyInstaller spec: {exc}")
    spec = spec_path.read_text(encoding="utf-8")
    for module in OPTIONAL_MODULES:
        if f'"{module}"' not in spec:
            fail(f"spec must exclude optional module {module}")
    if 'collect_submodules("orbit.phase3")' not in spec:
        fail("spec must collect orbit.phase3 package modules")

    print(
        "packaging validation: OK "
        f"(phase3 modules={len(PHASE3_MODULES)}, optional backends excluded)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
