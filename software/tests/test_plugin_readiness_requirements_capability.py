"""A third destination should get a readiness check for free, not a core edit.

Before this, "is this destination actually configured" was four blocks of
hand-written checks naming `notion` and `nextcloud` explicitly in
study_readiness_service.py. This test proves the replacement is genuinely
generic: a fixture plugin that exists only inside this test, declaring
nothing but a `readiness_requirements` capability, gets exactly the same
blockers Notion and Nextcloud get -- missing secret, missing setting, and
machine disabled -- without study_readiness_service.py ever having heard of
it.

Same temp-package + registry-patching pattern as
`test_plugin_credentials_capability.py`.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.plugin_framework import registry
from study_runner.plugin_framework.plugin_catalog import PluginCatalog, discover_plugin_catalog


PLUGIN_KEY = "fixture_vault"

MANIFEST = {
    "api_version": 3,
    "plugin_key": PLUGIN_KEY,
    "version": "1.0.0",
    "category": "storage",
    "config_key": PLUGIN_KEY,
    "entry_point": "plugin:PLUGIN",
    "ui": {
        "label": "Fixture Vault",
        "order": 999,
        "visibility": {
            "dashboard": False,
            "settings_hub": False,
            "study_settings": True,
            "destination_settings": True,
        },
    },
    "capabilities": {
        "upload_destination": {"destination": "fixture_vault", "default_enabled": False},
        "health": {},
        "study_settings": {},
        "credentials": {
            "config_field": "access_token",
            "env_var": "STUDY_RUNNER_FIXTURE_VAULT_TOKEN",
            "per_study": True,
        },
        "readiness_requirements": {
            "requires_secret": True,
            "requires_settings": ["bucket_name"],
            "requires_machine_enabled": True,
        },
    },
    "streams": [],
    "settings": {
        "machine": {},
        "study": {"bucket_name": {"type": "string", "default": ""}},
        "card_actions": {},
    },
}

PLUGIN_PY = """
from study_runner.plugin_framework.plugin_api import Plugin

def _status(context):
    return {{"status": "waiting"}}

def _publish(context, payload):
    return {{"ok": True}}

PLUGIN = Plugin(
    key="{key}", label="Fixture Vault", category="storage", config_key="{key}",
    get_status=_status,
    publish_destination=_publish,
)
""".format(key=PLUGIN_KEY)


class FixtureDestinationReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = PROJECT_ROOT / ".tmp" / "fixture-plugin-readiness"
        parent.mkdir(parents=True, exist_ok=True)
        self.root = parent / uuid.uuid4().hex
        self.package_dir = self.root / f"fixture_plugins_{self.root.name}"
        self.plugin_dir = self.package_dir / PLUGIN_KEY
        self.plugin_dir.mkdir(parents=True)
        (self.package_dir / "__init__.py").write_text("", encoding="utf-8")
        (self.plugin_dir / "__init__.py").write_text("", encoding="utf-8")
        (self.plugin_dir / "manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
        (self.plugin_dir / "plugin.py").write_text(PLUGIN_PY, encoding="utf-8")
        sys.path.insert(0, str(self.root))

    def tearDown(self) -> None:
        root_text = str(self.root)
        if root_text in sys.path:
            sys.path.remove(root_text)
        prefix = self.package_dir.name
        for module_name in list(sys.modules):
            if module_name == prefix or module_name.startswith(f"{prefix}."):
                sys.modules.pop(module_name, None)
        shutil.rmtree(self.root, ignore_errors=True)

    def _patched_catalog(self):
        fixture_catalog = discover_plugin_catalog(
            self.package_dir, package_name=self.package_dir.name
        )
        self.assertFalse(fixture_catalog.invalid_entries, fixture_catalog.invalid_entries)
        original = registry.get_plugin_catalog()
        return PluginCatalog(entries=(*original.entries, *fixture_catalog.entries))

    def _study(self, *, enabled_settings=None, machine_enabled=True):
        return {
            "study_id": "Study A",
            "study_settings": {
                "sensors_enabled": False,
                "sensors": {},
                "plugins": {
                    PLUGIN_KEY: {
                        "enabled": True,
                        "required": False,
                        "settings": enabled_settings or {},
                    }
                },
            },
        }

    def test_a_fixture_plugin_with_nothing_configured_blocks_on_all_three(self) -> None:
        from study_runner.backend.services.studies.study_readiness_service import check_study_readiness

        with patch.object(registry, "_PLUGIN_CATALOG", self._patched_catalog()):
            report = check_study_readiness(
                self._study(),
                {PLUGIN_KEY: {"enabled": False}},
                {},
                https_active=True,
            )

        codes = [blocker["code"] for blocker in report["blockers"]]
        self.assertIn(f"{PLUGIN_KEY}.credential_missing", codes)
        self.assertIn(f"{PLUGIN_KEY}.setting_missing", codes)
        self.assertIn(f"{PLUGIN_KEY}.machine_disabled", codes)
        self.assertFalse(report["ready"])

    def test_the_blocker_panel_is_the_plugin_key_with_no_registration(self) -> None:
        from study_runner.backend.services.studies.study_readiness_service import check_study_readiness

        with patch.object(registry, "_PLUGIN_CATALOG", self._patched_catalog()):
            report = check_study_readiness(
                self._study(), {PLUGIN_KEY: {"enabled": False}}, {}, https_active=True
            )

        panels = {blocker["code"]: blocker["panel"] for blocker in report["blockers"]}
        self.assertEqual(panels[f"{PLUGIN_KEY}.credential_missing"], PLUGIN_KEY)

    def test_fully_configured_fixture_plugin_is_ready(self) -> None:
        from study_runner.backend.services.studies.study_readiness_service import check_study_readiness

        with patch.object(registry, "_PLUGIN_CATALOG", self._patched_catalog()):
            report = check_study_readiness(
                self._study(enabled_settings={"bucket_name": "my-bucket"}),
                {PLUGIN_KEY: {"enabled": True, "access_token": "machine-token"}},
                {},
                https_active=True,
            )

        codes = [blocker["code"] for blocker in report["blockers"]]
        self.assertNotIn(f"{PLUGIN_KEY}.credential_missing", codes)
        self.assertNotIn(f"{PLUGIN_KEY}.setting_missing", codes)
        self.assertNotIn(f"{PLUGIN_KEY}.machine_disabled", codes)


if __name__ == "__main__":
    unittest.main()
