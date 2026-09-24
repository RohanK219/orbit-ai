# PyInstaller spec for orbit-ai.
#
# Produces a single windowed .exe that runs on a clean Windows machine with no
# Python, no venv, and no environment setup. That is the whole point: build on
# one machine, run on another.
#
# Build with the helper script (recommended):
#     powershell -File scripts\build_exe.ps1
# or directly:
#     pyinstaller packaging\orbit-ai.spec --noconfirm
#
# The two things that break a naive Qt+audio bundle, both handled below:
#   1. PySide6 Qt platform plugins (qwindows.dll) must be collected, or the exe
#      exits instantly with "could not find or load the Qt platform plugin".
#   2. PyAudioWPatch ships a native PortAudio DLL that PyInstaller does not pick
#      up from imports alone, so it is collected explicitly.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

# Spec files are executed before Analysis applies ``pathex``. Add the source
# tree here as well so collect_submodules can see the local Phase 3 package
# when PyInstaller is invoked from either the repository root or packaging/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

block_cipher = None

# -- native audio library ---------------------------------------------------
# Grab the bundled PortAudio DLL that ships inside the pyaudiowpatch wheel.
_pyaudio_binaries = collect_dynamic_libs("pyaudiowpatch")

# -- keyring backends --------------------------------------------------------
# keyring resolves its Windows Credential Manager backend dynamically, so its
# backend submodules must be forced in or storing the API key fails at runtime.
_hidden = collect_submodules("keyring.backends") + collect_submodules("orbit.phase3") + [
    "win32ctypes.core",  # keyring's Windows backend dependency
]

# -- Qt modules we do not use ------------------------------------------------
# PySide6-Essentials is already trimmed, but excluding these shaves further off
# the bundle and avoids pulling optional DLLs we never touch.
_excludes = [
    "PySide6.QtNetwork",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.Qt3DCore",
    "PySide6.QtMultimedia",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "tkinter",
    "matplotlib",
    "PIL",
    "pytesseract",
    "sounddevice",
    "faster_whisper",
    "pytest",
]

a = Analysis(
    ["entry.py"],
    pathex=["../src"],
    binaries=_pyaudio_binaries,
    datas=[],
    hiddenimports=_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="orbit-ai",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX compression trips antivirus far more than it saves space
    runtime_tmpdir=None,
    console=False,  # windowed: no black console window behind the overlay
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="orbit.ico",  # add an .ico here later for taskbar/Explorer polish
)
