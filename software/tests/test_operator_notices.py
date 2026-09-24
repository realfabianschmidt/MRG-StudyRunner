"""Every error the participant sees must also reach the admin."""
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
from study_runner.runtime_core.studies.operator_notices import OperatorNoticeStore


class OperatorNoticeStoreTests(unittest.TestCase):
    def test_notices_persist_until_acknowledged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = OperatorNoticeStore(Path(temp_dir))
            first = store.add("Sensor lost", source="tablet")
            store.add("Upload failed", severity="warning")
            reloaded = OperatorNoticeStore(Path(temp_dir))
            self.assertEqual(len(reloaded.unacknowledged()), 2)
            self.assertEqual(reloaded.acknowledge(first["id"]), 1)
            self.assertEqual([n["message"] for n in reloaded.unacknowledged()], ["Upload failed"])
            self.assertEqual(reloaded.acknowledge(), 1)
            self.assertEqual(reloaded.unacknowledged(), [])


class OperatorNoticeRouteTests(unittest.TestCase):
    def _app(self, data_dir: str):
        env = {
            "STUDY_RUNNER_DATA_DIR": data_dir,
            "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
        }
        with patch.dict(os.environ, env, clear=False):
            return create_app()

    def test_a_tablet_error_becomes_an_admin_notice_even_without_a_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = self._app(temp_dir).test_client()
            client.post(
                "/api/study/session/client-event",
                json={"event": "participant_notice", "severity": "error", "message": "Could not start the study session."},
            )
            notices = client.get("/api/admin/notices").get_json()["notices"]
            self.assertEqual(len(notices), 1)
            self.assertEqual(notices[0]["source"], "tablet")
            self.assertEqual(client.post("/api/admin/notices/ack", json={"id": notices[0]["id"]}).get_json()["acknowledged"], 1)
            self.assertEqual(client.get("/api/admin/notices").get_json()["notices"], [])

    def test_a_refused_session_start_is_reported_on_the_run_and_as_a_notice(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._app(temp_dir)
            app.config["STUDY_RUN_STATE"].start("study-a", "tablet-1")
            client = app.test_client()
            response = client.post("/api/study/session/start", json={"participant_id": ""})
            self.assertEqual(response.status_code, 400)
            notices = client.get("/api/admin/notices").get_json()["notices"]
            self.assertEqual(notices[-1]["code"], "session_start_failed")
            self.assertIn("last_start_error", app.config["STUDY_RUN_STATE"].public())


if __name__ == "__main__":
    unittest.main()
