from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services import results_service


class ResultsServicePathTests(unittest.TestCase):
    def test_project_root_points_to_software_folder(self) -> None:
        self.assertEqual(results_service._project_root().name, "software")

    def test_labrecorder_relative_path_resolves_under_software(self) -> None:
        resolved = results_service._resolve_project_path(
            "study_runner/integrations/brainbit/recordings",
            results_service._project_root(),
        )

        expected = (
            Path(__file__).resolve().parents[1]
            / "study_runner"
            / "integrations"
            / "brainbit"
            / "recordings"
        ).resolve()
        self.assertEqual(resolved, expected)


if __name__ == "__main__":
    unittest.main()
