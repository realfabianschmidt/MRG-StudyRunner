"""Keep DataCore boundaries visible and verify the committed cycle gate."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("measure_structure", REPO_ROOT / "tools" / "measure_structure.py")
assert SPEC is not None and SPEC.loader is not None
metrics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metrics)


class StructureMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        self.software = self.repo / "software"
        self.root = self.software / "study_runner"
        self.root.mkdir(parents=True)

    def write_module(self, relative: str, source: str = "") -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")

    def measure(self) -> dict:
        with patch.object(metrics, "REPO_ROOT", self.repo), patch.object(
            metrics, "SOFTWARE_ROOT", self.software
        ), patch.object(metrics, "STUDY_RUNNER_ROOT", self.root):
            return metrics.measure()

    def test_target_nested_packages_are_measured_independently(self) -> None:
        for relative in (
            "apps/server/module.py",
            "apps/ui/module.py",
            "extensions/sensors/module.py",
            "extensions/destinations/module.py",
        ):
            self.write_module(relative, "VALUE = 1\n")
        current = self.measure()
        self.assertEqual(current["lines_per_package"]["apps.server"], 1)
        self.assertEqual(current["lines_per_package"]["apps.ui"], 1)
        self.assertEqual(current["lines_per_package"]["extensions.sensors"], 1)
        self.assertEqual(current["lines_per_package"]["extensions.destinations"], 1)

    def test_detects_a_transitive_cycle_inside_the_future_data_core_layout(self) -> None:
        self.write_module("data_core/host/runtime.py", "from .. import worker\n")
        self.write_module("data_core/worker/runtime.py", "import study_runner.data_core.contract.wire\n")
        self.write_module("data_core/contract/wire.py", "import study_runner.data_core.host.runtime\n")
        current = self.measure()
        self.assertEqual(current["cycle_pairs"], [
            ["data_core.contract", "data_core.host"],
            ["data_core.contract", "data_core.worker"],
            ["data_core.host", "data_core.worker"],
        ])
        self.assertEqual(current["lines_per_package"]["data_core.host"], 1)
        self.assertEqual(current["lines_per_package"]["data_core.worker"], 1)
        self.assertEqual(current["lines_per_package"]["data_core.contract"], 1)

    def test_counts_one_statement_to_area_edge_and_ignores_bare_modules(self) -> None:
        self.write_module("backend/app.py", "from study_runner.shared import constants\nfrom study_runner import version\n")
        self.write_module("shared/constants.py", "VALUE = 1\n")
        self.write_module("version.py", "VERSION = 'test'\n")
        current = self.measure()
        self.assertEqual(current["cross_package_import_edges"], 1)
        self.assertEqual(current["cycle_count"], 0)
        self.assertNotIn("version", current["lines_per_package"])

    def test_replacing_an_old_cycle_with_a_new_one_still_fails_the_gate(self) -> None:
        self.write_module("backend/app.py")
        baseline = self.measure()
        baseline.update(cycle_count=1, cycle_pairs=[["backend", "shared"]])
        current = {**baseline, "cycle_pairs": [["data_core.host", "data_core.worker"]]}
        self.assertTrue(any("import cycles changed" in problem for problem in metrics.check(current, baseline)))


if __name__ == "__main__":
    unittest.main()
