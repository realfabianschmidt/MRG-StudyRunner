"""Every plugin process must implement exactly the operations it declares."""

from __future__ import annotations

import importlib
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.contracts.plugin_api import Plugin
from study_runner.plugin_framework.plugin_layout import resolve_plugin
from study_runner.plugin_framework.process_host import PROTOCOL_PREFIX
from study_runner.plugin_framework.runtime_contract import (
    declared_operations,
    validate_plugin_callbacks,
)
from study_runner.plugin_framework.registry import initialize_plugin, run_runtime_action


class PluginRuntimeContractTests(unittest.TestCase):
    def test_child_process_refuses_an_undeclared_rpc(self) -> None:
        from study_runner.plugin_framework.driver_runtime import _serve_plugin_driver

        plugin = Plugin(
            key="fixture", label="Fixture", category="test", config_key="fixture",
            get_status=lambda _context: {"status": "ready"},
        )
        manifest = {"capabilities": {"health": {}}, "runtime": {"actions": []}}
        requests = [
            {"kind": "request", "id": "initialize", "operation": "initialize", "payload": {"context": {}}},
            {"kind": "request", "id": "publish", "operation": "publish", "payload": {}},
            {"kind": "request", "id": "shutdown", "operation": "shutdown", "payload": {}},
        ]
        input_stream = StringIO("".join(PROTOCOL_PREFIX + json.dumps(item) + "\n" for item in requests))
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin_dir = Path(temp_dir)
            (plugin_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with (
                patch("sys.stdin", input_stream),
                patch("study_runner.plugin_framework.driver_runtime._emit_response") as emit,
                patch("study_runner.plugin_framework.driver_runtime.resolve_plugin", return_value=(plugin_dir, "fixture")),
                patch("study_runner.plugin_framework.driver_runtime.importlib.import_module", return_value=SimpleNamespace(PLUGIN=plugin)),
            ):
                self.assertEqual(_serve_plugin_driver("fixture"), 0)
        self.assertTrue(emit.call_args_list[0].kwargs["ok"])
        self.assertFalse(emit.call_args_list[1].kwargs["ok"])
        self.assertIn("not declared", emit.call_args_list[1].kwargs["error"])

    def test_declared_capabilities_select_internal_operations(self) -> None:
        manifest = {
            "runtime": {"actions": ["start", "stop"], "trial_events": ["session_end"]},
            "capabilities": {"study_sensor": {}, "recording_source": {}},
        }
        self.assertEqual(
            declared_operations(manifest),
            {"initialize", "status", "shutdown", "start", "stop", "session_end"},
        )
        destination = {
            "runtime": {"actions": [], "trial_events": []},
            "capabilities": {"upload_destination": {}},
        }
        self.assertEqual(
            declared_operations(destination),
            {"initialize", "status", "shutdown", "publish"},
        )
        actuator = {
            "runtime": {"trial_events": ["start", "stop"]},
            "capabilities": {},
        }
        self.assertEqual(
            declared_operations(actuator),
            {"initialize", "status", "shutdown", "trial_start", "trial_stop"},
        )
        card = {
            "runtime": {},
            "capabilities": {"card_contract": {"question_types": ["example"]}},
        }
        self.assertEqual(
            declared_operations(card),
            {"initialize", "status", "shutdown", "card_defaults", "card_normalize", "card_validate_answer"},
        )

    def test_an_object_study_setting_routes_through_validate_study_setting(self) -> None:
        """An "object" field (e.g. a plugin's own export mapping) is always
        plugin-shaped, the same way a "url" field with a declared format is -
        see runtime_core/studies/validation.py and process_host.py's own
        matching has_study_validator check."""
        manifest = {
            "runtime": {}, "capabilities": {},
            "study_settings_schema": {"export_mapping": {"type": "object"}},
        }
        self.assertIn("validate_study_setting", declared_operations(manifest))
        plain_string_manifest = {
            "runtime": {}, "capabilities": {},
            "study_settings_schema": {"label": {"type": "string"}},
        }
        self.assertNotIn("validate_study_setting", declared_operations(plain_string_manifest))

    def test_missing_declared_callback_fails_before_a_plugin_runs(self) -> None:
        plugin = Plugin(key="example", label="Example", category="biosignal", config_key="example")
        with self.assertRaisesRegex(TypeError, "start \\(start\\)"):
            validate_plugin_callbacks(
                plugin,
                {"runtime": {"actions": ["start"]}, "capabilities": {}},
            )
        with self.assertRaisesRegex(TypeError, "status \\(get_status\\)"):
            validate_plugin_callbacks(
                plugin,
                {"runtime": {}, "capabilities": {"health": {}}},
            )

    def test_session_start_reports_initialization_failure(self) -> None:
        def fail(_context: object) -> None:
            raise RuntimeError("device unavailable")

        plugin = Plugin(
            key="example", label="Example", category="biosignal", config_key="example",
            initialize=fail,
        )
        with patch("study_runner.plugin_framework.registry._require_plugin", return_value=plugin):
            initialize_plugin("example", object())
            with self.assertRaisesRegex(RuntimeError, "device unavailable"):
                initialize_plugin("example", object(), strict=True)

    def test_failed_start_command_or_status_is_not_acknowledged_as_active(self) -> None:
        plugin = Plugin(
            key="example", label="Example", category="biosignal", config_key="example",
            can_start=True, start=lambda _context: {"ok": False},
        )
        with (
            patch("study_runner.plugin_framework.registry._require_plugin", return_value=plugin),
            patch("study_runner.plugin_framework.registry.get_plugin_status", return_value={"status": "connecting"}),
        ):
            self.assertFalse(run_runtime_action("example", "start", object())["ok"])

        plugin = Plugin(
            key="example", label="Example", category="biosignal", config_key="example",
            can_start=True, start=lambda _context: None,
        )
        with (
            patch("study_runner.plugin_framework.registry._require_plugin", return_value=plugin),
            patch(
                "study_runner.plugin_framework.registry.get_plugin_status",
                return_value={"status": "failed", "last_message": "stream cannot open"},
            ),
        ):
            outcome = run_runtime_action("example", "start", object())
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["error"], "stream cannot open")

        plugin = Plugin(
            key="example", label="Example", category="biosignal", config_key="example",
            can_start=True, start=lambda _context: {"status": "failed", "last_message": "device refused"},
        )
        with (
            patch("study_runner.plugin_framework.registry._require_plugin", return_value=plugin),
            patch("study_runner.plugin_framework.registry.get_plugin_status", return_value={"status": "connecting"}),
        ):
            outcome = run_runtime_action("example", "start", object())
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["error"], "device refused")

    def test_every_installed_plugin_implements_its_manifest_operations(self) -> None:
        root = PROJECT_ROOT / "study_runner" / "plugins"
        for path in root.glob("*/*/manifest.json"):
            manifest = json.loads(path.read_text(encoding="utf-8"))
            plugin_key = manifest["plugin_key"]
            with self.subTest(plugin=plugin_key):
                package = resolve_plugin(plugin_key)[1]
                plugin = importlib.import_module(f"{package}.plugin").PLUGIN
                validate_plugin_callbacks(plugin, manifest)


if __name__ == "__main__":
    unittest.main()
