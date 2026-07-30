# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

sys.path.insert(0, str(Path(SPECPATH)))
from study_runner_server_common import (
    common_binaries,
    common_datas,
    common_excludes,
    common_hidden_imports,
    software_root,
)

root = software_root(SPECPATH)
# Make the editable Python software importable so collect_submodules finds study_runner.
sys.path.insert(0, str(root))

a = Analysis(
    [str(root / "server.py")],
    pathex=[str(root)],
    binaries=common_binaries(),
    datas=common_datas(root),
    hiddenimports=common_hidden_imports(),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=common_excludes(),
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="study-runner-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX compression corrupts code-signed Mach-O binaries on macOS and adds
    # little value elsewhere; keep it off on every platform.
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="study-runner-server",
)
