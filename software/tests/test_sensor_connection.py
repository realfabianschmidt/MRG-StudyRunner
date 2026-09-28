"""The shared connection pattern every sensor plugin follows.

Plugins report facts (phase, signal, setup, streaming). The core decides
whether a sensor is ready and which action is the next step, identically for
every sensor, so the dashboard can guide the operator the same way for a
headband, a radar or a camera.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.contracts.manifest import PluginManifestError, validate_and_normalize_manifest
from study_runner.plugin_framework.sensor_connection import (
    action_roles,
    is_ready,
    next_step,
    standardize_connection,
)

PLUGINS_ROOT = PROJECT_ROOT / "study_runner" / "plugins"
TEMPLATE = PROJECT_ROOT.parent / "tools" / "plugin_templates" / "sensors" / "manifest.json"
ALL_ROLES = ("select", "scan", "measure_signal", "initialize")


def _connected(signal="good", setup="done", streaming=True):
    return {
        "phase": "connected",
        "signal": {"state": signal},
        "setup": {"state": setup},
        "streaming": streaming,
    }


class ConnectionRuleTests(unittest.TestCase):
    def test_guided_order_for_a_headband(self) -> None:
        cases = [
            ({"phase": "no_device"}, "scan", False),
            ({"phase": "searching"}, None, False),
            ({"phase": "selection_required"}, "select", False),
            ({"phase": "connecting"}, None, False),
            (_connected(signal="unknown", setup="needed"), "measure_signal", False),
            (_connected(signal="measuring", setup="needed"), None, False),
            (_connected(signal="poor", setup="needed"), "measure_signal", False),
            (_connected(signal="good", setup="needed"), "initialize", False),
            (_connected(signal="good", setup="running"), None, False),
            (_connected(signal="good", setup="stalled"), "initialize", False),
            (_connected(signal="good", setup="done"), None, True),
            (_connected(signal="stale", setup="needed"), "measure_signal", False),
            (_connected(signal="good", setup="done", streaming=False), None, False),
            ({"phase": "failed"}, "scan", False),
            ({"phase": "off"}, None, False),
        ]
        for connection, expected_step, expected_ready in cases:
            with self.subTest(connection=connection):
                self.assertEqual(next_step(connection, ALL_ROLES), expected_step)
                self.assertEqual(is_ready(connection, ALL_ROLES), expected_ready)

    def test_sensors_without_actions_are_ready_once_they_stream(self) -> None:
        self.assertTrue(is_ready(_connected(signal="unknown", setup="not_needed"), ()))
        self.assertIsNone(next_step({"phase": "no_device"}, ()))

    def test_a_stopped_plugin_is_off_whatever_it_last_reported(self) -> None:
        connection = standardize_connection(
            {"connection": _connected()},
            running=False,
            roles=ALL_ROLES,
        )
        self.assertEqual(connection["phase"], "off")
        self.assertFalse(connection["ready"])

    def test_plugins_without_a_block_get_one_from_their_status(self) -> None:
        derived = standardize_connection({"status": "connected"}, running=True)
        self.assertEqual(derived["phase"], "connected")
        self.assertTrue(derived["ready"])
        self.assertEqual(standardize_connection({"status": "scanning"}, running=True)["phase"], "searching")
        self.assertEqual(standardize_connection({"status": "failed"}, running=True)["phase"], "failed")
        self.assertEqual(standardize_connection({"status": "stopped"}, running=False)["phase"], "off")

    def test_unknown_values_are_normalized_not_trusted(self) -> None:
        connection = standardize_connection(
            {"connection": {"phase": "teleporting", "signal": {"state": "excellent"}, "setup": {"state": "?"}}},
            running=True,
        )
        self.assertEqual(connection["phase"], "connecting")
        self.assertEqual(connection["signal"]["state"], "unknown")
        self.assertEqual(connection["setup"]["state"], "not_needed")


def _brainbit_payload() -> dict:
    return json.loads((PLUGINS_ROOT / "sensors" / "brainbit" / "manifest.json").read_text(encoding="utf-8"))


class ManifestRoleTests(unittest.TestCase):
    def test_roles_are_read_from_the_manifest(self) -> None:
        manifest = validate_and_normalize_manifest(_brainbit_payload(), directory_name="brainbit")
        roles = action_roles(manifest)
        self.assertEqual(set(roles), {"select", "scan", "measure_signal", "initialize"})
        self.assertEqual(roles["select"], "select_device")

    def test_invalid_or_duplicate_roles_are_rejected(self) -> None:
        payload = _brainbit_payload()
        actions = payload["capabilities"]["admin_actions"]["actions"]
        invalid = deepcopy(payload)
        invalid["capabilities"]["admin_actions"]["actions"][1]["role"] = "teleport"
        with self.assertRaises(PluginManifestError):
            validate_and_normalize_manifest(invalid, directory_name="brainbit")
        duplicate = deepcopy(payload)
        duplicate["capabilities"]["admin_actions"]["actions"][2]["role"] = actions[1]["role"]
        with self.assertRaises(PluginManifestError):
            validate_and_normalize_manifest(duplicate, directory_name="brainbit")

    def test_select_role_needs_a_device_list(self) -> None:
        payload = _brainbit_payload()
        scan = next(a for a in payload["capabilities"]["admin_actions"]["actions"] if a.get("role") == "scan")
        select = next(a for a in payload["capabilities"]["admin_actions"]["actions"] if a.get("role") == "select")
        select.pop("role")
        scan["role"] = "select"
        with self.assertRaises(PluginManifestError):
            validate_and_normalize_manifest(payload, directory_name="brainbit")


class SensorManifestContractTests(unittest.TestCase):
    """Every sensor, and the template new sensors start from, joins the pattern."""

    def _sensor_manifests(self):
        for path in sorted(PLUGINS_ROOT.glob("*/*/manifest.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            capabilities = payload.get("capabilities") or {}
            names = capabilities if isinstance(capabilities, list) else list(capabilities)
            if "study_sensor" in names:
                yield path.parent.name, payload

    def test_every_sensor_declares_the_connection_capability(self) -> None:
        sensors = list(self._sensor_manifests())
        self.assertTrue(sensors)
        for directory, payload in [*sensors, ("template", json.loads(TEMPLATE.read_text(encoding="utf-8")))]:
            with self.subTest(plugin=directory):
                self.assertIn("connection", payload["capabilities"])
                manifest = validate_and_normalize_manifest(
                    payload,
                    directory_name=payload.get("plugin_key") or directory,
                )
                config = manifest["capability_config"]["connection"]
                self.assertIn("setup_per_participant", config)

    def test_sensors_that_need_a_setup_offer_an_initialize_action(self) -> None:
        for directory, payload in self._sensor_manifests():
            manifest = validate_and_normalize_manifest(
                payload,
                directory_name=payload.get("plugin_key") or directory,
            )
            if manifest["capability_config"]["connection"].get("setup_per_participant"):
                with self.subTest(plugin=directory):
                    self.assertIn("initialize", action_roles(manifest))
                    self.assertIn("session_end", manifest["runtime"]["trial_events"])


if __name__ == "__main__":
    unittest.main()
