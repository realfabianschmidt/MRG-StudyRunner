from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from study_runner import self_check
from study_runner.apps import server as backend
from study_runner.plugin_framework.plugin_catalog import (
    PluginCatalog,
    PluginCatalogEntry,
    discover_plugin_catalog,
)


class SelfCheckIsolationTests(unittest.TestCase):
    def test_real_app_check_ignores_operator_storage_and_starts_no_background_work(self) -> None:
        seen: dict[str, Path] = {}
        original_factory = backend.create_app

        def create_app():
            seen["storage"] = Path(os.environ["STUDY_RUNNER_DATA_DIR"])
            app = original_factory()
            seen["data"] = app.config["DATA_DIR"]
            return app

        with tempfile.TemporaryDirectory() as temporary:
            operator_data = Path(temporary) / "operator"
            operator_data.mkdir()
            sentinel = operator_data / "session.json"
            sentinel.write_text('{"preserve":true}', encoding="utf-8")
            inherited = {
                "STUDY_RUNNER_DATA_DIR": str(operator_data),
                "STUDY_RUNNER_DISABLE_HARDWARE": "0",
                "STUDY_RUNNER_DISABLE_BACKGROUND": "0",
                "STUDY_RUNNER_XDF_WORKER": "operator-worker.exe",
                "STUDY_RUNNER_SELF_CHECK_PLUGIN": "",
            }
            with (
                patch.dict(os.environ, inherited),
                patch.object(backend, "create_app", side_effect=create_app),
                patch.object(backend, "initialize_plugins") as initialize,
                patch.object(backend.recording_markers, "initialize") as markers,
                patch.object(backend.recording_clock_diagnostics, "initialize") as clocks,
                patch.object(backend.TrialEventService, "resume_pending") as resume,
                patch("threading.Thread.start") as start_thread,
            ):
                self.assertEqual(self_check.main(), 0)
                self.assertEqual({key: os.environ.get(key) for key in inherited}, inherited)
            initialize.assert_not_called()
            markers.assert_not_called()
            clocks.assert_not_called()
            resume.assert_not_called()
            start_thread.assert_not_called()
            self.assertEqual(list(operator_data.iterdir()), [sentinel])
            self.assertEqual(json.loads(sentinel.read_text(encoding="utf-8")), {"preserve": True})
        self.assertNotEqual(seen["storage"], operator_data)
        self.assertTrue(seen["data"].is_relative_to(seen["storage"]))
        self.assertFalse(seen["storage"].exists(), "self-check storage must be removed on success")

    def test_failure_restores_absent_environment_keys_and_removes_temporary_storage(self) -> None:
        seen: list[Path] = []

        def fail(_fixture_key):
            seen.append(Path(os.environ["STUDY_RUNNER_DATA_DIR"]))
            self.assertEqual(os.environ["STUDY_RUNNER_DISABLE_BACKGROUND"], "1")
            raise RuntimeError("deliberately broken bundle")

        with patch.dict(os.environ, {}, clear=True), patch.object(self_check, "_check_application", side_effect=fail):
            self.assertEqual(self_check.main(), 1)
            self.assertEqual(dict(os.environ), {})
        self.assertEqual(len(seen), 1)
        self.assertFalse(seen[0].exists(), "self-check storage must be removed on failure")

    def test_invalid_catalog_entries_fail_the_check(self) -> None:
        catalog = PluginCatalog((PluginCatalogEntry("broken", "invalid", None, errors=("missing manifest",)),))
        with patch("study_runner.plugin_framework.plugin_catalog.discover_plugin_catalog", return_value=catalog):
            self.assertEqual(self_check.main(), 1)

    def test_explicit_fixture_check_rejects_an_empty_catalog(self) -> None:
        with patch.dict(os.environ, {"STUDY_RUNNER_SELF_CHECK_PLUGIN": "packaging_probe"}):
            with patch("study_runner.plugin_framework.plugin_catalog.discover_plugin_catalog", return_value=PluginCatalog(())):
                self.assertEqual(self_check.main(), 1)

    def test_packaging_fixture_is_valid_without_importing_plugin_code_in_the_host(self) -> None:
        """Phase 3.1 (docs/archive/architecture-1.0-umbau.md) removed the v3
        in-process import path this used to guard against directly
        (``plugin_catalog.importlib``); v4 discovery only reads
        ``manifest.json`` and checks that ``driver.py`` exists on disk, so
        the isolation this proves now holds by construction rather than by
        one specific avoided call. Patching the global ``importlib.import_module``
        instead keeps the test meaningful as a regression guard: the plugin's
        own code is only ever imported inside the spawned subprocess
        (``driver_runtime.run_plugin_driver``), never here.
        """
        fixture_root = Path(__file__).parent / "fixtures"
        with patch("importlib.import_module", side_effect=AssertionError("host imported fixture")):
            catalog = discover_plugin_catalog(fixture_root)
        entries = [entry for entry in catalog.entries if entry.plugin_key == "packaging_probe"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].status, "valid", entries[0].errors)

    def test_fixture_failure_still_shuts_down_its_process(self) -> None:
        fixture = PluginCatalogEntry("packaging_probe", "valid", "packaging_probe", manifest={"plugin_key": "packaging_probe"})
        app = SimpleNamespace(config={"BASE_DIR": Path("."), "DATA_DIR": Path("data"), "LOCAL_SECRETS_FILE": Path("secrets.json")})
        with patch("study_runner.plugin_framework.process_host.PluginProcessRuntime") as runtime_class, patch(
            "study_runner.plugin_framework.plugin_layout.resolve_plugin",
            return_value=(Path("fixture"), "fixture.packaging_probe"),
        ):
            runtime = runtime_class.return_value
            runtime.request.side_effect = RuntimeError("RPC failure")
            with self.assertRaisesRegex(RuntimeError, "RPC failure"):
                self_check._check_plugin_process(app, PluginCatalog((fixture,)), "packaging_probe")
            runtime.shutdown.assert_called_once_with()

    def test_frozen_discovery_accepts_archived_driver_but_source_requires_file(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "packaging_probe" / "manifest.json"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plugin = root / "packaging_probe"
            plugin.mkdir()
            (plugin / "manifest.json").write_bytes(fixture.read_bytes())
            for frozen in (True, False):
                with self.subTest(frozen=frozen), patch.object(sys, "frozen", frozen, create=True):
                    catalog = discover_plugin_catalog(root)
                    self.assertEqual(len(catalog.entries), 1)
                    self.assertEqual(catalog.entries[0].status, "valid" if frozen else "invalid")


if __name__ == "__main__":
    unittest.main()

