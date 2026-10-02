"""The operator's dashboard tile order: kept per computer, validated, resettable."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.runtime_core.settings import dashboard_layout_service as service  # noqa: E402


class DashboardLayoutServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.settings = Path(temp.name)

    def test_nothing_stored_means_the_default_order(self) -> None:
        self.assertEqual(service.load_layout(self.settings), {"columns": None})

    def test_a_layout_is_stored_and_read_back(self) -> None:
        stored = service.save_layout(self.settings, {"columns": [["am_hub", "brainbit"], ["mini_radar"]]})
        self.assertEqual(stored, {"columns": [["am_hub", "brainbit"], ["mini_radar"]]})
        self.assertEqual(service.load_layout(self.settings), stored)
        self.assertEqual(service.layout_path(self.settings).name, "dashboard_layout.local.json")

    def test_a_bad_layout_is_refused_with_a_reason(self) -> None:
        for payload, message in (
            (None, "list of columns"),
            ({"columns": [["am_hub"]]}, "exactly 2"),
            ({"columns": [["am_hub"], "brainbit"]}, "list of plugin keys"),
            ({"columns": [["Am-Hub"], []]}, "snake_case"),
            ({"columns": [["am_hub"], ["am_hub"]]}, "twice"),
            ({"columns": [[f"plugin_{index}" for index in range(65)], []]}, "at most 64"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(service.DashboardLayoutError, message):
                service.save_layout(self.settings, payload)
        self.assertFalse(service.layout_path(self.settings).exists())

    def test_a_broken_file_falls_back_to_the_default_order(self) -> None:
        service.layout_path(self.settings).write_text("{not json", encoding="utf-8")
        self.assertEqual(service.load_layout(self.settings), {"columns": None})
        service.layout_path(self.settings).write_text(json.dumps({"columns": [["x"], ["x"]]}), encoding="utf-8")
        self.assertEqual(service.load_layout(self.settings), {"columns": None})

    def test_reset_forgets_the_arrangement(self) -> None:
        service.save_layout(self.settings, {"columns": [["am_hub"], []]})
        self.assertEqual(service.reset_layout(self.settings), {"columns": None})
        self.assertEqual(service.load_layout(self.settings), {"columns": None})
        self.assertEqual(service.reset_layout(self.settings), {"columns": None})  # nothing to forget is fine


class DashboardLayoutRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        env = patch.dict(os.environ, {
            "STUDY_RUNNER_DATA_DIR": str(base / "data"),
            "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            "STUDY_RUNNER_USER_CONFIG_DIR": str(base / "user"),
        })
        env.start()
        self.addCleanup(env.stop)
        from study_runner.apps.server import create_app

        self.app = create_app()
        self.client = self.app.test_client()

    def test_put_get_and_delete(self) -> None:
        layout = {"columns": [["brainbit"], ["am_hub", "mini_radar"]]}
        response = self.client.put("/api/admin/dashboard-layout", json=layout)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.client.get("/api/admin/dashboard-layout").get_json()["layout"], layout)
        self.assertTrue((Path(self.app.config["SETTINGS_DIR"]) / service.LAYOUT_FILE).is_file())
        self.assertEqual(self.client.delete("/api/admin/dashboard-layout").get_json()["layout"], {"columns": None})

    def test_a_bad_layout_is_a_400_with_the_reason(self) -> None:
        response = self.client.put("/api/admin/dashboard-layout", json={"columns": [["x"]]})
        self.assertEqual(response.status_code, 400)
        self.assertIn("exactly 2", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
