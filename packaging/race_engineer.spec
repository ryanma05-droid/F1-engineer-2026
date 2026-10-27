# PyInstaller build file: bundles Python, the engineer and every library into dist/RaceEngineer/.
#
#     uv run --group build pyinstaller packaging/race_engineer.spec --noconfirm
#
# One folder, not a single .exe: a single file unpacks ~500 MB on every start (slow, and often
# flagged by antivirus). The installer (packaging/installer.iss) wraps the folder.
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "config.yaml"), "."),
    (str(ROOT / "data" / "tracks"), "data/tracks"),
    (str(ROOT / "docs" / "telemetry-guide.md"), "docs"),
]
binaries = []
hiddenimports = collect_submodules("engineer")
# Libraries with data files or native parts PyInstaller can't see by itself: the Piper voice
# (espeak-ng data), speech recognition (model runtime, tokenizer, VAD model), audio (PortAudio).
for pkg in ("piper", "onnxruntime", "faster_whisper", "ctranslate2", "tokenizers",
            "sounddevice", "_sounddevice_data", "pyarrow", "zstandard"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h
if __import__("sys").platform == "win32":
    for pkg in ("pyttsx3", "comtypes"):
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h + ["pyttsx3.drivers.sapi5"]

# Piper's voice-training code (needs torch) is not part of the app.
hiddenimports = [h for h in hiddenimports if not h.startswith("piper.train")]

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # duckdb is only for the developer's SQL command (python -m engineer.features query).
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "piper.train", "duckdb"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RaceEngineer",
    console=True,                 # the engineer prints lap lines and radio calls
    icon=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="RaceEngineer")
