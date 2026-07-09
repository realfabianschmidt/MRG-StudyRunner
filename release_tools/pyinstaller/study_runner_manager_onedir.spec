# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

sys.path.insert(0, str(Path(SPECPATH)))
from study_runner_server_common import software_root

root = software_root(SPECPATH)
repo_root = root.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(repo_root))

a = Analysis(
    [str(repo_root / "tools" / "study_runner_manager.py")],
    pathex=[str(repo_root), str(root)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "study_runner.update_keys",
    ],
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
    [],
    exclude_binaries=True,
    name="study-runner-manager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="study-runner-manager",
)
