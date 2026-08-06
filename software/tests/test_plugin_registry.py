from __future__ import annotations

import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.plugin_framework.registry import (
    PLUGINS,
    PLUGINS_BY_KEY,
    get_plugin_manifest,
    get_plugin_manifests,
    get_backup_projection_specs,
    get_plugins_with_capability,
    get_sample_metadata_model,
    ingest_participant_payload,
    run_admin_action,
    run_participant_action,
)


EXPECTED_PLUGIN_MAPPING = {
    "brainbit": ("brainbit", "brainbit"),
    "mr60_mini_radar": ("mini_radar", "mini_radar"),
    "camera_emotion": ("camera_emotion", "camera_emotion"),
    "lsl_markers": ("lsl", "lsl"),
    "clock_diagnostics": ("clock_diagnostics", "clock_diagnostics"),
    "osc_touchdesigner": ("osc", "osc"),
    "notion_upload": ("notion", "notion"),
    "nextcloud_upload": ("nextcloud", "nextcloud"),
}


class PluginRegistryContractTests(unittest.TestCase):
    def test_folder_plugin_key_and_config_key_mapping_is_explicit(self) -> None:
        actual = {}
        for folder in EXPECTED_PLUGIN_MAPPING:
            module = importlib.import_module(
                f"study_runner.plugins.{folder}.plugin"
            )
            plugin = module.PLUGIN
            actual[folder] = (plugin.key, plugin.config_key)
            self.assertIs(
                PLUGINS_BY_KEY[plugin.key],
                plugin,
                f"{folder} plugin is not the object registered under {plugin.key}",
            )

        self.assertEqual(actual, EXPECTED_PLUGIN_MAPPING)

    def test_registry_contains_each_documented_plugin_once(self) -> None:
        expected_keys = {
            plugin_key
            for plugin_key, _config_key in EXPECTED_PLUGIN_MAPPING.values()
        }
        registered_keys = [plugin.key for plugin in PLUGINS]

        self.assertEqual(set(registered_keys), expected_keys)
        self.assertEqual(len(registered_keys), len(set(registered_keys)))
        self.assertEqual(set(PLUGINS_BY_KEY), expected_keys)

    def test_each_active_plugin_owns_one_config_key(self) -> None:
        config_to_plugins: dict[str, set[str]] = {}
        for plugin in PLUGINS:
            config_to_plugins.setdefault(plugin.config_key, set()).add(plugin.key)
        duplicates = {
            config_key: plugin_keys
            for config_key, plugin_keys in config_to_plugins.items()
            if len(plugin_keys) > 1
        }

        self.assertEqual(duplicates, {})

    def test_each_registered_plugin_has_a_manifest(self) -> None:
        manifests = get_plugin_manifests()
        self.assertEqual(set(manifests), {plugin.key for plugin in PLUGINS})

        for plugin in PLUGINS:
            with self.subTest(plugin=plugin.key):
                manifest = get_plugin_manifest(plugin.key)
                self.assertEqual(manifest["plugin_key"], plugin.key)
                self.assertEqual(manifest["config_key"], plugin.config_key)
                self.assertGreater(manifest["poll_interval_ms"], 0)
                self.assertGreater(manifest["request_timeout_ms"], 0)
                self.assertGreaterEqual(manifest["backpressure"]["max_in_flight"], 1)
                self.assertIsInstance(manifest["capabilities"], list)
                self.assertEqual(manifest["api_version"], 3)
                self.assertIn("health", manifest["capabilities"])
                self.assertEqual(manifest["entry_point"], "plugin:PLUGIN")
                self.assertEqual(
                    set(manifest["ui"]["visibility"]),
                    {
                        "dashboard",
                        "settings_hub",
                        "study_settings",
                        "destination_settings",
                    },
                )

    def test_sample_metadata_model_has_required_timing_fields(self) -> None:
        fields = set(get_sample_metadata_model())
        self.assertTrue(
            {
                "source_epoch_ms",
                "server_received_epoch_ms",
                "processing_epoch_ms",
                "sequence_number",
                "latency_ms",
                "clock_domain",
                "drop_count",
            }.issubset(fields)
        )

    def test_capability_queries_are_manifest_driven(self) -> None:
        sensors = {plugin.key for plugin in get_plugins_with_capability("study_sensor")}
        self.assertEqual(sensors, {"brainbit", "mini_radar", "camera_emotion"})

        projections = get_backup_projection_specs({"brainbit", "mini_radar"})
        self.assertEqual({projection["plugin_key"] for projection in projections}, sensors - {"camera_emotion"})
        self.assertTrue(all(projection["rate_hz"] > 0 for projection in projections))
        self.assertTrue(all(projection["channels"] for projection in projections))
        self.assertTrue(
            all(
                channel["output"] and channel["stream"] and channel["channel"]
                for projection in projections
                for channel in projection["channels"]
            )
        )

    def test_recording_plugins_declare_how_samples_reach_lsl(self) -> None:
        expected = {
            "brainbit": ("ble", "host_lsl_bridge"),
            "mini_radar": ("local_hardware", "host_lsl_bridge"),
            "camera_emotion": ("browser_https", "host_lsl_bridge"),
            "lsl": ("internal", "host_lsl_bridge"),
            "clock_diagnostics": ("internal", "host_lsl_bridge"),
        }
        for plugin_key, pair in expected.items():
            with self.subTest(plugin=plugin_key):
                config = get_plugin_manifest(plugin_key)["capability_config"][
                    "acquisition_transport"
                ]
                self.assertEqual((config["transport"], config["delivery"]), pair)

    def test_legacy_worker_and_labrecorder_packages_are_not_catalog_plugins(self) -> None:
        manifests = get_plugin_manifests()
        self.assertNotIn("emotion_worker", manifests)
        self.assertNotIn("labrecorder", manifests)

    def test_internal_and_destination_visibility_is_explicit(self) -> None:
        hidden = {
            "dashboard": False,
            "settings_hub": False,
            "study_settings": False,
            "destination_settings": False,
        }
        self.assertEqual(get_plugin_manifest("lsl")["ui"]["visibility"], hidden)
        self.assertEqual(
            get_plugin_manifest("clock_diagnostics")["ui"]["visibility"],
            hidden,
        )
        destination = {
            "dashboard": False,
            "settings_hub": False,
            "study_settings": True,
            "destination_settings": True,
        }
        self.assertEqual(get_plugin_manifest("notion")["ui"]["visibility"], destination)
        self.assertEqual(
            get_plugin_manifest("nextcloud")["ui"]["visibility"], destination
        )

    def test_disable_reinitialization_is_manifest_driven(self) -> None:
        for plugin_key in ("mini_radar", "camera_emotion", "notion"):
            self.assertTrue(
                get_plugin_manifest(plugin_key)["lifecycle"][
                    "reinitialize_on_disable"
                ]
            )
        registry_source = (
            PROJECT_ROOT / "study_runner" / "plugin_framework" / "registry.py"
        ).read_text(encoding="utf-8")
        self.assertIn('lifecycle.get("reinitialize_on_disable")', registry_source)
        self.assertNotIn('key in {"mini_radar"', registry_source)

    def test_generic_admin_action_allows_only_manifest_declared_keys(self) -> None:
        with (
            patch(
                "study_runner.plugins.camera_emotion.worker.plugin.repair_runtime",
                return_value={"queued": True},
            ),
            patch(
                "study_runner.plugin_framework.registry.get_plugin_status",
                return_value={"status": "running"},
            ),
        ):
            result = run_admin_action("camera_emotion", "repair_runtime", object())

        self.assertEqual(result["result"], {"queued": True})
        self.assertEqual(result["plugin_key"], "camera_emotion")
        self.assertEqual(result["action_key"], "repair_runtime")
        with self.assertRaisesRegex(ValueError, "does not declare admin action"):
            run_admin_action("camera_emotion", "format_disk", object())

    def test_participant_dispatch_uses_only_manifest_declared_operations(self) -> None:
        context = object()
        with (
            patch("study_runner.plugins.camera_emotion.plugin._initialize"),
            patch(
                "study_runner.plugins.camera_emotion.plugin._start",
                return_value={"enabled": True},
            ),
            patch(
                "study_runner.plugins.camera_emotion.adapter.set_preview_active",
                return_value={"active": True},
            ) as preview_active,
            patch(
                "study_runner.plugins.camera_emotion.adapter.is_configured",
                return_value=True,
            ),
            patch(
                "study_runner.plugins.camera_emotion.adapter.process_frame",
                return_value={"accepted": True, "sequence_number": 7},
            ) as process_frame,
            patch(
                "study_runner.plugin_framework.registry.get_plugin_status",
                return_value={"status": "ready"},
            ),
        ):
            action = run_participant_action(
                "camera_emotion",
                "start_monitor",
                context,
                {"study_id": "study-a"},
            )
            ingest = ingest_participant_payload(
                "camera_emotion",
                "frame",
                context,
                {"sequence_number": 7, "source_epoch_ms": 1000.0},
            )

        self.assertTrue(action["result"]["monitor_active"])
        self.assertTrue(ingest["ok"])
        self.assertEqual(ingest["result"]["sequence_number"], 7)
        preview_active.assert_called_once_with(True)
        process_frame.assert_called_once_with(
            {"sequence_number": 7, "source_epoch_ms": 1000.0}
        )

        with self.assertRaisesRegex(ValueError, "does not declare participant action"):
            run_participant_action("camera_emotion", "format_disk", context)
        with self.assertRaisesRegex(ValueError, "does not declare participant ingest"):
            ingest_participant_payload("camera_emotion", "commands", context, {})

    def test_declared_lsl_source_ids_match_adapter_constants(self) -> None:
        adapter_modules = {
            "brainbit": "brainbit.adapter",
            "mini_radar": "mr60_mini_radar.adapter",
            "camera_emotion": "camera_emotion.adapter",
            "lsl": "lsl_markers.adapter",
            "clock_diagnostics": "clock_diagnostics.adapter",
        }
        for plugin_key, module_suffix in adapter_modules.items():
            with self.subTest(plugin=plugin_key):
                adapter = importlib.import_module(f"study_runner.plugins.{module_suffix}")
                manifest_ids = {
                    stream["key"]: stream["source_id"]
                    for stream in get_plugin_manifest(plugin_key)["streams"]
                }
                self.assertEqual(manifest_ids, adapter.LSL_SOURCE_IDS)
                manifest_units = {
                    stream["key"]: tuple(stream["channel_units"])
                    for stream in get_plugin_manifest(plugin_key)["streams"]
                }
                self.assertEqual(manifest_units, adapter.LSL_CHANNEL_UNITS)


if __name__ == "__main__":
    unittest.main()
