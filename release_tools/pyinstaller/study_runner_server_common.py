from __future__ import annotations

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


def software_root(spec_path: str) -> Path:
    """Return the Python software folder that holds study_runner and study_content."""
    for candidate in Path(spec_path).resolve().parents:
        root = candidate / "software"
        if (root / "server.py").exists() and (root / "study_runner").exists():
            return root
    raise RuntimeError("Could not locate the repository software/ folder.")


def common_datas(root: Path) -> list[tuple[str, str]]:
    return [
        (str(root / "study_runner" / "web"), "study_runner/web"),
        (str(root / "study_content"), "study_content"),
    ]


def common_hidden_imports() -> list[str]:
    return (
        collect_submodules("study_runner.backend")
        + collect_submodules("study_runner.integrations")
        + [
            "study_runner.update_helper",
            "study_runner.update_keys",
            "study_runner.version",
        ]
    )
