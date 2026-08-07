"""A third destination needs a secret too -- can it get one without a core edit?

Before this, "which plugins have a secret at all" was a hardcoded map in three
different files (`SECRET_ENV_VARS`, `SECRET_FIELDS`, `_CREDENTIAL_ENV_VARS`),
so a plugin like S3 or a lab file share could not declare its own API key; a
core file had to be edited to add it. This test proves that gap is closed: a
fixture plugin that exists only inside this test, declaring nothing but a
`credentials` capability in its manifest, is picked up by every function that
resolves, describes, or redacts a secret -- the same functions Notion and
Nextcloud use, unmodified.

Uses the same temp-package + registry-patching pattern as
`test_fixture_plugin_blueprint.py`, so a fixture plugin is discovered exactly
the way a real one would be, through `discover_plugin_catalog`.
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
    },
    "streams": [],
    "settings": {"machine": {}, "study": {}, "card_actions": {}},
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


class FixtureDestinationCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = PROJECT_ROOT / ".tmp" / "fixture-plugin-credentials"
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

    def test_secret_fields_picks_up_the_fixture_plugin_unmodified(self) -> None:
        from study_runner.backend.services.studies.study_secrets_service import secret_fields

        with patch.object(registry, "_PLUGIN_CATALOG", self._patched_catalog()):
            fields = secret_fields()

        self.assertEqual(fields[PLUGIN_KEY], "access_token")
        # The two real destinations are still there too -- this is additive.
        self.assertEqual(fields["notion"], "api_key")
        self.assertEqual(fields["nextcloud"], "password")

    def test_resolve_plugin_secret_finds_it_at_every_scope(self) -> None:
        from study_runner.backend.services.studies.study_secrets_service import resolve_plugin_secret

        with patch.object(registry, "_PLUGIN_CATALOG", self._patched_catalog()):
            legacy = resolve_plugin_secret(
                PLUGIN_KEY, {PLUGIN_KEY: {"access_token": "legacy-token"}}, {}
            )
            machine = resolve_plugin_secret(
                PLUGIN_KEY, {}, {PLUGIN_KEY: {"access_token": "machine-token"}}
            )

        self.assertEqual(legacy, "legacy-token")
        self.assertEqual(machine, "machine-token")

    def test_env_var_declared_in_the_manifest_overrides_everything(self) -> None:
        import os

        from study_runner.backend.services.studies.study_secrets_service import resolve_plugin_secret

        with (
            patch.object(registry, "_PLUGIN_CATALOG", self._patched_catalog()),
            patch.dict(os.environ, {"STUDY_RUNNER_FIXTURE_VAULT_TOKEN": "env-token"}),
        ):
            resolved = resolve_plugin_secret(
                PLUGIN_KEY, {PLUGIN_KEY: {"access_token": "legacy-token"}}, {}
            )

        self.assertEqual(resolved, "env-token")

    def test_describe_secret_state_reports_scope_without_the_value(self) -> None:
        from study_runner.backend.services.studies.study_secrets_service import describe_secret_state

        with patch.object(registry, "_PLUGIN_CATALOG", self._patched_catalog()):
            state = describe_secret_state(
                PLUGIN_KEY, {}, {PLUGIN_KEY: {"access_token": "machine-token"}}
            )

        self.assertEqual(state, {"configured": True, "scope": "machine", "source": "local_file"})

    def test_redact_hardware_config_blanks_it_and_reports_why(self) -> None:
        from study_runner.backend.services.settings.secrets_service import redact_hardware_config

        hardware_config = {PLUGIN_KEY: {"access_token": "machine-token", "enabled": True}}

        with patch.object(registry, "_PLUGIN_CATALOG", self._patched_catalog()):
            redacted = redact_hardware_config(hardware_config, {})

        self.assertEqual(redacted[PLUGIN_KEY]["access_token"], "")
        self.assertTrue(redacted[PLUGIN_KEY]["access_token_configured"])
        self.assertEqual(redacted[PLUGIN_KEY]["access_token_scope"], "machine")
        self.assertNotIn("machine-token", json.dumps(redacted))


if __name__ == "__main__":
    unittest.main()
