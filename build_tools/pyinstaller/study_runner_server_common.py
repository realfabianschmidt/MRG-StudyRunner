from __future__ import annotations

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


def project_root(spec_path: str) -> Path:
    return Path(spec_path).resolve().parents[1]


def common_datas(root: Path) -> list[tuple[str, str]]:
    return [
        (str(root / "study_runner" / "web"), "study_runner/web"),
        (str(root / "study_content"), "study_content"),
    ]


def common_hidden_imports() -> list[str]:
    return collect_submodules("study_runner.backend") + collect_submodules("study_runner.integrations")
