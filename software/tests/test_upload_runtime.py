"""Host-side persistence of what a destination plugin discovered mid-upload.

A destination plugin runs inside its own `driver.py` subprocess and may not
import `backend` (docs/architecture-1.0-umbau.md invariant #2), so it cannot
save something like an auto-created Notion database id back to the study
config itself. `_persist_study_config_updates` is the host-side half: it
takes the flat updates a plugin reports in its result and does what
`notion_upload.adapter._persist_study_database_id` used to do in-process --
merge, canonicalize, save both the active config and the study's own file.

See test_notion_upload.py::test_auto_created_database_is_reported_as_a_study_config_update
for the plugin-side half of this split.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.delivery.upload_runtime import _persist_study_config_updates


class FakeApp:
    def __init__(self, config: dict) -> None:
        self.config = config


class PersistStudyConfigUpdatesTests(unittest.TestCase):
    def test_flat_updates_are_merged_and_canonicalized(self) -> None:
        app = FakeApp(
            {
                "CONFIG_FILE": Path("active.study-runner"),
                "SAVED_STUDIES_DIR": Path("studies"),
            }
        )
        payload = {
            "config_data": {
                "study_id": "Notion Metadata",
                "study_settings": {
                    "notion_enabled": True,
                    "plugins": {
                        "notion": {
                            "enabled": True,
                            "required": False,
                            "settings": {"parent_page_id": "parent-1"},
                        }
                    },
                },
            }
        }

        with (
            patch("study_runner.backend.services.delivery.upload_runtime.save_config") as save_config,
            patch("study_runner.backend.services.delivery.upload_runtime.save_study") as save_study,
        ):
            _persist_study_config_updates(
                app,
                payload,
                "notion",
                {"notion_database_id": "created-db", "notion_data_source_id": "created-source"},
            )

        save_config.assert_called_once()
        persisted = save_config.call_args.args[1]
        settings = persisted["study_settings"]
        # The flat fields this function receives must not leak into the
        # canonical form -- only the plugin's own settings sub-tree.
        self.assertNotIn("notion_database_id", settings)
        self.assertNotIn("notion_data_source_id", settings)
        self.assertEqual(
            settings["plugins"]["notion"]["settings"],
            {
                "parent_page_id": "parent-1",
                "database_id": "created-db",
                "data_source_id": "created-source",
            },
        )
        self.assertEqual(save_config.call_args.args[0], app.config["CONFIG_FILE"])
        save_study.assert_called_once_with(app.config["SAVED_STUDIES_DIR"], persisted)

    def test_missing_config_data_is_a_silent_no_op(self) -> None:
        app = FakeApp({"CONFIG_FILE": Path("active.study-runner"), "SAVED_STUDIES_DIR": Path("studies")})

        with (
            patch("study_runner.backend.services.delivery.upload_runtime.save_config") as save_config,
            patch("study_runner.backend.services.delivery.upload_runtime.save_study") as save_study,
        ):
            _persist_study_config_updates(app, {}, "notion", {"notion_database_id": "x"})

        save_config.assert_not_called()
        save_study.assert_not_called()

    def test_a_save_failure_is_logged_not_raised(self) -> None:
        """An upload that already succeeded must not fail the whole job
        because the config-refresh convenience write failed."""
        app = FakeApp({"CONFIG_FILE": Path("active.study-runner"), "SAVED_STUDIES_DIR": Path("studies")})
        payload = {"config_data": {"study_settings": {}}}

        with patch(
            "study_runner.backend.services.delivery.upload_runtime.save_config",
            side_effect=OSError("disk full"),
        ):
            _persist_study_config_updates(app, payload, "notion", {"notion_database_id": "x"})  # must not raise


if __name__ == "__main__":
    unittest.main()
