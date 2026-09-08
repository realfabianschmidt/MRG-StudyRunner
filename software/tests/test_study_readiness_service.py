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

from study_runner.backend.services.studies.study_readiness_service import check_study_readiness
from study_runner.plugin_framework.plugin_secrets import (
    _credential_declarations,
    set_study_secret,
)

# The env var names now live only in each plugin's manifest; read them from
# there rather than hardcoding them a second time in the test.
NOTION_API_KEY_ENV = _credential_declarations()["notion"]["env_var"]
NEXTCLOUD_PASSWORD_ENV = _credential_declarations()["nextcloud"]["env_var"]


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

        self.assertIn("notion.credential_missing", codes(report))
        self.assertFalse(report["ready"])

    def test_machine_key_satisfies_notion(self) -> None:
        report = check_study_readiness(
            study(notion_enabled=True, notion_parent_page_id="p1"),
            hardware(),
            {"notion": {"api_key": "machine-key"}},
            https_active=True,
        )

        self.assertNotIn("notion.credential_missing", codes(report))

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

        self.assertIn("notion.setting_missing", codes(report))

    def test_database_id_alone_is_a_valid_target(self) -> None:
        report = check_study_readiness(
            study(notion_enabled=True, notion_database_id="db1"),
            hardware(),
            {"notion": {"api_key": "k"}},
            https_active=True,
        )

        self.assertNotIn("notion.setting_missing", codes(report))

    def test_notion_disabled_machine_side_blocks(self) -> None:
        report = check_study_readiness(
            study(notion_enabled=True, notion_parent_page_id="p1"),
            hardware(notion={"enabled": False}),
            {"notion": {"api_key": "k"}},
            https_active=True,
        )

        self.assertIn("notion.machine_disabled", codes(report))

    def test_nextcloud_without_a_link_blocks(self) -> None:
        report = check_study_readiness(study(nextcloud_enabled=True), hardware(), {}, https_active=True)

        self.assertIn("nextcloud.setting_missing", codes(report))

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

        self.assertNotIn("nextcloud.setting_missing", codes(report))
        self.assertNotIn("notion.setting_missing", codes(report))
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

        for expected in ("notion.credential_missing", "notion.setting_missing", "notion.machine_disabled",
                         "nextcloud.setting_missing", "sensor_machine_disabled"):
            self.assertIn(expected, codes(report))


class RecordingPreflightReadinessTests(unittest.TestCase):
    """Package 5b: capacity and clock get their own blocker codes, distinct
    from the generic "worker unavailable" one, so an operator sees exactly
    what's wrong instead of one message that hides which."""

    def test_capacity_failure_gets_its_own_code(self) -> None:
        report = check_study_readiness(
            study(sensors_enabled=True, sensors={"brainbit": True}),
            hardware(),
            {},
            https_active=True,
            recording_preflight={
                "ready": False,
                "selected_plugins": ["brainbit"],
                "required_plugins": ["brainbit"],
                "capacity": {"ok": False, "known": False, "reason": "planned_session_duration_minutes is not configured"},
                "clock": {"ok": True},
                "reason": "planned_session_duration_minutes is not configured",
            },
        )

        self.assertIn("recording_capacity_insufficient", codes(report))
        self.assertNotIn("recording_worker_unavailable", codes(report))
        self.assertNotIn("recording_clock_implausible", codes(report))
        self.assertFalse(report["ready"])

    def test_clock_failure_gets_its_own_code(self) -> None:
        report = check_study_readiness(
            study(sensors_enabled=True, sensors={"brainbit": True}),
            hardware(),
            {},
            https_active=True,
            recording_preflight={
                "ready": False,
                "selected_plugins": ["brainbit"],
                "required_plugins": ["brainbit"],
                "capacity": {"ok": True},
                "clock": {"ok": False, "reason": "System clock is not plausible."},
                "reason": "System clock is not plausible.",
            },
        )

        self.assertIn("recording_clock_implausible", codes(report))
        self.assertNotIn("recording_worker_unavailable", codes(report))
        self.assertNotIn("recording_capacity_insufficient", codes(report))

    def test_both_capacity_and_clock_can_block_at_once(self) -> None:
        report = check_study_readiness(
            study(sensors_enabled=True, sensors={"brainbit": True}),
            hardware(),
            {},
            https_active=True,
            recording_preflight={
                "ready": False,
                "selected_plugins": ["brainbit"],
                "required_plugins": ["brainbit"],
                "capacity": {"ok": False, "reason": "insufficient space"},
                "clock": {"ok": False, "reason": "implausible clock"},
                "reason": "insufficient space; implausible clock",
            },
        )

        self.assertIn("recording_capacity_insufficient", codes(report))
        self.assertIn("recording_clock_implausible", codes(report))

    def test_worker_unavailable_without_capacity_or_clock_detail_keeps_the_generic_code(self) -> None:
        """A missing native worker binary is neither a capacity nor a clock
        problem; the fallback code must still fire when preflight failed for
        some other reason (see availability())."""
        report = check_study_readiness(
            study(sensors_enabled=True, sensors={"brainbit": True}),
            hardware(),
            {},
            https_active=True,
            recording_preflight={
                "ready": False,
                "selected_plugins": ["brainbit"],
                "required_plugins": ["brainbit"],
                "capacity": None,
                "clock": None,
                "reason": "native XDF worker is unavailable",
            },
        )

        self.assertIn("recording_worker_unavailable", codes(report))
        self.assertNotIn("recording_capacity_insufficient", codes(report))
        self.assertNotIn("recording_clock_implausible", codes(report))

    def test_ready_preflight_reports_no_recording_blocker(self) -> None:
        report = check_study_readiness(
            study(sensors_enabled=True, sensors={"brainbit": True}),
            hardware(),
            {},
            https_active=True,
            recording_preflight={
                "ready": True,
                "selected_plugins": ["brainbit"],
                "required_plugins": ["brainbit"],
                "capacity": {"ok": True},
                "clock": {"ok": True},
                "reason": None,
            },
        )

        self.assertNotIn("recording_capacity_insufficient", codes(report))
        self.assertNotIn("recording_clock_implausible", codes(report))
        self.assertNotIn("recording_worker_unavailable", codes(report))


if __name__ == "__main__":
    unittest.main()
