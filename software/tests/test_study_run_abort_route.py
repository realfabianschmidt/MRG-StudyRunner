"""POST /api/admin/study-run/abort: end a live session, keep everything it recorded.

The route's own job is thin -- it delegates to
runtime_core/studies/study_run_abort.py, which freezes a recording session via
WithdrawalService.abort() or, when nothing records, closes the running run -- so these tests exercise exactly that plumbing rather than
re-proving abort()'s own behavior (test_withdrawal_service.py already does).
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

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
        (paths.recording_plan_file).write_text(
            json.dumps({"status": "recording"}), encoding="utf-8"
        )
        recording_runtime = app.config["RECORDING_RUNTIME_SERVICE"]
        recording_runtime._active_paths[SESSION_ID] = paths.root
        recording_runtime.freeze_worker = Mock(return_value={"already_closed": True})
        recording_runtime.shutdown_worker = Mock(return_value={"ok": True})
        app.config["STUDY_RUN_STATE"].start("study-a", "tablet-1")
        app.config["SESSION_STORE"].start_or_reuse({
            "session_id": SESSION_ID,
            "study_id": "study-a",
            "participant_id": "p1",
            "client_id": "tablet-1",
        })
        return paths.root

    def _prepared_trial(self, app, *, started: bool) -> dict:
        events = app.config["TRIAL_EVENT_SERVICE"]
        deadline = (time.time() + 60) * 1000
        payload = {
            "event_id": "start-abort-test",
            "stop_event_id": "stop-abort-test",
            "stimulus_id": "stimulus-abort-test",
            "study_id": "study-a",
            "participant_id": "p1",
            "session_id": SESSION_ID,
            "client_id": "tablet-1",
            "question_index": 0,
            "planned_start_epoch_ms": deadline - 30_000,
            "planned_deadline_epoch_ms": deadline,
        }
        events.prepare(payload)
        events.arm_deadline(payload["stimulus_id"], deadline, {**payload, "event_id": payload["stop_event_id"]}, lambda _event: {})
        if started:
            events.execute(payload["event_id"], "trial_start", payload, lambda _event: {"ok": True})
        return payload

    def test_a_reason_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._app(temp_dir)
            for body in ({}, {"reason": ""}, {"reason": "   "}):
                response = app.test_client().post("/api/admin/study-run/abort", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.get_json()["ok"])

    def test_aborting_with_nothing_running_is_a_404(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._app(temp_dir)
            response = app.test_client().post(
                "/api/admin/study-run/abort", json={"reason": "nothing is running"}
            )
            self.assertEqual(response.status_code, 404)
            self.assertFalse(response.get_json()["ok"])

    def test_a_running_study_without_a_recording_can_still_be_aborted(self) -> None:
        """The tablet's session start failed: the run shows "running" but no
        recording exists. Abort must still end it instead of answering 404."""
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._app(temp_dir)
            app.config["STUDY_RUN_STATE"].start("study-a", "tablet-1")
            session = app.config["SESSION_STORE"].start_or_reuse(
                {"study_id": "study-a", "participant_id": "p1", "client_id": "tablet-1"}
            )

            response = app.test_client().post(
                "/api/admin/study-run/abort", json={"reason": "sensor never connected"}
            )

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["outcome"], "run_only")
            self.assertEqual(payload["run_state"]["status"], "aborted")
            self.assertEqual(payload["run_state"]["aborted_reason"], "sensor never connected")
            self.assertIn(session["session_id"], payload["closed_sessions"])
            self.assertEqual(app.config["SESSION_STORE"].list_active(), [])

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
            self.assertTrue((session_root / "meta" / "recording-plan.json").is_file())

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

    def test_failed_freeze_stays_aborting_and_can_be_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._app(temp_dir)
            self._live_session(app, temp_dir)
            recorder = app.config["RECORDING_RUNTIME_SERVICE"]
            recorder.freeze_worker.side_effect = RuntimeError("freeze failed")
            client = app.test_client()
            first = client.post("/api/admin/study-run/abort", json={"reason": "stop now"})
            self.assertEqual(first.status_code, 503)
            self.assertEqual(app.config["STUDY_RUN_STATE"].public()["status"], "aborting")
            self.assertEqual(len(app.config["SESSION_STORE"].list_active()), 1)

            recorder.freeze_worker.side_effect = None
            second = client.post("/api/admin/study-run/abort", json={"reason": "stop now"})
            self.assertEqual(second.status_code, 200)
            self.assertEqual(app.config["STUDY_RUN_STATE"].public()["status"], "aborted")

    def test_unmatched_active_recorder_never_reports_a_successful_abort(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._app(temp_dir)
            self._live_session(app, temp_dir)
            runtime = app.config["RECORDING_RUNTIME_SERVICE"]
            runtime.current_status = Mock(return_value={"session_id": "different", "status": "recording"})
            response = app.test_client().post("/api/admin/study-run/abort", json={"reason": "stop now"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(app.config["STUDY_RUN_STATE"].public()["status"], "aborting")

    def test_old_frozen_recorder_does_not_count_as_the_current_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._app(temp_dir)
            session_root = self._live_session(app, temp_dir)
            (session_root / "meta" / "recording-plan.json").write_text(json.dumps({"status": "frozen"}), encoding="utf-8")
            runtime = app.config["RECORDING_RUNTIME_SERVICE"]
            runtime.current_status = Mock(return_value={"session_id": SESSION_ID, "status": "frozen"})
            response = app.test_client().post("/api/admin/study-run/abort", json={"reason": "stop now"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["outcome"], "run_only")
            runtime.freeze_worker.assert_not_called()

    def test_restart_recovers_the_recording_plan_before_confirming_abort(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first_app = self._app(temp_dir)
            self._live_session(first_app, temp_dir)
            first_app.config["STUDY_RUN_STATE"].begin_abort("stop now")
            config_file = Path(first_app.config["CONFIG_FILE"])
            config = json.loads(config_file.read_text(encoding="utf-8"))
            config_file.write_text(json.dumps({**config, "study_id": "study-a"}), encoding="utf-8")
            restarted = self._app(temp_dir)
            runtime = restarted.config["RECORDING_RUNTIME_SERVICE"]
            self.assertIsNone(runtime.current_status())
            runtime.freeze_worker = Mock(return_value={"already_closed": True})
            runtime.shutdown_worker = Mock(return_value={"ok": True})
            response = restarted.test_client().post("/api/admin/study-run/abort", json={"reason": "stop now"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["outcome"], "recording")
            runtime.freeze_worker.assert_called_once()

    def test_aborting_blocks_new_run_and_trial_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._app(temp_dir)
            self._live_session(app, temp_dir)
            app.config["STUDY_RUN_STATE"].begin_abort("stop now")
            client = app.test_client()
            self.assertEqual(client.post("/api/start", json={}).status_code, 409)
            self.assertEqual(client.post("/api/trial/prepare", json={}).status_code, 409)

    def test_abort_cancels_an_unstarted_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._app(temp_dir)
            self._live_session(app, temp_dir)
            payload = self._prepared_trial(app, started=False)
            with patch("study_runner.apps.server.routes.admin.stop_trial_session") as stopper:
                response = app.test_client().post("/api/admin/study-run/abort", json={"reason": "stop now"})
            self.assertEqual(response.status_code, 200)
            stopper.assert_not_called()
            snapshot = app.config["TRIAL_EVENT_SERVICE"].snapshot()
            self.assertEqual(snapshot["deadlines"][payload["stimulus_id"]]["status"], "cancelled")
            self.assertTrue(any(item["event_id"] == payload["event_id"] for item in snapshot["prepare_cancellations"]))

    def test_abort_confirms_a_started_trial_stop_before_finishing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._app(temp_dir)
            self._live_session(app, temp_dir)
            payload = self._prepared_trial(app, started=True)
            with patch("study_runner.apps.server.routes.admin.stop_trial_session", return_value={"ok": True}) as stopper:
                response = app.test_client().post("/api/admin/study-run/abort", json={"reason": "stop now"})
            self.assertEqual(response.status_code, 200)
            stopper.assert_called_once()
            snapshot = app.config["TRIAL_EVENT_SERVICE"].snapshot()
            self.assertEqual(snapshot["events"][payload["stop_event_id"]]["status"], "done")

    def test_failed_trial_stop_keeps_aborting_and_replays_the_same_stop_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._app(temp_dir)
            self._live_session(app, temp_dir)
            payload = self._prepared_trial(app, started=True)
            with patch("study_runner.apps.server.routes.admin.stop_trial_session", side_effect=RuntimeError("device did not stop")):
                first = app.test_client().post("/api/admin/study-run/abort", json={"reason": "stop now"})
            self.assertEqual(first.status_code, 503)
            self.assertEqual(app.config["STUDY_RUN_STATE"].public()["status"], "aborting")
            with patch("study_runner.apps.server.routes.admin.stop_trial_session", return_value={"ok": True}) as stopper:
                second = app.test_client().post("/api/admin/study-run/abort", json={"reason": "stop now"})
            self.assertEqual(second.status_code, 200)
            self.assertEqual(stopper.call_args.args[0]["event_id"], payload["stop_event_id"])


if __name__ == "__main__":
    unittest.main()
