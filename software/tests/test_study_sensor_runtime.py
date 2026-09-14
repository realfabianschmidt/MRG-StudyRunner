from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.data_core.host.study_sensor_runtime import (
    build_sensor_runtime_state,
    build_effective_hardware_config,
    normalize_study_sensors,
)
from study_runner.plugin_framework.registry import get_plugin_manifests


def _manifest_sensor_defaults() -> dict[str, bool]:
    """`{plugin key: default_enabled}` for every plugin declaring study_sensor.

    Derived rather than listed, so installing a sensor plugin never means
    editing this file. The cross-check still has teeth: the expectation comes
    from the manifests, the result from the runtime module, and the two are
    computed independently.
    """
    return {
        key: bool((manifest["capability_config"]["study_sensor"] or {}).get("default_enabled", False))
        for key, manifest in get_plugin_manifests().items()
        if "study_sensor" in (manifest.get("capabilities") or [])
    }


class StudySensorRuntimeTests(unittest.TestCase):
    def test_missing_sensor_selection_falls_back_to_the_manifest_defaults(self) -> None:
        defaults = _manifest_sensor_defaults()
        self.assertTrue(defaults, "no plugin declares study_sensor at all")
        self.assertEqual(normalize_study_sensors({}), defaults)

    def test_master_sensor_switch_disables_every_study_sensor(self) -> None:
        every_sensor_on = {key: True for key in _manifest_sensor_defaults()}
        result = normalize_study_sensors(
            {"sensors_enabled": "false", "sensors": every_sensor_on}
        )

        self.assertEqual(set(result), set(every_sensor_on))
        self.assertEqual(
            [key for key, enabled in result.items() if enabled],
            [],
            "the master switch must win over every per-sensor selection",
        )

    def test_effective_hardware_config_only_overrides_study_sensor_enabled_flags(self) -> None:
        hardware_config = {
            "brainbit": {"enabled": True, "serial_number": "BB-1"},
            "mini_radar": {"enabled": True, "ble_device_name": "MR60"},
            "camera_emotion": {"enabled": True, "worker_mode": "local_worker"},
            "notion": {"enabled": True},
        }

        effective = build_effective_hardware_config(
            hardware_config,
            {
                "sensors_enabled": True,
                "sensors": {
                    "brainbit": False,
                    "mini_radar": True,
                    "camera_emotion": False,
                },
            },
        )

        self.assertFalse(effective["brainbit"]["enabled"])
        self.assertEqual(effective["brainbit"]["serial_number"], "BB-1")
        self.assertTrue(effective["mini_radar"]["enabled"])
        self.assertEqual(effective["mini_radar"]["ble_device_name"], "MR60")
        self.assertFalse(effective["camera_emotion"]["enabled"])
        self.assertEqual(effective["camera_emotion"]["worker_mode"], "local_worker")
        self.assertTrue(effective["notion"]["enabled"])
        self.assertIsNot(effective["brainbit"], hardware_config["brainbit"])

    def test_session_override_wins_over_study_sensor_setting(self) -> None:
        hardware_config = {
            "camera_emotion": {"enabled": False, "worker_mode": "local_worker"},
            "brainbit": {"enabled": True},
        }
        study_settings = {
            "sensors_enabled": False,
            "sensors": {
                "brainbit": False,
                "mini_radar": False,
                "camera_emotion": False,
            },
        }

        runtime_state = build_sensor_runtime_state(
            hardware_config,
            study_settings,
            {"camera_emotion": True},
        )
        effective = build_effective_hardware_config(
            hardware_config,
            study_settings,
            {"camera_emotion": True},
        )

        self.assertFalse(runtime_state["study"]["camera_emotion"])
        self.assertTrue(runtime_state["override_active"]["camera_emotion"])
        self.assertTrue(runtime_state["effective"]["camera_emotion"])
        self.assertTrue(effective["camera_emotion"]["enabled"])
        self.assertEqual(effective["camera_emotion"]["worker_mode"], "local_worker")


if __name__ == "__main__":
    unittest.main()
