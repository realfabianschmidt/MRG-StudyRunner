from __future__ import annotations

import importlib
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.integrations.registry import PLUGINS, PLUGINS_BY_KEY


EXPECTED_PLUGIN_MAPPING = {
    "brainbit": ("brainbit", "brainbit"),
    "mr60_mini_radar": ("mini_radar", "mini_radar"),
    "tablet_camera_emotion": ("camera_emotion", "camera_emotion"),
    "local_emotion_worker": ("emotion_worker", "camera_emotion"),
    "lsl_markers": ("lsl", "lsl"),
    "osc_touchdesigner": ("osc", "osc"),
    "labrecorder_xdf": ("labrecorder", "labrecorder"),
    "notion_upload": ("notion", "notion"),
}


class PluginRegistryContractTests(unittest.TestCase):
    def test_folder_plugin_key_and_config_key_mapping_is_explicit(self) -> None:
        actual = {}
        for folder in EXPECTED_PLUGIN_MAPPING:
            module = importlib.import_module(
                f"study_runner.integrations.{folder}.plugin"
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

    def test_shared_camera_config_key_is_the_only_documented_duplicate(self) -> None:
        config_to_plugins: dict[str, set[str]] = {}
        for plugin in PLUGINS:
            config_to_plugins.setdefault(plugin.config_key, set()).add(plugin.key)
        duplicates = {
            config_key: plugin_keys
            for config_key, plugin_keys in config_to_plugins.items()
            if len(plugin_keys) > 1
        }

        self.assertEqual(
            duplicates,
            {"camera_emotion": {"camera_emotion", "emotion_worker"}},
        )


if __name__ == "__main__":
    unittest.main()
