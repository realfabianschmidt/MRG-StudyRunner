from __future__ import annotations

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


def project_root(spec_path: str) -> Path:
    return Path(spec_path).resolve().parent


def common_datas(root: Path) -> list[tuple[str, str]]:
    return [
        (str(root / "web_interface"), "web_interface"),
        (str(root / "settings"), "settings"),
        (str(root / "saved_studies"), "saved_studies"),
        (str(root / "plugins"), "plugins"),
    ]


def common_hidden_imports() -> list[str]:
    return collect_submodules("server_app") + collect_submodules("plugins")
