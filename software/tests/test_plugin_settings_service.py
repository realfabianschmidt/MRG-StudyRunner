"""Machine-level plugin settings: validation, effective values, and the
sibling-key preservation that the whole-document POST does not give us.

Also pins the manifest contract itself: every declared path must exist in the
shipped hardware settings, and every default must be legal under its own
constraints. Both were violated before v2, in ways that would have silently
changed behaviour on the first save.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend import create_app
from study_runner.backend.services.settings.plugin_settings_service import (
    PluginSettingsError,
    apply_plugin_settings,
    build_plugin_settings_schema,
    effective_value,
    get_at,
    set_at,
)
from study_runner.plugin_framework.registry import get_plugin, get_plugin_manifests, iter_plugins

MANIFEST_FILES = tuple(
    sorted((PROJECT_ROOT / "study_runner" / "plugins").glob("*/manifest.json"))
)
HARDWARE_FILE = PROJECT_ROOT / "study_content" / "settings" / "hardware_settings.json"


def shipped_hardware() -> dict:
    return json.loads(HARDWARE_FILE.read_text(encoding="utf-8"))


class PathHelperTests(unittest.TestCase):
    def test_get_at_reads_nested(self) -> None:
        self.assertEqual(get_at({"lsl": {"enabled": True}}, "lsl.enabled"), True)

    def test_get_at_returns_none_for_missing(self) -> None:
        self.assertIsNone(get_at({"lsl": {}}, "lsl.enabled"))
        self.assertIsNone(get_at({}, "a.b.c"))

    def test_set_at_creates_intermediates(self) -> None:
        section: dict = {}
        set_at(section, "ble.scan_timeout_seconds", 9)
        self.assertEqual(section, {"ble": {"scan_timeout_seconds": 9}})

    def test_set_at_replaces_a_non_dict_step(self) -> None:
        section = {"ble": "not-a-dict"}
        set_at(section, "ble.scan", 1)
        self.assertEqual(section, {"ble": {"scan": 1}})


class EffectiveValueTests(unittest.TestCase):
    def test_disk_wins_over_manifest_default(self) -> None:
        field = {"path": "connection_type", "default": "serial"}
        value = effective_value({"mini_radar": {"connection_type": "ble"}}, "mini_radar", field)
        self.assertEqual(value, "ble")

    def test_default_only_fills_a_missing_key(self) -> None:
        field = {"path": "connection_type", "default": "ble"}
        self.assertEqual(effective_value({"mini_radar": {}}, "mini_radar", field), "ble")

    def test_falsy_stored_value_is_respected(self) -> None:
        """False must not fall back to a True default."""
        field = {"path": "enabled", "default": True}
        self.assertIs(effective_value({"lsl": {"enabled": False}}, "lsl", field), False)


class ApplyTests(unittest.TestCase):
    def test_sibling_keys_survive_a_save(self) -> None:
        """The regression the targeted route exists to prevent."""
        config = {
            "mini_radar": {
                "connection_type": "ble",
                "baudrate": 115200,
                "ble": {"device_name": "MR60_BLE", "scan_timeout_seconds": 5},
                "log_dir": "logs",
            }
        }

        updated, _ = apply_plugin_settings(config, "mini_radar", {"connection_type": "serial"})

        self.assertEqual(updated["mini_radar"]["connection_type"], "serial")
        self.assertEqual(updated["mini_radar"]["ble"]["device_name"], "MR60_BLE")
        self.assertEqual(updated["mini_radar"]["log_dir"], "logs")
        self.assertEqual(updated["mini_radar"]["baudrate"], 115200)

    def test_other_plugins_are_untouched(self) -> None:
        config = {"mini_radar": {}, "brainbit": {"serial_number": "0403"}}

        updated, _ = apply_plugin_settings(config, "mini_radar", {"connection_type": "serial"})

        self.assertEqual(updated["brainbit"]["serial_number"], "0403")

    def test_input_config_is_never_mutated(self) -> None:
        config = {"mini_radar": {"connection_type": "ble"}}

        apply_plugin_settings(config, "mini_radar", {"connection_type": "serial"})

        self.assertEqual(config["mini_radar"]["connection_type"], "ble")

    def test_nested_path_is_written(self) -> None:
        updated, _ = apply_plugin_settings({}, "mini_radar", {"ble.scan_timeout_seconds": 12})

        self.assertEqual(updated["mini_radar"]["ble"]["scan_timeout_seconds"], 12)

    def test_unknown_setting_is_refused(self) -> None:
        with self.assertRaises(PluginSettingsError):
            apply_plugin_settings({}, "mini_radar", {"not_a_setting": 1})

    def test_unknown_plugin_is_refused(self) -> None:
        with self.assertRaises(PluginSettingsError):
            apply_plugin_settings({}, "not_a_plugin", {"x": 1})

    def test_choice_outside_options_is_refused(self) -> None:
        with self.assertRaises(PluginSettingsError):
            apply_plugin_settings({}, "mini_radar", {"connection_type": "carrier_pigeon"})

    def test_number_below_minimum_is_refused(self) -> None:
        with self.assertRaises(PluginSettingsError):
            apply_plugin_settings({}, "brainbit", {"scan_seconds": 0})

    def test_number_above_maximum_is_refused(self) -> None:
        with self.assertRaises(PluginSettingsError):
            apply_plugin_settings({}, "brainbit", {"scan_seconds": 9999})

    def test_non_numeric_is_refused(self) -> None:
        with self.assertRaises(PluginSettingsError):
            apply_plugin_settings({}, "brainbit", {"scan_seconds": "soon"})

    def test_boolean_accepts_common_spellings(self) -> None:
        for raw, expected in (("true", True), ("0", False), (True, True), ("off", False)):
            updated, _ = apply_plugin_settings(
                {}, "notion", {"auto_retry_failed": raw}
            )
            self.assertIs(updated["notion"]["auto_retry_failed"], expected)

    def test_restart_required_is_reported(self) -> None:
        _, restart = apply_plugin_settings({}, "brainbit", {"scan_seconds": 7})
        self.assertTrue(restart)

        _, no_restart = apply_plugin_settings({}, "osc", {"port": 9000})
        self.assertFalse(no_restart)


class ManifestContractTests(unittest.TestCase):
    """The manifest is schema; these keep it honest against the real config."""

    def test_manifest_version_is_known(self) -> None:
        self.assertTrue(MANIFEST_FILES)
        for manifest_file in MANIFEST_FILES:
            self.assertEqual(json.loads(manifest_file.read_text(encoding="utf-8"))["api_version"], 4)

    def test_every_field_declares_what_a_form_needs(self) -> None:
        problems = []
        for key, manifest in get_plugin_manifests().items():
            for name, field in (manifest.get("runtime_settings") or {}).items():
                if not field.get("path"):
                    problems.append(f"{key}.{name}: no path")
                if not field.get("label_key"):
                    problems.append(f"{key}.{name}: no label_key")
                if field.get("type") == "choice" and not field.get("options"):
                    problems.append(f"{key}.{name}: choice without options")
        self.assertEqual(problems, [])

    def test_every_declared_path_exists_on_disk(self) -> None:
        """A path with no key on disk is a dead setting nobody would notice."""
        hardware = shipped_hardware()
        missing = []
        for plugin in iter_plugins():
            manifest = get_plugin_manifests().get(plugin.key) or {}
            section = hardware.get(plugin.config_key)
            if not isinstance(section, dict):
                continue
            for name, field in (manifest.get("runtime_settings") or {}).items():
                if get_at(section, str(field.get("path"))) is None:
                    missing.append(f"{plugin.key}.{name} -> {plugin.config_key}.{field.get('path')}")
        self.assertEqual(missing, [])

    def test_every_default_is_legal_under_its_own_constraints(self) -> None:
        """Catches the camera default of 1000 against its own minimum of 1000."""
        problems = []
        for key, manifest in get_plugin_manifests().items():
            for name, field in (manifest.get("runtime_settings") or {}).items():
                default = field.get("default")
                if field.get("type") == "number":
                    if field.get("minimum") is not None and default < field["minimum"]:
                        problems.append(f"{key}.{name}: default below its own minimum")
                    if field.get("maximum") is not None and default > field["maximum"]:
                        problems.append(f"{key}.{name}: default above its own maximum")
                if field.get("type") == "choice" and default not in (field.get("options") or []):
                    problems.append(f"{key}.{name}: default is not one of its options")
        self.assertEqual(problems, [])

    def test_saving_one_field_never_writes_another_fields_default(self) -> None:
        """The real protection against manifest/disk drift.

        Deliberately not "defaults must equal what is on disk": operators are
        meant to change these values, so such a test would fail the moment the
        settings hub is used. What must hold is that a save touches only the
        paths it was given - then a stale default can never leak into the
        config, however far it has drifted.
        """
        config = {
            "mini_radar": {
                "connection_type": "serial",
                "data_timeout_seconds": 42,
                "ble": {"scan_timeout_seconds": 99},
            }
        }

        updated, _ = apply_plugin_settings(config, "mini_radar", {"connection_type": "ble"})

        self.assertEqual(updated["mini_radar"]["data_timeout_seconds"], 42)
        self.assertEqual(updated["mini_radar"]["ble"]["scan_timeout_seconds"], 99)
        # baudrate has a manifest default but was absent - it must stay absent.
        self.assertNotIn("baudrate", updated["mini_radar"])

    def test_no_per_study_value_is_declared_machine_level(self) -> None:
        """notion database_id / upload_enabled belong to the study, not the machine."""
        notion = (get_plugin_manifests().get("notion") or {}).get("runtime_settings") or {}

        for forbidden in ("database_id", "upload_enabled", "parent_page_id"):
            self.assertNotIn(forbidden, notion)


class PluginSettingsRouteTests(unittest.TestCase):
    def _app(self, data_dir: str):
        with patch.dict(
            os.environ,
            {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
                "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
            },
            clear=False,
        ):
            return create_app()

    def test_get_returns_schema_with_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            body = self._app(temp_dir).test_client().get("/api/admin/plugin-settings").get_json()

        self.assertTrue(body["ok"])
        self.assertIn("mini_radar", body["plugins"])
        names = [field["name"] for field in body["plugins"]["mini_radar"]["fields"]]
        self.assertIn("connection_type", names)

    def test_post_writes_only_the_named_setting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._app(temp_dir)
            client = app.test_client()
            app.config["HARDWARE_CONFIG"] = {
                "mini_radar": {"connection_type": "ble", "ble": {"device_name": "MR60_BLE"}}
            }

            response = client.post(
                "/api/admin/plugin-settings/mini_radar",
                json={"settings": {"connection_type": "serial"}},
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(app.config["HARDWARE_CONFIG"]["mini_radar"]["connection_type"], "serial")
            self.assertEqual(app.config["HARDWARE_CONFIG"]["mini_radar"]["ble"]["device_name"], "MR60_BLE")

    def test_post_rejects_an_invalid_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            response = self._app(temp_dir).test_client().post(
                "/api/admin/plugin-settings/brainbit",
                json={"settings": {"scan_seconds": -1}},
            )

        self.assertEqual(response.status_code, 400)

    def test_post_without_settings_is_a_bad_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            response = self._app(temp_dir).test_client().post(
                "/api/admin/plugin-settings/brainbit", json={}
            )

        self.assertEqual(response.status_code, 400)

    def test_saving_never_writes_the_manifest(self) -> None:
        before = {path: path.read_bytes() for path in MANIFEST_FILES}
        with tempfile.TemporaryDirectory() as temp_dir:
            self._app(temp_dir).test_client().post(
                "/api/admin/plugin-settings/osc", json={"settings": {"port": 9001}}
            )

        self.assertEqual(
            {path: path.read_bytes() for path in MANIFEST_FILES},
            before,
            "plugin manifests are read-only shipped assets",
        )


if __name__ == "__main__":
    unittest.main()
