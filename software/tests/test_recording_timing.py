"""Machine wait settings are bounded and saved without changing quality rules."""

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
from study_runner.data_core.host.recording_timing import recording_timing


class RecordingTimingTests(unittest.TestCase):
    def test_defaults_and_bounds(self) -> None:
        self.assertEqual(recording_timing(None), {
            "start_wait_seconds": 8.0,
            "end_tail_wait_seconds": 10.0,
        })
        for invalid in (0, 31, float("nan"), True, "8"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                recording_timing({"start_wait_seconds": invalid})

    def test_machine_endpoint_persists_valid_waits_with_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": temp_dir,
                "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            }
            with patch.dict(os.environ, env, clear=False), patch("study_runner.apps.server.initialize_plugins"):
                app = create_app()
            try:
                client = app.test_client()
                before = client.get("/api/admin/recording-timing")
                self.assertEqual(before.status_code, 200)
                revision = before.json["revision"]
                saved = client.post("/api/admin/recording-timing", json={
                    "revision": revision,
                    "settings": {"start_wait_seconds": 12, "end_tail_wait_seconds": 15},
                })
                self.assertEqual(saved.status_code, 200, saved.json)
                self.assertEqual(saved.json["settings"]["end_tail_wait_seconds"], 15.0)
                self.assertEqual(client.get("/api/admin/recording-timing").json["settings"], saved.json["settings"])
                self.assertEqual(client.post("/api/admin/recording-timing", json={
                    "revision": revision, "settings": {"start_wait_seconds": 20},
                }).status_code, 409)
                self.assertEqual(client.post("/api/admin/recording-timing", json={
                    "settings": {"end_tail_wait_seconds": 0},
                }).status_code, 400)
            finally:
                app.config["SENSOR_COORDINATOR"].close(wait=True)


if __name__ == "__main__":
    unittest.main()
