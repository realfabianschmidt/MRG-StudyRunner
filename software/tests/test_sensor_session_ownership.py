"""A late session end from participant A never reaches participant B."""
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

from study_runner.apps.server import create_app  # noqa: E402
from study_runner.contracts.plugin_api import Plugin  # noqa: E402
from study_runner.plugin_framework import driver_runtime  # noqa: E402
from study_runner.runtime_core.studies.sensor_session_ownership import SensorSessionOwnership  # noqa: E402

HELPERS = "study_runner.apps.server.routes.helpers"


class OwnershipLedgerTests(unittest.TestCase):
    def test_setup_for_the_next_person_blocks_the_previous_reset(self) -> None:
        ledger = SensorSessionOwnership()
        ledger.claim_session(["brainbit"], "A")
        ledger.claim_setup("brainbit", active_session_id=None)
        self.assertFalse(ledger.may_end("brainbit", "A"))

    def test_setup_inside_the_running_session_stays_with_it(self) -> None:
        ledger = SensorSessionOwnership()
        ledger.claim_session(["brainbit"], "A")
        ledger.claim_setup("brainbit", active_session_id="A")
        self.assertTrue(ledger.may_end("brainbit", "A"))

    def test_a_timed_out_end_stays_pending_until_the_plugin_confirms_it(self) -> None:
        ledger = SensorSessionOwnership()
        ledger.ended("brainbit", "A", outcome_known=False)
        self.assertEqual(ledger.unresolved("brainbit"), "A")
        self.assertFalse(ledger.reconcile("brainbit", {"session_end": {"in_progress": "A"}}))
        self.assertTrue(ledger.reconcile("brainbit", {"session_end": {"last_completed": "A"}}))
        self.assertIsNone(ledger.unresolved("brainbit"))


class DriverSessionEndLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        driver_runtime._session_end_ledger.update(in_progress=None, last_completed=None, last_failed=None)
        self.calls: list[str] = []
        self.plugin = Plugin(
            key="fixture",
            label="Fixture",
            category="sensor",
            config_key="fixture",
            get_status=lambda context: {"status": "running"},
            on_session_end=lambda context, options: self.calls.append(options["session_id"]),
        )

    def test_status_reports_the_last_finished_end_and_repeats_are_ignored(self) -> None:
        driver_runtime._dispatch(self.plugin, None, "session_end", {"session_id": "A"})
        driver_runtime._dispatch(self.plugin, None, "session_end", {"session_id": "A"})
        status, _ = driver_runtime._dispatch(self.plugin, None, "status", {})
        self.assertEqual(self.calls, ["A"])
        self.assertEqual(status["session_end"]["last_completed"], "A")
        self.assertIsNone(status["session_end"]["in_progress"])


def _app(data_dir: str):
    env = {
        "STUDY_RUNNER_DATA_DIR": data_dir,
        "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
        "STUDY_RUNNER_DISABLE_HARDWARE": "0",
    }
    with patch.dict(os.environ, env, clear=False), patch("study_runner.apps.server.initialize_plugins"):
        return create_app()


class LateSessionEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = _app(self.temp_dir.name)

    def tearDown(self) -> None:
        self.app.config["SENSOR_COORDINATOR"].close(wait=True)
        self.temp_dir.cleanup()

    def _session_b_running(self) -> None:
        self.app.config["ACTIVE_STUDY_HARDWARE_CONFIG"] = {"brainbit": {"enabled": True}}
        self.app.config["ACTIVE_STUDY_SENSOR_PLUGINS"] = ["brainbit"]
        self.app.config["ACTIVE_STUDY_SENSOR_SESSION_ID"] = "B"
        ledger = SensorSessionOwnership()
        ledger.claim_session(["brainbit"], "B")
        self.app.config["SENSOR_SESSION_OWNERSHIP"] = ledger

    def test_finalization_of_a_does_not_end_session_b(self) -> None:
        from study_runner.apps.server.routes.helpers import _end_study_sensor_session

        self._session_b_running()
        with self.app.app_context(), patch(f"{HELPERS}.run_session_end") as session_end:
            result = _end_study_sensor_session(session_id="A", notify=True, plugin_keys=["brainbit"])
        session_end.assert_not_called()
        self.assertTrue(result["superseded"])
        self.assertEqual(result["not_reset_plugins"], ["brainbit"])
        self.assertEqual(self.app.config["ACTIVE_STUDY_SENSOR_SESSION_ID"], "B")
        self.assertEqual(self.app.config["ACTIVE_STUDY_SENSOR_PLUGINS"], ["brainbit"])

    def test_setup_for_the_next_person_is_not_reset_by_a_late_end(self) -> None:
        from study_runner.apps.server.routes.helpers import _end_study_sensor_session

        ledger = SensorSessionOwnership()
        ledger.claim_session(["brainbit"], "A")
        ledger.claim_setup("brainbit", active_session_id=None)
        self.app.config["SENSOR_SESSION_OWNERSHIP"] = ledger
        with self.app.app_context(), patch(f"{HELPERS}.run_session_end") as session_end:
            _end_study_sensor_session(session_id="A", notify=True, plugin_keys=["brainbit"])
        session_end.assert_not_called()

    def test_a_timed_out_end_blocks_the_next_session_until_confirmed(self) -> None:
        from study_runner.apps.server.routes.helpers import (
            _end_study_sensor_session,
            _start_study_sensor_runtime,
        )

        self.app.config["ACTIVE_STUDY_SENSOR_PLUGINS"] = ["brainbit"]
        self.app.config["ACTIVE_STUDY_SENSOR_SESSION_ID"] = "A"
        timeout = {"notified_plugins": ["brainbit"], "runtime": {"brainbit": {"ok": False, "error": "timed out", "outcome_known": False}}}
        with self.app.app_context(), patch(f"{HELPERS}.run_session_end", return_value=timeout):
            _end_study_sensor_session(session_id="A", notify=True)

        coordinator = self.app.config["SENSOR_COORDINATOR"]
        settings = {"sensors_enabled": True, "plugins": {"brainbit": {"enabled": True, "required": True, "settings": {}}}}
        def running(*_args):
            return {"active_plugins": ["brainbit"], "runtime": {"brainbit": {"ok": True}}, "coordinator": {}}
        with (
            self.app.app_context(),
            patch.object(coordinator, "ensure_running", side_effect=running),
            patch(f"{HELPERS}._refresh_trial_runtime"),
            patch(f"{HELPERS}.get_plugin_status", return_value={"session_end": {"in_progress": "A"}}),
        ):
            blocked = _start_study_sensor_runtime(settings, session_id="B")
        self.assertEqual(blocked["runtime"]["brainbit"]["code"], "sensor_reset_pending")
        self.assertEqual(blocked["active_plugins"], [])

        with (
            self.app.app_context(),
            patch.object(coordinator, "ensure_running", side_effect=running),
            patch(f"{HELPERS}._refresh_trial_runtime"),
            patch(f"{HELPERS}.get_plugin_status", return_value={"session_end": {"last_completed": "A"}}),
        ):
            started = _start_study_sensor_runtime(settings, session_id="B")
        self.assertTrue(started["runtime"]["brainbit"]["ok"])
        self.assertEqual(started["active_plugins"], ["brainbit"])


if __name__ == "__main__":
    unittest.main()
