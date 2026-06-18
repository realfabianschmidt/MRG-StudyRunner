from __future__ import annotations

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


def software_root(spec_path: str) -> Path:
    """Return the Python software folder that holds study_runner and study_content.

    The PyInstaller specs live in desktop/build_tools/pyinstaller/, so the editable
    Python software is a sibling of the desktop wrapper: <repo>/software.
    """
    return Path(spec_path).resolve().parents[2] / "software"


def common_datas(root: Path) -> list[tuple[str, str]]:
    return [
        (str(root / "study_runner" / "web"), "study_runner/web"),
        (str(root / "study_content"), "study_content"),
    ]


def common_hidden_imports() -> list[str]:
    return collect_submodules("study_runner.backend") + collect_submodules("study_runner.integrations")
