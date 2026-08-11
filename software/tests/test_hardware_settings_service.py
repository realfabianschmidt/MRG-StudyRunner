"""The plugin folder rename must not break an operator's existing settings.

Machine settings store paths into the plugin folder verbatim -- a script to
launch, a folder to log into, a directory of model weights. Up to 0.5.0 that
folder was called `integrations`. Every one of those paths would point nowhere
after the rename, so they are repaired when the file is read. Losing them is
silent: recording simply never starts.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.settings.hardware_settings_service import (
    migrate_moved_plugin_paths,
    save_hardware_config,
)


class MovedPluginPathTests(unittest.TestCase):
    def test_posix_paths_are_repointed(self) -> None:
        migrated, changed = migrate_moved_plugin_paths(
            {"brainbit": {"script_path": "study_runner/integrations/brainbit/brainbit_realtime_cli.py"}}
        )

        self.assertEqual(changed, 1)
        self.assertEqual(
            migrated["brainbit"]["script_path"],
            "study_runner/plugins/brainbit/brainbit_realtime_cli.py",
        )

    def test_windows_paths_are_repointed(self) -> None:
        """Settings written on Windows store backslashes, so both separators count."""
        stored = "C:\\Study\\software\\study_runner\\integrations\\brainbit\\logs"

        migrated, changed = migrate_moved_plugin_paths({"brainbit": {"log_dir": stored}})

        self.assertEqual(changed, 1)
        self.assertEqual(
            migrated["brainbit"]["log_dir"],
            "C:\\Study\\software\\study_runner\\plugins\\brainbit\\logs",
        )

    def test_per_platform_mappings_and_lists_are_reached(self) -> None:
        """The paths sit at different depths per plugin, some inside a platform map."""
        migrated, changed = migrate_moved_plugin_paths(
            {
                "camera_emotion": {
                    "settings": {
                        "log_dir": {
                            "windows": "study_runner\\integrations\\camera_emotion\\worker\\logs",
                            "default": "study_runner/integrations/camera_emotion/worker/logs",
                        },
                        "extra_paths": ["study_runner/integrations/camera_emotion/worker/model_assets"],
                    }
                }
            }
        )

        settings = migrated["camera_emotion"]["settings"]
        self.assertEqual(changed, 3)
        self.assertEqual(settings["log_dir"]["windows"], "study_runner\\plugins\\camera_emotion\\worker\\logs")
        self.assertEqual(settings["log_dir"]["default"], "study_runner/plugins/camera_emotion/worker/logs")
        self.assertEqual(settings["extra_paths"], ["study_runner/plugins/camera_emotion/worker/model_assets"])

    def test_unrelated_values_are_left_alone(self) -> None:
        """Only the folder path moved. A URL that happens to say 'integrations' did not."""
        original = {
            "notion": {"guide_url": "https://www.notion.so/profile/integrations", "enabled": True},
            "poll_interval_ms": 2000,
            "nothing": None,
        }

        migrated, changed = migrate_moved_plugin_paths(original)

        self.assertEqual(changed, 0)
        self.assertEqual(migrated, original)

    def test_an_already_migrated_file_is_a_no_op(self) -> None:
        already = {"brainbit": {"script_path": "study_runner/plugins/brainbit/brainbit_realtime_cli.py"}}

        migrated, changed = migrate_moved_plugin_paths(already)

        self.assertEqual(changed, 0)
        self.assertEqual(migrated, already)

    def test_the_shipped_defaults_need_no_migration(self) -> None:
        """Whatever ships must already be on the new path, or first start rewrites it."""
        shipped = json.loads(
            (PROJECT_ROOT / "study_content" / "settings" / "hardware_settings.json").read_text(encoding="utf-8")
        )

        _, changed = migrate_moved_plugin_paths(shipped)

        self.assertEqual(changed, 0)

    def test_the_shipped_defaults_carry_no_real_device_identity(self) -> None:
        """A real BrainBit MAC/serial has been committed here by accident twice.

        device_address and serial_number identify one specific physical
        headset. The shipped template must stay empty so a fresh checkout
        never fingerprints whoever's lab happened to save it last.
        """
        shipped = json.loads(
            (PROJECT_ROOT / "study_content" / "settings" / "hardware_settings.json").read_text(encoding="utf-8")
        )

        brainbit = shipped.get("brainbit", {})
        self.assertEqual(brainbit.get("device_address"), "")
        self.assertEqual(brainbit.get("serial_number"), "")

    def test_a_migrated_config_survives_a_save_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hardware_settings.json"
            migrated, _ = migrate_moved_plugin_paths(
                {"brainbit": {"working_dir": "study_runner/integrations/brainbit"}}
            )

            save_hardware_config(path, migrated)
            reloaded = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(reloaded["brainbit"]["working_dir"], "study_runner/plugins/brainbit")


if __name__ == "__main__":
    unittest.main()
