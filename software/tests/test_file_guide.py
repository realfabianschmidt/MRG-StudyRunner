"""Keeps docs/file-guide.md honest.

The owner wants every source file explained. This test walks the source
tree and fails when a .py/.js file is not mentioned in the guide, so the
guide cannot silently go stale.
"""
from __future__ import annotations

from pathlib import Path
import unittest


SOFTWARE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SOFTWARE_ROOT.parent
GUIDE = REPO_ROOT / "docs" / "file-guide.md"

SOURCE_DIRS = [
    SOFTWARE_ROOT / "study_runner",
    REPO_ROOT / "tools",
    REPO_ROOT / "release_tools",
]
EXTRA_FILES = [SOFTWARE_ROOT / "server.py"]

# Whole groups that one guide line covers.
COVERED_BY_GROUP = {
    "__init__.py",  # "Empty package markers"
}
SKIP_DIR_NAMES = {"__pycache__", "build", "dist", "node_modules", "deepface_home", "logs", "recordings"}


def _source_files() -> list[Path]:
    files: list[Path] = list(EXTRA_FILES)
    for root in SOURCE_DIRS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".js"}:
                continue
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            files.append(path)
    return files


class FileGuideTests(unittest.TestCase):
    def test_every_source_file_is_documented(self) -> None:
        guide_text = GUIDE.read_text(encoding="utf-8")
        missing = []
        for path in _source_files():
            if path.name in COVERED_BY_GROUP:
                continue
            if path.name not in guide_text:
                missing.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(
            missing,
            [],
            "add one line per file to docs/file-guide.md: " + ", ".join(missing),
        )

    def test_release_python_scripts_use_snake_case(self) -> None:
        release_tools = REPO_ROOT / "release_tools"
        kebab_case_scripts = sorted(
            path.name
            for path in release_tools.glob("*.py")
            if "-" in path.stem
        )
        self.assertEqual(
            kebab_case_scripts,
            [],
            "release_tools Python scripts must use snake_case",
        )


if __name__ == "__main__":
    unittest.main()
