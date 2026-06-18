# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

sys.path.insert(0, str(Path(SPECPATH)))
from study_runner_server_common import common_datas, common_hidden_imports, software_root

root = software_root(SPECPATH)
# Make the editable Python software importable so collect_submodules finds study_runner.
sys.path.insert(0, str(root))

a = Analysis(
    [str(root / "server.py")],
    pathex=[str(root)],
    binaries=[],
    datas=common_datas(root),
    hiddenimports=common_hidden_imports(),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="study-runner-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
)
