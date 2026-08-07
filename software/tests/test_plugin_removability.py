"""A plugin is a USB stick: pull it out and the software still runs.

That is the property the "plugin" word is supposed to promise. It did not hold
for two folders that used to live here: `lsl_markers` carried the study's own
event markers and `clock_diagnostics` measured clock sync, and deleting either
broke recording -- the code that resolved them raised unless it found *exactly
one* of each. That was a necessity wearing a manifest, not an optional part, so
both are now real recording code instead -- see `recording/markers.py` and
`recording/clock_diagnostics.py`.

This test makes the promise executable for what is left, instead of assumed. It
works on a copy of the real `plugins/` tree with one folder removed, so a
regression here means an operator's plugin folder, not a fixture.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.plugin_framework.plugin_catalog import discover_plugin_catalog


PLUGINS_DIR = PROJECT_ROOT / "study_runner" / "plugins"

# Every folder under plugins/ must have this property. If a folder is added
# that is not meant to be removable, it does not belong under plugins/ --
# see recording/markers.py and recording/clock_diagnostics.py for the pattern
# built-in recording concerns follow instead.
REMOVABLE_PLUGINS = tuple(
    sorted(
        path.name
        for path in PLUGINS_DIR.iterdir()
        if path.is_dir() and (path / "manifest.json").is_file()
    )
)


class PlugInAndPullOutTests(unittest.TestCase):
    """Named per-plugin so a failure says which one broke, not just that one did."""

    def _catalog_without(self, plugin_key: str):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "plugins"
            shutil.copytree(PLUGINS_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.rmtree(copy / plugin_key)
            return discover_plugin_catalog(copy, package_name="study_runner.plugins")

    def test_every_declared_plugin_is_actually_removable(self) -> None:
        for plugin_key in REMOVABLE_PLUGINS:
            with self.subTest(plugin=plugin_key):
                catalog = self._catalog_without(plugin_key)

                self.assertEqual(
                    catalog.invalid_entries,
                    (),
                    f"removing {plugin_key} left another plugin invalid: {catalog.invalid_entries}",
                )
                remaining_keys = {plugin.key for plugin in catalog.plugins}
                self.assertNotIn(plugin_key, remaining_keys)
                self.assertEqual(len(catalog.plugins), len(REMOVABLE_PLUGINS) - 1)

    def test_the_recording_plan_is_unaffected_by_any_one_plugin_removed(self) -> None:
        """The two sources every session carries are code now, not a catalog lookup."""
        from study_runner.backend.services.recording.recording_dependencies import (
            INTERNAL_RECORDING_SOURCE_KEYS,
        )

        for plugin_key in REMOVABLE_PLUGINS:
            with self.subTest(plugin=plugin_key):
                catalog = self._catalog_without(plugin_key)

                self.assertEqual(INTERNAL_RECORDING_SOURCE_KEYS, ("lsl", "clock_diagnostics"))
                self.assertNotIn(plugin_key, INTERNAL_RECORDING_SOURCE_KEYS)


if __name__ == "__main__":
    unittest.main()
