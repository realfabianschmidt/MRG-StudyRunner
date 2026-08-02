"""Every blocker the readiness gate can raise, one at a time.

A false blocker is worse than no gate: it stops a lab session that would have
worked. So each code is pinned individually, and the "fully configured study is
ready" case is pinned too.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.secrets_service import NEXTCLOUD_PASSWORD_ENV, NOTION_API_KEY_ENV
from study_runner.backend.services.study_readiness_service import check_study_readiness
from study_runner.backend.services.study_secrets_service import set_study_secret


def hardware(**overrides) -> dict:
    config = {
        "notion": {"enabled": True},
        "nextcloud": {},
        "brainbit": {"enabled": True},
        "mini_radar": {"enabled": True},
        "camera_emotion": {"enabled": True},
    }
    config.update(overrides)
    return config


def study(**settings) -> dict:
    base = {
        "sensors_enabled": False,
        "sensors": {"brainbit": False, "mini_radar": False, "camera_emotion": False},
    }
    base.update(settings)
    return {"study_id": "Study A", "study_settings": base}


def codes(report: dict) -> list[str]:
    return [blocker["code"] for blocker in report["blockers"]]


class ReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        cleared = patch.dict(os.environ, {NOTION_API_KEY_ENV: "", NEXTCLOUD_PASSWORD_ENV: ""}, clear=False)
        cleared.start()
        self.addCleanup(cleared.stop)

    def test_study_without_uploads_or_sensors_is_ready(self) -> None:
        report = check_study_readiness(study(), hardware(), {}, https_active=True)

        self.assertTrue(report["ready"])
        self.assertEqual(report["blockers"], [])

    def test_notion_without_any_key_blocks(self) -> None:
        report = check_study_readiness(
            study(notion_enabled=True, notion_parent_page_id="p1"), hardware(), {}, https_active=True
        )

        self.assertIn("notion_api_key_missing", codes(report))
        self.assertFalse(report["ready"])

    def test_machine_key_satisfies_notion(self) -> None:
        report = check_study_readiness(
            study(notion_enabled=True, notion_parent_page_id="p1"),
            hardware(),
            {"notion": {"api_key": "machine-key"}},
            https_active=True,
        )

        self.assertNotIn("notion_api_key_missing", codes(report))

    def test_study_key_satisfies_notion(self) -> None:
        secrets = set_study_secret({}, "Study A", "notion", "study-key")

        report = check_study_readiness(
            study(notion_enabled=True, notion_parent_page_id="p1"), hardware(), secrets, https_active=True
        )

        self.assertTrue(report["ready"])

    def test_notion_without_a_target_blocks(self) -> None:
        report = check_study_readiness(
            study(notion_enabled=True), hardware(), {"notion": {"api_key": "k"}}, https_active=True
        )

        self.assertIn("notion_target_missing", codes(report))

    def test_database_id_alone_is_a_valid_target(self) -> None:
        report = check_study_readiness(
            study(notion_enabled=True, notion_database_id="db1"),
            hardware(),
            {"notion": {"api_key": "k"}},
            https_active=True,
        )

        self.assertNotIn("notion_target_missing", codes(report))

    def test_notion_disabled_machine_side_blocks(self) -> None:
        report = check_study_readiness(
            study(notion_enabled=True, notion_parent_page_id="p1"),
            hardware(notion={"enabled": False}),
            {"notion": {"api_key": "k"}},
            https_active=True,
        )

        self.assertIn("notion_machine_disabled", codes(report))

    def test_nextcloud_without_a_link_blocks(self) -> None:
        report = check_study_readiness(study(nextcloud_enabled=True), hardware(), {}, https_active=True)

        self.assertIn("nextcloud_link_missing", codes(report))

    def test_nextcloud_without_a_password_is_not_a_blocker(self) -> None:
        """Public shares legitimately have no password."""
        report = check_study_readiness(
            study(nextcloud_enabled=True, nextcloud_share_link="https://c.example.com/s/AbC"),
            hardware(),
            {},
            https_active=True,
        )

        self.assertTrue(report["ready"])

    def test_v3_destination_settings_override_legacy_projection(self) -> None:
        report = check_study_readiness(
            study(
                notion_enabled=False,
                nextcloud_enabled=True,
                nextcloud_share_link="",
                plugins={
                    "notion": {
                        "enabled": True,
                        "required": False,
                        "settings": {"parent_page_id": "p1"},
                    },
                    "nextcloud": {
                        "enabled": False,
                        "required": False,
                        "settings": {},
                    },
                },
            ),
            hardware(),
            {"notion": {"api_key": "k"}},
            https_active=True,
        )

        self.assertNotIn("nextcloud_link_missing", codes(report))
        self.assertNotIn("notion_target_missing", codes(report))
        self.assertTrue(report["ready"])

    def test_sensor_disabled_machine_side_blocks(self) -> None:
        report = check_study_readiness(
            study(sensors_enabled=True, sensors={"brainbit": True, "mini_radar": False, "camera_emotion": False}),
            hardware(brainbit={"enabled": False}),
            {},
            https_active=True,
        )

        self.assertIn("sensor_machine_disabled", codes(report))
        self.assertEqual(report["blockers"][0]["sensor"], "brainbit")

    def test_camera_without_https_blocks(self) -> None:
        report = check_study_readiness(
            study(sensors_enabled=True, sensors={"brainbit": False, "mini_radar": False, "camera_emotion": True}),
            hardware(),
            {},
            https_active=False,
        )

        self.assertIn("browser_source_requires_https", codes(report))

    def test_camera_with_https_is_fine(self) -> None:
        report = check_study_readiness(
            study(sensors_enabled=True, sensors={"brainbit": False, "mini_radar": False, "camera_emotion": True}),
            hardware(),
            {},
            https_active=True,
        )

        self.assertTrue(report["ready"])

    def test_unpinned_brainbit_is_not_a_blocker(self) -> None:
        """The CLI falls back to the first headset it finds, so this must not gate."""
        report = check_study_readiness(
            study(sensors_enabled=True, sensors={"brainbit": True, "mini_radar": False, "camera_emotion": False}),
            hardware(brainbit={"enabled": True, "serial_number": "", "device_address": ""}),
            {},
            https_active=True,
        )

        self.assertTrue(report["ready"])

    def test_disabled_uploads_are_never_checked(self) -> None:
        report = check_study_readiness(
            study(notion_enabled=False, nextcloud_enabled=False), hardware(notion={"enabled": False}), {}, https_active=True
        )

        self.assertTrue(report["ready"])

    def test_missing_required_plugin_blocks_but_optional_plugin_does_not(self) -> None:
        required = check_study_readiness(
            study(
                plugins={
                    "future_sensor": {"enabled": True, "required": True, "settings": {}}
                }
            ),
            hardware(),
            {},
            https_active=True,
        )
        optional = check_study_readiness(
            study(
                plugins={
                    "future_sensor": {"enabled": True, "required": False, "settings": {}}
                }
            ),
            hardware(),
            {},
            https_active=True,
        )

        self.assertIn("plugin_unavailable", codes(required))
        self.assertEqual(required["blockers"][0]["plugin"], "future_sensor")
        self.assertTrue(optional["ready"])

    def test_manifest_platform_contract_blocks_unsupported_required_mode(self) -> None:
        report = check_study_readiness(
            study(
                sensors_enabled=True,
                sensors={"brainbit": False, "mini_radar": False, "camera_emotion": True},
                plugins={
                    "camera_emotion": {
                        "enabled": True,
                        "required": True,
                        "settings": {},
                    }
                },
            ),
            hardware(camera_emotion={"enabled": True, "worker_mode": "local_worker"}),
            {},
            https_active=True,
            platform_target="macos-x64",
        )

        self.assertIn("plugin_mode_unsupported", codes(report))
        blocker = next(item for item in report["blockers"] if item["code"] == "plugin_mode_unsupported")
        self.assertTrue(report["start_blocked"])
        self.assertEqual(blocker["plugin"], "camera_emotion")
        self.assertEqual(blocker["mode"], "local_worker")
        self.assertEqual(blocker["platform"], "macos-x64")
        self.assertEqual(blocker["supported_modes"], ["remote_worker"])

    def test_manifest_platform_contract_allows_supported_remote_mode(self) -> None:
        report = check_study_readiness(
            study(
                sensors_enabled=True,
                sensors={"brainbit": False, "mini_radar": False, "camera_emotion": True},
                plugins={
                    "camera_emotion": {
                        "enabled": True,
                        "required": True,
                        "settings": {},
                    }
                },
            ),
            hardware(camera_emotion={"enabled": True, "worker_mode": "remote_worker"}),
            {},
            https_active=True,
            platform_target="macos-x64",
        )

        self.assertTrue(report["ready"])
        self.assertFalse(report["start_blocked"])

    def test_blockers_name_the_panel_that_fixes_them(self) -> None:
        report = check_study_readiness(
            study(notion_enabled=True, nextcloud_enabled=True), hardware(), {}, https_active=True
        )

        self.assertEqual(sorted(report["panels"]), ["nextcloud", "notion"])

    def test_env_key_counts_as_configured(self) -> None:
        with patch.dict(os.environ, {NOTION_API_KEY_ENV: "env-key"}):
            report = check_study_readiness(
                study(notion_enabled=True, notion_parent_page_id="p1"), hardware(), {}, https_active=True
            )

        self.assertTrue(report["ready"])

    def test_several_problems_are_all_reported(self) -> None:
        report = check_study_readiness(
            study(notion_enabled=True, nextcloud_enabled=True, sensors_enabled=True,
                  sensors={"brainbit": True, "mini_radar": False, "camera_emotion": False}),
            hardware(notion={"enabled": False}, brainbit={"enabled": False}),
            {},
            https_active=True,
        )

        for expected in ("notion_api_key_missing", "notion_target_missing", "notion_machine_disabled",
                         "nextcloud_link_missing", "sensor_machine_disabled"):
            self.assertIn(expected, codes(report))


if __name__ == "__main__":
    unittest.main()
