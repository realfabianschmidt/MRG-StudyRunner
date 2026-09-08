"""Exercise architecture checks with import forms that previously escaped them."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TEST_ROOT = Path(__file__).resolve().parent
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

import test_import_boundaries as boundaries
from support.import_graph import iter_imports


class ImportGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.software = Path(self.temporary.name)
        self.root = self.software / "study_runner"
        for package in ("", "backend", "plugins", "plugins/sample"):
            directory = self.root / package
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "__init__.py").touch()

    def test_boundary_rules_catch_absolute_relative_and_local_submodule_imports(self) -> None:
        statements = (
            "import study_runner.apps.server as web",
            "from study_runner import backend as web",
            "from ... import backend as web",
            "from ...backend import create_app",
            "def delayed():\n    from study_runner import backend",
        )
        source = self.root / "plugins" / "sample" / "plugin.py"
        for statement in statements:
            with self.subTest(statement=statement):
                source.write_text(statement + "\n", encoding="utf-8-sig")
                with patch.object(boundaries, "PROJECT_ROOT", self.software), patch.object(
                    boundaries, "STUDY_RUNNER_ROOT", self.root
                ):
                    actual = boundaries._find_violations()
                self.assertIn(("plugins/sample/plugin.py", "study_runner.apps.server"), actual)

    def test_package_init_relative_import_uses_the_package_itself(self) -> None:
        source = self.root / "plugins" / "sample" / "__init__.py"
        source.write_text("from ... import backend\n", encoding="utf-8")
        modules = {edge.imported_module for edge in iter_imports(source, package_root=self.software)}
        self.assertIn("study_runner.apps.server", modules)
        self.assertNotIn("backend", modules)

    def test_from_import_preserves_attributes_without_inventing_modules(self) -> None:
        source = self.root / "plugins" / "sample" / "plugin.py"
        source.write_text("from study_runner.apps.server import create_app\n", encoding="utf-8")
        edges = list(iter_imports(source, package_root=self.software))
        self.assertEqual([(edge.imported_module, edge.names) for edge in edges], [
            ("study_runner.apps.server", ("create_app",)),
        ])

    def test_from_import_finds_a_module_file_without_importing_its_code(self) -> None:
        backend_module = self.root / "backend" / "dangerous.py"
        backend_module.write_text("raise RuntimeError('must never execute')\n", encoding="utf-8")
        source = self.root / "plugins" / "sample" / "plugin.py"
        source.write_text("from study_runner.apps.server import dangerous\n", encoding="utf-8")
        modules = {edge.imported_module for edge in iter_imports(source, package_root=self.software)}
        self.assertEqual(modules, {"study_runner.apps.server", "study_runner.apps.server.dangerous"})


if __name__ == "__main__":
    unittest.main()
