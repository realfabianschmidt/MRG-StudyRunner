"""/api/admin/data-folder: show, choose, link, set up and reset the data folder."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.apps.server import create_app
from study_runner.apps.server.routes import admin
from study_runner.runtime_core.settings import data_folder


class DataFolderRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.install = self.base / "install"
        (self.install / "software").mkdir(parents=True)
        self.current = self.base / "current-data"
        env = {
            data_folder.ENV_DATA_DIR: str(self.current),
            data_folder.ENV_FROM_SETTING: "1",  # as if chosen in the settings
            "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            "STUDY_RUNNER_USER_CONFIG_DIR": str(self.base / "user"),
        }
        self.env = patch.dict(os.environ, env)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = create_app()
        self.client = self.app.test_client()
        for target, name in ((admin, "_install_root"), (admin, "_spawn_visible_restart"), (admin, "_exit_process_soon")):
            patcher = patch.object(target, name, return_value=self.install) if name == "_install_root" else patch.object(target, name)
            self.addCleanup(patcher.stop)
            setattr(self, name, patcher.start())

    def test_status_shows_the_current_folder(self) -> None:
        payload = self.client.get("/api/admin/data-folder").get_json()
        self.assertTrue(payload["external"])
        self.assertEqual(Path(payload["current"]), self.current.resolve())
        self.assertFalse(payload["set_by_environment"])

    def test_an_empty_folder_is_set_up_and_linked_then_the_server_restarts(self) -> None:
        target = self.base / "external"
        response = self.client.post("/api/admin/data-folder", json={"path": str(target)})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["kind"], "new")
        self.assertTrue(data_folder.is_data_folder(target))
        self.assertEqual(data_folder.read_setting(self.install), target.resolve())
        self._spawn_visible_restart.assert_called_once()

    def test_bringing_the_data_along_copies_studies_and_results(self) -> None:
        (Path(self.app.config["DATA_DIR"]) / "Study").mkdir(parents=True)
        target = self.base / "external"
        response = self.client.post("/api/admin/data-folder", json={"path": str(target), "copy_current": True})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertIn("saved_results", response.get_json()["copied"])
        self.assertTrue((target / "saved_results" / "Study").is_dir())

    def test_an_existing_data_folder_is_linked_without_copying(self) -> None:
        existing = self.base / "from-earlier"
        existing.mkdir()
        data_folder.write_marker(existing, "1.0.0")
        response = self.client.post("/api/admin/data-folder", json={"path": str(existing), "copy_current": True})
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/admin/data-folder", json={"path": str(existing)})
        self.assertEqual(response.get_json()["kind"], "link")
        self.assertEqual(data_folder.read_setting(self.install), existing.resolve())

    def test_reset_returns_to_the_program_folder(self) -> None:
        data_folder.write_setting(self.current, self.install)
        response = self.client.post("/api/admin/data-folder", json={"reset": True})
        self.assertEqual(response.get_json()["kind"], "default")
        self.assertIsNone(data_folder.read_setting(self.install))

    def test_refused_while_a_session_is_recorded(self) -> None:
        with patch("study_runner.apps.server.routes.update._activity",
                   return_value={"active_session": True, "study_run_status": ""}):
            response = self.client.post("/api/admin/data-folder", json={"path": str(self.base / "x")})
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(data_folder.read_setting(self.install))
        self._spawn_visible_restart.assert_not_called()

    def test_refused_when_set_by_a_hand_set_environment_variable(self) -> None:
        with patch.dict(os.environ, {data_folder.ENV_FROM_SETTING: ""}):
            response = self.client.post("/api/admin/data-folder", json={"reset": True})
        self.assertEqual(response.status_code, 409)

    def test_an_unusable_folder_is_refused_with_its_reason(self) -> None:
        response = self.client.post("/api/admin/data-folder", json={"path": str(self.install / "inside")})
        self.assertEqual(response.status_code, 400)
        self.assertIn("outside", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
