"""Keeps docs/file-guide.md honest.

The owner wants every source file explained. This test walks the source
tree and fails when a .py/.js file is not mentioned in the guide, so the
guide cannot silently go stale.

`_source_files` skips a root that does not exist, because `tools/` and
`release_tools/` are absent from a packaged tree. That skip also means a
renamed source root makes this test pass while checking nothing at all --
it would scan two small script folders and call the application documented.
`test_source_roots_exist` closes that hole: rename a root and this file
fails loudly instead of going quiet. It matters most during the 1.0
package restructure (see docs/architecture-1.0-umbau.md), which moves
every path in SOURCE_DIRS.
"""
from __future__ import annotations

from pathlib import Path
import re
import re
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
    def test_source_roots_exist(self) -> None:
        """A moved root must fail here, not silently shrink the scan."""
        missing = [
            str(path.relative_to(REPO_ROOT))
            for path in SOURCE_DIRS + EXTRA_FILES
            if not path.exists()
        ]

        self.assertEqual(
            missing,
            [],
            "SOURCE_DIRS/EXTRA_FILES point at paths that no longer exist; "
            "update them in the same commit that moved the code: "
            + ", ".join(missing),
        )

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

    def test_documented_concrete_repository_paths_exist(self) -> None:
        guide_text = GUIDE.read_text(encoding="utf-8")
        paths = set(re.findall(r"`((?:software|release_tools|tools|docs|\.github)/[^`]+)`", guide_text))
        missing = []
        for value in paths:
            if any(character in value for character in "*{}|"):
                continue
            candidate = REPO_ROOT / value.rstrip("/")
            if not candidate.exists():
                missing.append(value)
        self.assertEqual(missing, [], "documented paths do not exist: " + ", ".join(sorted(missing)))


if __name__ == "__main__":
    unittest.main()
