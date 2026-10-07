"""The loaded study decides which sensors run; sessions only record them.

Sensors are prepared before a participant starts (connected, electrode
contact measured, calibrated) and keep streaming between participants. A
participant session must neither re-initialize nor stop them; loading a study
starts exactly the sensors it needs and stops the others.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.apps.server import create_app


def _app(data_dir: str):
    env = {
        "STUDY_RUNNER_DATA_DIR": data_dir,
        "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
        "STUDY_RUNNER_DISABLE_HARDWARE": "0",
    }
    with patch.dict(os.environ, env, clear=False), patch("study_runner.apps.server.initialize_plugins"):
        return create_app()


class StudySensorLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = _app(self.temp_dir.name)

    def tearDown(self) -> None:
        self.app.config["SENSOR_COORDINATOR"].close(wait=True)
        self.temp_dir.cleanup()

    def _apply(self, study_settings: dict) -> dict:
        from study_runner.apps.server.routes.helpers import _apply_study_sensor_selection

        coordinator = self.app.config["SENSOR_COORDINATOR"]
        with self.app.app_context(), patch.object(
            coordinator, "apply_selection", return_value={"runtime": {}, "active_plugins": []}
        ) as apply:
            _apply_study_sensor_selection(study_settings)
        return apply.call_args.args[0] if apply.call_args else {}

    def test_a_study_without_sensorics_runs_no_sensor(self) -> None:
        selected = self._apply({"sensors_enabled": False, "plugins": {}})
        self.assertTrue(selected)
        self.assertFalse(any(selected.values()), selected)

    def test_a_study_runs_exactly_the_sensors_it_selects(self) -> None:
        selected = self._apply(
            {
                "sensors_enabled": True,
                "plugins": {
                    "brainbit": {"enabled": True, "required": True, "settings": {}},
                    "am_hub": {"enabled": False, "required": False, "settings": {}},
                },
            }
        )
        self.assertTrue(selected["brainbit"])
        self.assertFalse(selected["am_hub"])

    def test_session_start_checks_only_manifest_declared_plugin_status(self) -> None:
        from study_runner.apps.server.routes.helpers import _start_study_sensor_runtime

        settings = {
            "sensors_enabled": True,
            "plugins": {
                "brainbit": {"enabled": True, "required": True, "settings": {}},
                "am_hub": {"enabled": True, "required": True, "settings": {}},
            },
        }
        coordinator = self.app.config["SENSOR_COORDINATOR"]
        runtime = {
            key: {"ok": True, "plugin": key, "action": "none"}
            for key in ("brainbit", "am_hub")
        }
        manifests = {
            "brainbit": {"capability_config": {"study_sensor": {
                "start_condition": {"path": "connection.ready", "equals": True},
            }}},
            "am_hub": {"capability_config": {"study_sensor": {}}},
        }
        with (
            self.app.app_context(),
            patch.object(coordinator, "ensure_running", return_value={
                "active_plugins": ["brainbit", "am_hub"], "runtime": runtime, "coordinator": {},
            }),
            patch("study_runner.apps.server.routes.helpers._refresh_trial_runtime"),
            patch("study_runner.apps.server.routes.helpers.get_plugin_manifest", side_effect=manifests.get),
            patch("study_runner.apps.server.routes.helpers.get_plugin_status", return_value={
                "connection": {"ready": False, "phase": "connected", "next_step": "initialize"},
            }) as status,
        ):
            result = _start_study_sensor_runtime(settings, enforce_start_conditions=True)
        self.assertFalse(result["runtime"]["brainbit"]["ok"])
        self.assertIn("connection.ready", result["runtime"]["brainbit"]["error"])
        self.assertTrue(result["runtime"]["am_hub"]["ok"])
        status.assert_called_once()

    def test_selection_is_not_applied_during_a_participant_session(self) -> None:
        self.app.config["ACTIVE_STUDY_HARDWARE_CONFIG"] = {"brainbit": {"enabled": True}}
        from study_runner.apps.server.routes.helpers import _apply_study_sensor_selection

        coordinator = self.app.config["SENSOR_COORDINATOR"]
        with self.app.app_context(), patch.object(coordinator, "apply_selection") as apply:
            result = _apply_study_sensor_selection({"sensors_enabled": False, "plugins": {}})
        apply.assert_not_called()
        self.assertEqual(result["skipped"], "session_active")

    def test_ending_a_run_notifies_sensors_without_stopping_them(self) -> None:
        self.app.config["ACTIVE_STUDY_HARDWARE_CONFIG"] = {"brainbit": {"enabled": True}}
        self.app.config["ACTIVE_STUDY_SENSOR_PLUGINS"] = ["brainbit"]
        coordinator = self.app.config["SENSOR_COORDINATOR"]
        with (
            patch(
                "study_runner.apps.server.routes.helpers.run_session_end",
                return_value={"notified_plugins": ["brainbit"], "runtime": {"brainbit": {"ok": True}}},
            ) as session_end,
            patch.object(coordinator, "stop_plugins") as stop_plugins,
            patch("study_runner.apps.server.routes.helpers.run_runtime_action") as runtime_action,
        ):
            response = self.app.test_client().post("/api/admin/study-run/stop", json={})

        self.assertEqual(response.status_code, 200)
        session_end.assert_called_once()
        self.assertEqual(session_end.call_args.args[0], ["brainbit"])
        stop_plugins.assert_not_called()
        runtime_action.assert_not_called()
        # The session lock is released so the sensors can be set up again.
        self.assertIsNone(self.app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG"))
        self.assertEqual(self.app.config["ACTIVE_STUDY_SENSOR_PLUGINS"], [])

    def test_session_end_only_notifies_plugins_with_declared_capability(self) -> None:
        from study_runner.plugin_framework.registry import run_session_end

        calls: list[str] = []
        manifests = {
            "continuous": {"runtime": {"trial_events": []}},
            "resettable": {"runtime": {"trial_events": ["session_end"]}},
        }

        def get_plugin(key: str) -> SimpleNamespace:
            return SimpleNamespace(
                key=key,
                on_session_end=lambda *_: calls.append(key),
            )

        with (
            patch("study_runner.plugin_framework.registry.get_plugin_manifests", return_value=manifests),
            patch("study_runner.plugin_framework.registry.get_plugin", side_effect=get_plugin),
        ):
            outcome = run_session_end(["continuous", "resettable"], {}, object())

        self.assertEqual(calls, ["resettable"])
        self.assertEqual(outcome["notified_plugins"], ["resettable"])

    def test_switching_a_sensor_on_initializes_it_with_the_new_configuration(self) -> None:
        # A sensor the study does not select was set up disabled; "start" alone
        # would find nothing to start (AM Hub ignored it silently before).
        coordinator = self.app.config["SENSOR_COORDINATOR"]
        with (
            patch("study_runner.apps.server.routes.sensors.initialize_plugin") as initialize,
            patch.object(coordinator, "run_action", return_value={"ok": True}) as run_action,
        ):
            response = self.app.test_client().post("/api/admin/plugins/am_hub/start", json={})

        self.assertEqual(response.status_code, 200)
        initialize.assert_called_once()
        self.assertEqual(initialize.call_args.args[0], "am_hub")
        self.assertTrue(initialize.call_args.args[1].hardware_config["am_hub"]["enabled"])
        self.assertTrue(initialize.call_args.kwargs["strict"])
        run_action.assert_called_once()
        self.assertEqual(run_action.call_args.args[:2], ("am_hub", "start"))

    def test_admin_start_does_not_acknowledge_failed_initialization(self) -> None:
        coordinator = self.app.config["SENSOR_COORDINATOR"]
        with (
            patch(
                "study_runner.apps.server.routes.sensors.initialize_plugin",
                side_effect=RuntimeError("device unavailable"),
            ),
            patch.object(coordinator, "run_action") as run_action,
        ):
            response = self.app.test_client().post("/api/admin/plugins/am_hub/start", json={})
        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.json["ok"])
        run_action.assert_not_called()


if __name__ == "__main__":
    unittest.main()
