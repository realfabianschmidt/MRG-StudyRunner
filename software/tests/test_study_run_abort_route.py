"""POST /api/admin/study-run/abort: end a live session, keep everything it recorded.

The route's own job is thin -- require a reason, find the currently
recording session, delegate to WithdrawalService.abort(), and update
study_run_state -- so these tests exercise exactly that plumbing rather than
re-proving abort()'s own behavior (test_withdrawal_service.py already does).
"""
from __future__ import annotations

import datetime as dt
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

from study_runner.apps.server import create_app
from study_runner.data_core.host.artifacts import ArtifactStore, SessionIdentity


SESSION_ID = "20260915T121234Z-abcdef"


class StudyRunAbortRouteTests(unittest.TestCase):
    def _app(self, data_dir: str):
        env = {
            "STUDY_RUNNER_DATA_DIR": data_dir,
            "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
        }
        with patch.dict(os.environ, env, clear=False):
            return create_app()

    def _live_session(self, app, data_dir: str) -> Path:
        """Reserve a real session directory and register it as recording,
        the way a real start would -- short of driving the whole native
        worker launch, which STUDY_RUNNER_DISABLE_HARDWARE rules out here.
        """
        identity = SessionIdentity(
            study_id="study-a",
            participant_id="p1",
            session_id=SESSION_ID,
            started_at=dt.datetime.now(dt.timezone.utc),
        )
        paths = ArtifactStore(Path(data_dir) / "saved_results").reserve(identity)
        (paths.root / "recording-plan.json").write_text(
            json.dumps({"status": "recording"}), encoding="utf-8"
        )
        recording_runtime = app.config["RECORDING_RUNTIME_SERVICE"]
        recording_runtime._active_paths[SESSION_ID] = paths.root
        return paths.root

    def test_a_reason_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._app(temp_dir)
            for body in ({}, {"reason": ""}, {"reason": "   "}):
                response = app.test_client().post("/api/admin/study-run/abort", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.get_json()["ok"])

    def test_aborting_with_nothing_recording_is_a_404(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._app(temp_dir)
            response = app.test_client().post(
                "/api/admin/study-run/abort", json={"reason": "nothing is running"}
            )
            self.assertEqual(response.status_code, 404)
            self.assertFalse(response.get_json()["ok"])

    def test_abort_stops_the_session_updates_run_state_and_keeps_the_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._app(temp_dir)
            session_root = self._live_session(app, temp_dir)

            response = app.test_client().post(
                "/api/admin/study-run/abort",
                json={"reason": "tablet unresponsive", "requested_by": "operator"},
            )

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["run_state"]["status"], "aborted")
            self.assertEqual(payload["run_state"]["aborted_reason"], "tablet unresponsive")
            self.assertEqual(payload["withdrawal"]["status"], "withdrawn")

            marker = json.loads((session_root / "WITHDRAWN.json").read_text(encoding="utf-8"))
            self.assertEqual(marker["kind"], "admin_abort")
            self.assertEqual(marker["reason"], "tablet unresponsive")
            # The recording plan this fixture wrote is untouched -- nothing
            # about an abort deletes what was already on disk.
            self.assertTrue((session_root / "recording-plan.json").is_file())

    def test_a_second_abort_of_the_same_session_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._app(temp_dir)
            self._live_session(app, temp_dir)
            client = app.test_client()
            first = client.post("/api/admin/study-run/abort", json={"reason": "stuck"})
            self.assertEqual(first.status_code, 200)
            second = client.post("/api/admin/study-run/abort", json={"reason": "stuck again"})

        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.get_json()["ok"])


if __name__ == "__main__":
    unittest.main()
