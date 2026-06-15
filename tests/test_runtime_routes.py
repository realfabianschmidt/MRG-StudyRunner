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

from server_app import create_app


class RuntimeRoutesTests(unittest.TestCase):
    def test_health_and_runtime_info_routes(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_APP_MODE": "desktop",
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
        self.assertEqual(health_payload["app_mode"], "desktop")
        self.assertEqual(info_payload["port"], 3123)
        self.assertEqual(info_payload["admin_url"], "http://localhost:3123/admin")
        self.assertTrue(info_payload["participant_url"].startswith("http://"))
        self.assertTrue(info_payload["uses_external_storage"])


if __name__ == "__main__":
    unittest.main()
