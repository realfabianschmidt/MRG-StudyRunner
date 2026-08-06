from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.studies.study_plugin_config import (
    migrate_study_plugin_config,
    normalize_study_settings_plugins,
)
from study_runner.backend.services.studies.study_readiness_service import check_study_readiness
from study_runner.backend.services.shared.validation import validate_and_normalize_config


class StudyPluginMigrationTests(unittest.TestCase):
    def test_legacy_sensor_and_destination_fields_create_v3_entries(self) -> None:
        migrated = normalize_study_settings_plugins(
            {
                "sensors": {"brainbit": True, "mini_radar": False, "camera_emotion": True},
                "notion_enabled": True,
                "notion_database_id": "db-1",
                "nextcloud_enabled": True,
                "nextcloud_share_link": "https://cloud.example/s/token",
            }
        )

        self.assertEqual(
            migrated["plugins"]["brainbit"],
            {"enabled": True, "required": True, "settings": {}},
        )
        self.assertFalse(migrated["plugins"]["mini_radar"]["enabled"])
        self.assertTrue(migrated["plugins"]["camera_emotion"]["enabled"])
        self.assertEqual(migrated["plugins"]["notion"]["settings"]["database_id"], "db-1")
        self.assertEqual(
            migrated["plugins"]["nextcloud"]["settings"]["share_link"],
            "https://cloud.example/s/token",
        )

    def test_v3_plugin_values_override_legacy_without_reemitting_flat_fields(self) -> None:
        migrated = normalize_study_settings_plugins(
            {
                "sensors": {"brainbit": True},
                "notion_enabled": False,
                "plugins": {
                    "brainbit": {"enabled": False, "required": False, "settings": {}},
                    "notion": {
                        "enabled": True,
                        "required": False,
                        "settings": {"database_id": "v3-db"},
                    },
                },
            }
        )

        self.assertFalse(migrated["sensors"]["brainbit"])
        self.assertTrue(migrated["plugins"]["notion"]["enabled"])
        self.assertEqual(
            migrated["plugins"]["notion"]["settings"]["database_id"],
            "v3-db",
        )
        self.assertNotIn("notion_enabled", migrated)
        self.assertNotIn("notion_database_id", migrated)

    def test_missing_required_plugin_reference_is_preserved(self) -> None:
        validated = validate_and_normalize_config(
            {
                "study_id": "Future sensor",
                "questions": [{"type": "finish"}],
                "study_settings": {
                    "plugins": {
                        "future_sensor": {
                            "enabled": True,
                            "required": True,
                            "settings": {"mode": "fast"},
                        }
                    }
                },
            }
        )

        self.assertEqual(
            validated["study_settings"]["plugins"]["future_sensor"],
            {"enabled": True, "required": True, "settings": {"mode": "fast"}},
        )

    def test_legacy_emotion_worker_plugin_becomes_camera_emotion(self) -> None:
        migrated = normalize_study_settings_plugins(
            {
                "plugins": {
                    "emotion_worker": {
                        "enabled": True,
                        "required": True,
                        "settings": {"worker_mode": "remote_worker"},
                    }
                }
            }
        )

        self.assertNotIn("emotion_worker", migrated["plugins"])
        self.assertEqual(
            migrated["plugins"]["camera_emotion"],
            {
                "enabled": True,
                "required": True,
                "settings": {"worker_mode": "remote_worker"},
            },
        )
        readiness = check_study_readiness(
            {"study_id": "Legacy emotion", "study_settings": migrated},
            {"camera_emotion": {"enabled": True}},
            {},
            https_active=True,
        )
        self.assertNotIn(
            "plugin_unavailable",
            [blocker["code"] for blocker in readiness["blockers"]],
        )

    def test_explicit_camera_emotion_wins_and_inherits_missing_legacy_settings(self) -> None:
        migrated = normalize_study_settings_plugins(
            {
                "plugins": {
                    "emotion_worker": {
                        "enabled": True,
                        "required": True,
                        "settings": {
                            "worker_mode": "remote_worker",
                            "emotion_worker_url": "http://legacy.example:3001",
                        },
                    },
                    "camera_emotion": {
                        "enabled": False,
                        "required": False,
                        "settings": {
                            "worker_mode": "local_worker",
                        },
                    },
                }
            }
        )

        self.assertNotIn("emotion_worker", migrated["plugins"])
        self.assertEqual(
            migrated["plugins"]["camera_emotion"],
            {
                "enabled": False,
                "required": False,
                "settings": {
                    "worker_mode": "local_worker",
                    "emotion_worker_url": "http://legacy.example:3001",
                },
            },
        )

    def test_legacy_stimulus_fields_become_plugin_actions(self) -> None:
        migrated = migrate_study_plugin_config(
            {
                "study_id": "Legacy cards",
                "questions": [
                    {
                        "type": "stimulus",
                        "send_signal": False,
                        "brainbit_to_touchdesigner": True,
                        "camera_capture_enabled": True,
                        "camera_snapshot_interval_ms": 1500,
                        "mini_radar_recording_enabled": False,
                    }
                ],
            }
        )
        actions = migrated["questions"][0]["plugin_actions"]

        self.assertTrue(actions["brainbit"]["to_touchdesigner"])
        self.assertEqual(actions["camera_emotion"]["snapshot_interval_ms"], 1500)
        self.assertNotIn("lsl", actions)
        self.assertNotIn("mini_radar", actions)
        self.assertNotIn("to_lsl", actions["brainbit"])
        self.assertNotIn("capture_enabled", actions["camera_emotion"])
        self.assertFalse(actions["osc"]["forward_marker"])
        self.assertNotIn("send_signal", migrated["questions"][0])
        self.assertNotIn("camera_capture_enabled", migrated["questions"][0])

    def test_v3_card_actions_survive_validation_without_legacy_projections(self) -> None:
        validated = validate_and_normalize_config(
            {
                "study_id": "V3 cards",
                "questions": [
                    {
                        "type": "stimulus",
                        "duration_ms": 1000,
                        "plugin_actions": {
                            "brainbit": {"to_lsl": False, "to_touchdesigner": True},
                            "mini_radar": {"recording_enabled": False},
                            "camera_emotion": {
                                "capture_enabled": True,
                                "snapshot_interval_ms": 250,
                            },
                            "lsl": {"send_marker": False},
                        },
                    }
                ],
            }
        )
        card = validated["questions"][0]

        self.assertNotIn("send_signal", card)
        self.assertNotIn("brainbit_to_lsl", card)
        self.assertNotIn("brainbit_to_touchdesigner", card)
        self.assertNotIn("camera_capture_enabled", card)
        self.assertNotIn("camera_snapshot_interval_ms", card)
        self.assertNotIn("mini_radar_recording_enabled", card)
        self.assertNotIn("to_lsl", card["plugin_actions"]["brainbit"])
        self.assertNotIn("capture_enabled", card["plugin_actions"]["camera_emotion"])
        self.assertNotIn("lsl", card["plugin_actions"])
        self.assertNotIn("mini_radar", card["plugin_actions"])
        self.assertIn("plugin_actions", card)


if __name__ == "__main__":
    unittest.main()
