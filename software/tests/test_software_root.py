"""The software root is found by marker, never by counting directory levels.

`Path(__file__).resolve().parents[4]` was spread across four modules. It is
silent when it is wrong: move a module one level and it still returns *a*
directory, so `get_project_base_dir()` resolves `study_content/` and
`saved_results/` one folder too high with nothing raising and no test failing.

These tests pin the replacement rather than the symptom, because the 1.0
package restructure (docs/architecture-1.0-umbau.md) changes the depth of
every one of those modules.
"""
from __future__ import annotations

from pathlib import Path
import ast
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.shared.software_root import find_software_root, is_software_root


class SoftwareRootTests(unittest.TestCase):
    def test_finds_the_real_software_folder(self) -> None:
        self.assertEqual(find_software_root(Path(__file__)), PROJECT_ROOT)

    def test_result_does_not_depend_on_nesting_depth(self) -> None:
        """The point of the marker search: depth may change, the answer may not."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve() / "software"
            (root / "study_runner").mkdir(parents=True)
            (root / "server.py").touch()

            shallow = root / "study_runner" / "a.py"
            deep = root / "study_runner" / "b" / "c" / "d" / "e" / "f.py"
            deep.parent.mkdir(parents=True)

            self.assertEqual(find_software_root(shallow), root)
            self.assertEqual(find_software_root(deep), root)

    def test_raises_instead_of_returning_a_wrong_folder(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            stray = Path(raw).resolve() / "no" / "markers" / "here.py"
            stray.parent.mkdir(parents=True)

            with self.assertRaises(RuntimeError):
                find_software_root(stray)

    def test_requires_both_markers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            (root / "server.py").touch()
            self.assertFalse(is_software_root(root), "server.py alone is not enough")

            (root / "study_runner").mkdir()
            self.assertTrue(is_software_root(root))

    def test_no_module_counts_parents_to_reach_the_software_root(self) -> None:
        """A reintroduced `parents[N]` would fail silently; catch it here instead.

        Scoped to the modules that resolve the software root. `parents[1]` and
        similar short hops inside one package are fine and stay allowed.
        """
        watched = [
            PROJECT_ROOT / "study_runner" / "runtime_core" / "settings" / "runtime_config.py",
            PROJECT_ROOT / "study_runner" / "plugin_framework" / "process_host.py",
            PROJECT_ROOT / "study_runner" / "plugins" / "camera_emotion" / "worker" / "plugin.py",
            PROJECT_ROOT / "study_runner" / "plugins" / "mr60_mini_radar" / "tools" / "ble_mr60_receiver.py",
        ]
        missing = [str(path) for path in watched if not path.exists()]
        self.assertEqual(missing, [], f"update these paths when the modules move: {missing}")

        offenders = []
        for path in watched:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                # Match `<...>.parents[N]` where N >= 2 -- the depth-counting shape.
                if not isinstance(node, ast.Subscript):
                    continue
                value = node.value
                if not (isinstance(value, ast.Attribute) and value.attr == "parents"):
                    continue
                index = node.slice
                if isinstance(index, ast.Constant) and isinstance(index.value, int) and index.value >= 2:
                    offenders.append(f"{path.name}:{node.lineno} uses parents[{index.value}]")

        self.assertEqual(
            offenders,
            [],
            "use find_software_root() instead of counting parents: " + ", ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
