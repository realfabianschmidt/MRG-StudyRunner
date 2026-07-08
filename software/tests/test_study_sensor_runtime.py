from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.study_sensor_runtime import (
    build_effective_hardware_config,
    normalize_study_sensors,
)


class StudySensorRuntimeTests(unittest.TestCase):
    def test_missing_sensor_selection_defaults_to_brainbit_and_radar(self) -> None:
        self.assertEqual(
            normalize_study_sensors({}),
            {"brainbit": True, "mini_radar": True, "camera_emotion": False},
        )

    def test_master_sensor_switch_disables_every_study_sensor(self) -> None:
        self.assertEqual(
            normalize_study_sensors(
                {
                    "sensors_enabled": "false",
                    "sensors": {
                        "brainbit": True,
                        "mini_radar": True,
                        "camera_emotion": True,
                    },
                }
            ),
            {"brainbit": False, "mini_radar": False, "camera_emotion": False},
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


if __name__ == "__main__":
    unittest.main()
