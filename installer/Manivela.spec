# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

datas = [
    (str(ROOT / "servos" / "ui" / "pardilston.txt"), "servos/ui"),
]
datas += collect_data_files("customtkinter")
datas += collect_data_files("piper")
datas += collect_data_files("sounddevice")
datas += collect_data_files("ctranslate2")
datas += collect_data_files("onnxruntime")

binaries = []
binaries += collect_dynamic_libs("ctranslate2")
binaries += collect_dynamic_libs("onnxruntime")

nvidia = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
for name in ("cublas", "cudnn", "cuda_nvrtc"):
    folder = nvidia / name / "bin"
    if not folder.is_dir():
        continue
    for dll in folder.glob("*.dll"):
        binaries.append((str(dll), f"nvidia/{name}/bin"))

hiddenimports = collect_submodules("servos") + [
    "pynput.keyboard._win32",
    "pynput.mouse._win32",
    "pystray._win32",
    "faster_whisper",
    "ctranslate2",
    "tokenizers",
    "onnxruntime",
    "piper",
    "ddgs",
    "trafilatura",
]

a = Analysis(
    [str(ROOT / "servos" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["torch", "tensorflow"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Manivela",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Manivela",
)
