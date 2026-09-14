"""A plugin is a USB stick: pull it out and the software still runs.

That is the property the "plugin" word is supposed to promise. It did not hold
for two folders that used to live here: `lsl_markers` carried the study's own
event markers and `clock_diagnostics` measured clock sync, and deleting either
broke recording -- the code that resolved them raised unless it found *exactly
one* of each. That was a necessity wearing a manifest, not an optional part, so
both are now real recording code instead -- see `recording/markers.py` and
`recording/clock_diagnostics.py`.

This test makes the promise executable for what is left, instead of assumed. It
works on a flat copy of the real `plugins/*/` trees with one folder removed, so a
regression here means an operator's plugin folder, not a fixture.

Package 5g.B5 made cards real extensions under `plugins/cards/`, so they are
swept into this file's per-plugin checks too, and the promise holds: removing
any one card's folder leaves every other plugin's manifest valid and the
internal recording plan untouched, same as any sensor/destination/output.
The one exception is the *zero-catalog* edge case below -- `participant-id`
and `finish` are the mandatory bookends of every study, so a catalog with
neither installed cannot author a *playable* study. That is the same
"necessity wearing a manifest" property `lsl_markers`/`clock_diagnostics` had
(and it is why they no longer live under `plugins/`); cards stay real,
process-isolated extensions regardless, because the isolation guarantee
matters for participant-id/finish too, not just for peripheral hardware.
"""
from __future__ import annotations

from pathlib import Path
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.plugin_framework.plugin_catalog import PluginCatalog, discover_plugin_catalog
from study_runner.plugin_framework.registry import reload_plugin_catalog


PLUGINS_DIR = PROJECT_ROOT / "study_runner" / "plugins"

# Every folder under plugins/ must have this property. If a folder is added
# that is not meant to be removable, it does not belong under plugins/ --
# see recording/markers.py and recording/clock_diagnostics.py for the pattern
# built-in recording concerns follow instead.
REMOVABLE_PLUGINS = tuple(
    sorted(
        path.name
        for category in PLUGINS_DIR.iterdir() if category.is_dir()
        for path in category.iterdir() if path.is_dir() and (path / "manifest.json").is_file()
    )
)


class PlugInAndPullOutTests(unittest.TestCase):
    """Named per-plugin so a failure says which one broke, not just that one did."""

    def tearDown(self) -> None:
        # discover_plugin_catalog() on a temp copy makes every v4 plugin's
        # process_host.build_process_plugin() replace that plugin's entry in
        # the module-global _RUNTIMES registry, pointed at a directory that
        # is deleted the moment the temporary copy goes out of scope. Left
        # alone, that orphaned runtime silently leaks into later tests in the
        # same process: get_process_runtime() starts returning it, while
        # PLUGINS_BY_KEY's dispatch closures still reference whichever
        # runtime object existed when the module first loaded -- two
        # different objects, so mocking the one and dispatching through the
        # other silently talks to a real subprocess. reload_plugin_catalog()
        # rebuilds both the runtime registry AND PLUGINS_BY_KEY's closures
        # against the real plugins/ directory together, so they stay the
        # same object again before the next test runs.
        reload_plugin_catalog()

    def _catalog_without(self, plugin_key: str):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "plugins"
            copy.mkdir()
            for category in PLUGINS_DIR.iterdir():
                if not category.is_dir():
                    continue
                for source in category.iterdir():
                    if source.is_dir() and (source / "manifest.json").is_file():
                        shutil.copytree(source, copy / source.name, ignore=shutil.ignore_patterns("__pycache__"))
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
        from study_runner.data_core.host.recording_dependencies import (
            INTERNAL_RECORDING_SOURCE_KEYS,
        )

        for plugin_key in REMOVABLE_PLUGINS:
            with self.subTest(plugin=plugin_key):
                catalog = self._catalog_without(plugin_key)

                self.assertEqual(INTERNAL_RECORDING_SOURCE_KEYS, ("lsl", "clock_diagnostics"))
                self.assertNotIn(plugin_key, INTERNAL_RECORDING_SOURCE_KEYS)

    def test_empty_catalog_keeps_app_admin_hardware_and_study_io_available(self) -> None:
        """Zero plugins is a supported deployment, not an import-time accident."""
        from study_runner.apps.server import create_app
        from study_runner.plugin_framework import registry

        with tempfile.TemporaryDirectory() as plugin_dir, tempfile.TemporaryDirectory() as data_dir:
            empty_catalog = discover_plugin_catalog(Path(plugin_dir))
            self.assertEqual(empty_catalog, PluginCatalog(entries=()))
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
                "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
            }
            with (
                patch.dict(os.environ, env, clear=False),
                patch.object(registry, "_PLUGIN_CATALOG", empty_catalog),
                patch.object(registry, "PLUGINS", ()),
                patch.object(registry, "PLUGINS_BY_KEY", {}),
            ):
                app = create_app()
                client = app.test_client()

                self.assertEqual(client.get("/").status_code, 200)
                self.assertEqual(client.get("/admin").status_code, 200)
                self.assertEqual(client.get("/api/plugins/catalog").get_json()["plugins"], [])
                self.assertEqual(client.post("/api/admin/plugins/absent/start", json={}).status_code, 404)
                self.assertEqual(client.post("/api/admin/plugins/brainbit/start", json={}).status_code, 404)

                hardware_save = client.post(
                    "/api/hardware-config",
                    json={"lsl": {"resolve_timeout_seconds": 2}},
                )
                self.assertEqual(hardware_save.status_code, 200, hardware_save.get_data(as_text=True))

                # Config I/O and study_settings (sensor/plugin overrides) do not
                # depend on which cards are installed -- an uninstalled sensor
                # referenced here is preserved opaquely, same as any other
                # unrecognised plugin section.
                study_save = client.post(
                    "/api/config",
                    json={
                        "study_id": "empty-plugin-study",
                        "questions": [],
                        "study_settings": {
                            "sensors_enabled": True,
                            "sensors": {"missing_sensor": True},
                            "plugins": {
                                "missing_sensor": {
                                    "enabled": True,
                                    "required": True,
                                    "settings": {"opaque": {"value": 7}},
                                }
                            },
                        },
                    },
                )
                self.assertEqual(study_save.status_code, 200, study_save.get_data(as_text=True))
                reloaded = client.get("/api/config").get_json()

                # Package 5g.B5: participant-id/finish are card extensions now,
                # not hardcoded types -- authoring a *playable* study needs at
                # least those two installed, the same "necessity wearing a
                # manifest" property lsl_markers/clock_diagnostics already had
                # (see this file's module docstring). That is an accepted
                # behavior change, not a regression, as long as the failure
                # stays the same clean 400 every other missing card produces
                # instead of a crash or a silent accept.
                card_dependent_save = client.post(
                    "/api/config",
                    json={
                        "study_id": "empty-plugin-study",
                        "questions": [{"type": "participant-id"}, {"type": "finish"}],
                        "study_settings": {"sensors_enabled": False},
                    },
                )
                self.assertEqual(card_dependent_save.status_code, 400)
                self.assertEqual(card_dependent_save.get_json()["error_code"], "invalid_input")

            self.assertEqual(
                reloaded["study_settings"]["plugins"]["missing_sensor"]["settings"],
                {"opaque": {"value": 7}},
            )


if __name__ == "__main__":
    unittest.main()
