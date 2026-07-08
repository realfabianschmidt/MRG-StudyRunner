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

from study_runner.backend import create_app


class RuntimeRoutesTests(unittest.TestCase):
    def test_health_and_runtime_info_routes(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_APP_MODE": "packaged",
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
                "STUDY_RUNNER_PORT": "3123",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()

            client = app.test_client()
            health = client.get("/api/health")
            runtime_info = client.get("/api/runtime-info")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(runtime_info.status_code, 200)

        health_payload = health.get_json()
        info_payload = runtime_info.get_json()
        self.assertEqual(health_payload["status"], "running")
        self.assertEqual(health_payload["app_mode"], "packaged")
        self.assertEqual(info_payload["port"], 3123)
        self.assertEqual(info_payload["admin_url"], "http://localhost:3123/admin")
        self.assertTrue(info_payload["participant_url"].startswith("http://"))
        self.assertTrue(info_payload["uses_external_storage"])

    def test_packaged_restart_returns_packaged_message(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_APP_MODE": "packaged",
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()

            response = app.test_client().post("/api/admin/restart")

        payload = response.get_json()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(payload["ok"])
        self.assertIn("packaged builds", payload["error"])

    def test_study_session_start_requires_participant_id(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()

            response = app.test_client().post(
                "/api/study/session/start",
                json={"study_id": "test", "participant_id": ""},
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertFalse(payload["ok"])
        self.assertIn("Participant ID", payload["error"])

    def test_active_study_sensor_toggle_is_study_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()

            app.config["HARDWARE_CONFIG"] = {"brainbit": {"enabled": False}}
            app.config["ACTIVE_STUDY_HARDWARE_CONFIG"] = {"brainbit": {"enabled": True}}
            response = app.test_client().post(
                "/api/admin/integrations/brainbit/enabled",
                json={"enabled": False},
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["study_controlled"])
        self.assertTrue(payload["enabled"])

    def test_active_study_lsl_toggle_updates_active_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()

            app.config["HARDWARE_CONFIG"] = {"lsl": {"enabled": True}}
            app.config["ACTIVE_STUDY_HARDWARE_CONFIG"] = {"lsl": {"enabled": True}}
            response = app.test_client().post(
                "/api/admin/integrations/lsl/enabled",
                json={"enabled": False},
            )

            active_config = app.config["ACTIVE_STUDY_HARDWARE_CONFIG"]

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["active_runtime_updated"])
        self.assertFalse(active_config["lsl"]["enabled"])

    def test_emotion_worker_repair_runtime_route_reports_package_and_model_state(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()

            with patch(
                "study_runner.integrations.local_emotion_worker.plugin.repair_runtime",
                return_value={
                    "dependency_install": {"status": "running"},
                    "model_asset_install": {"status": "queued"},
                },
            ):
                response = app.test_client().post("/api/admin/emotion-worker/repair-runtime")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["dependency_install"]["status"], "running")
        self.assertEqual(payload["model_asset_install"]["status"], "queued")


if __name__ == "__main__":
    unittest.main()
