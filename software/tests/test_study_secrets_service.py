"""Per-study credentials: storage, resolution order, rename, delete, and leakage.

The load-bearing rule is that a study carries its upload *targets* but never its
*credentials* - study_settings is serialized verbatim into the exported
.study-runner file, so a key stored there would travel to whoever gets the file.
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

from study_runner.backend.services.settings.secrets_service import (
    NEXTCLOUD_PASSWORD_ENV,
    NOTION_API_KEY_ENV,
    load_local_secrets,
    redact_hardware_config,
    resolve_nextcloud_password,
    resolve_notion_api_key,
    save_local_secrets,
)
from study_runner.backend.services.studies.study_secrets_service import (
    copy_study_secrets,
    describe_secret_state,
    forget_study_secrets,
    get_study_secret,
    list_study_credential_state,
    set_study_secret,
    study_key,
)


def machine_secrets() -> dict:
    return {"notion": {"api_key": "machine-key"}, "nextcloud": {"password": "machine-pw"}}


class StudySecretStorageTests(unittest.TestCase):
    def test_set_and_get_round_trip(self) -> None:
        secrets = set_study_secret({}, "Study A", "notion", "study-key")

        self.assertEqual(get_study_secret(secrets, "Study A", "notion"), "study-key")

    def test_clearing_removes_the_entry_entirely(self) -> None:
        secrets = set_study_secret({}, "Study A", "notion", "study-key")
        secrets = set_study_secret(secrets, "Study A", "notion", "")

        self.assertEqual(get_study_secret(secrets, "Study A", "notion"), "")
        # No misleading empty shell left behind.
        self.assertNotIn("studies", secrets)

    def test_studies_do_not_see_each_others_secrets(self) -> None:
        secrets = set_study_secret({}, "Study A", "notion", "key-a")
        secrets = set_study_secret(secrets, "Study B", "notion", "key-b")

        self.assertEqual(get_study_secret(secrets, "Study A", "notion"), "key-a")
        self.assertEqual(get_study_secret(secrets, "Study B", "notion"), "key-b")

    def test_key_matches_the_filename_normalizer(self) -> None:
        # Credential key and study filename must agree, or a rename strands secrets.
        from study_runner.backend.services.studies.study_config_service import normalize_study_id

        for raw in ("My Study!", "study/../etc", "Neue Studie"):
            self.assertEqual(study_key(raw), normalize_study_id(raw))

    def test_empty_study_id_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            set_study_secret({}, "", "notion", "key")

    def test_unknown_kind_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            set_study_secret({}, "Study A", "dropbox", "key")


class ResolutionOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        cleared = patch.dict(os.environ, {NOTION_API_KEY_ENV: "", NEXTCLOUD_PASSWORD_ENV: ""}, clear=False)
        cleared.start()
        self.addCleanup(cleared.stop)

    def test_study_key_wins_over_machine_key(self) -> None:
        secrets = set_study_secret(machine_secrets(), "Study A", "notion", "study-key")

        self.assertEqual(resolve_notion_api_key({}, secrets, "Study A"), "study-key")
        self.assertEqual(resolve_notion_api_key({}, secrets, "Study B"), "machine-key")

    def test_machine_key_is_the_fallback_for_an_imported_study(self) -> None:
        # A study from another computer carries no credentials at all.
        self.assertEqual(resolve_notion_api_key({}, machine_secrets(), "Imported"), "machine-key")

    def test_env_overrides_everything(self) -> None:
        secrets = set_study_secret(machine_secrets(), "Study A", "notion", "study-key")

        with patch.dict(os.environ, {NOTION_API_KEY_ENV: "env-key"}):
            self.assertEqual(resolve_notion_api_key({}, secrets, "Study A"), "env-key")

    def test_legacy_hardware_config_still_resolves(self) -> None:
        hardware = {"notion": {"api_key": "legacy-key"}}

        self.assertEqual(resolve_notion_api_key(hardware, {}, "Study A"), "legacy-key")

    def test_nextcloud_password_follows_the_same_order(self) -> None:
        secrets = set_study_secret(machine_secrets(), "Study A", "nextcloud", "study-pw")

        self.assertEqual(resolve_nextcloud_password({}, secrets, "Study A"), "study-pw")
        self.assertEqual(resolve_nextcloud_password({}, secrets, "Study B"), "machine-pw")

    def test_no_study_context_behaves_exactly_as_before(self) -> None:
        secrets = set_study_secret(machine_secrets(), "Study A", "notion", "study-key")

        self.assertEqual(resolve_notion_api_key({}, secrets), "machine-key")


class ScopeReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        cleared = patch.dict(os.environ, {NOTION_API_KEY_ENV: "", NEXTCLOUD_PASSWORD_ENV: ""}, clear=False)
        cleared.start()
        self.addCleanup(cleared.stop)

    def test_scope_distinguishes_study_from_machine(self) -> None:
        secrets = set_study_secret(machine_secrets(), "Study A", "notion", "study-key")

        self.assertEqual(describe_secret_state("notion", {}, secrets, "Study A")["scope"], "study")
        self.assertEqual(describe_secret_state("notion", {}, secrets, "Study B")["scope"], "machine")

    def test_scope_reports_nothing_configured(self) -> None:
        state = describe_secret_state("notion", {}, {}, "Study A")

        self.assertFalse(state["configured"])
        self.assertEqual(state["scope"], "none")

    def test_scope_reports_env(self) -> None:
        with patch.dict(os.environ, {NOTION_API_KEY_ENV: "env-key"}):
            state = describe_secret_state("notion", {}, {}, "Study A", env_var=NOTION_API_KEY_ENV)

        self.assertEqual(state["scope"], "env")

    def test_credential_state_never_contains_a_value(self) -> None:
        secrets = set_study_secret(machine_secrets(), "Study A", "notion", "super-secret")

        state = list_study_credential_state({}, secrets, "Study A")

        self.assertNotIn("super-secret", json.dumps(state))
        self.assertEqual(sorted(state), ["nextcloud", "notion"])


class RenameAndDeleteTests(unittest.TestCase):
    def test_rename_copies_rather_than_moves(self) -> None:
        # save_study never deletes the old file, so the old study still exists
        # and must keep working.
        secrets = set_study_secret({}, "Old Name", "notion", "key")

        self.assertTrue(copy_study_secrets(secrets, "Old Name", "New Name"))
        self.assertEqual(get_study_secret(secrets, "New Name", "notion"), "key")
        self.assertEqual(get_study_secret(secrets, "Old Name", "notion"), "key")

    def test_rename_to_the_same_normalized_key_is_a_no_op(self) -> None:
        secrets = set_study_secret({}, "Study A", "notion", "key")

        self.assertFalse(copy_study_secrets(secrets, "Study A", "Study A"))

    def test_rename_without_stored_secrets_is_a_no_op(self) -> None:
        self.assertFalse(copy_study_secrets({}, "Old", "New"))

    def test_delete_prunes_the_entry(self) -> None:
        secrets = set_study_secret({}, "Study A", "notion", "key")

        self.assertTrue(forget_study_secrets(secrets, "Study A"))
        self.assertEqual(get_study_secret(secrets, "Study A", "notion"), "")
        self.assertNotIn("studies", secrets)

    def test_delete_of_unknown_study_is_a_no_op(self) -> None:
        self.assertFalse(forget_study_secrets({}, "Never Existed"))


class PersistenceTests(unittest.TestCase):
    def test_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "local_secrets.json"
            secrets = set_study_secret(machine_secrets(), "Study A", "notion", "study-key")

            save_local_secrets(path, secrets)
            loaded = load_local_secrets(path)

            self.assertEqual(get_study_secret(loaded, "Study A", "notion"), "study-key")

    def test_save_leaves_no_partial_file_behind(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "local_secrets.json"
            save_local_secrets(path, machine_secrets())

            leftovers = [p.name for p in Path(temp_dir).iterdir() if p.name != "local_secrets.json"]

            self.assertEqual(leftovers, [], "atomic write must not leave temp files")
            self.assertEqual(load_local_secrets(path)["notion"]["api_key"], "machine-key")


class LeakageTests(unittest.TestCase):
    def setUp(self) -> None:
        cleared = patch.dict(os.environ, {NOTION_API_KEY_ENV: "", NEXTCLOUD_PASSWORD_ENV: ""}, clear=False)
        cleared.start()
        self.addCleanup(cleared.stop)

    def test_redaction_never_returns_a_value_but_reports_scope(self) -> None:
        secrets = set_study_secret(machine_secrets(), "Study A", "notion", "study-key")
        hardware = {"notion": {"enabled": True}, "nextcloud": {}}

        redacted = redact_hardware_config(hardware, secrets, "Study A")

        self.assertNotIn("study-key", json.dumps(redacted))
        self.assertNotIn("machine-key", json.dumps(redacted))
        self.assertEqual(redacted["notion"]["api_key"], "")
        self.assertTrue(redacted["notion"]["api_key_configured"])
        self.assertEqual(redacted["notion"]["api_key_scope"], "study")

    def test_redaction_keeps_the_existing_keys_for_older_callers(self) -> None:
        redacted = redact_hardware_config({"notion": {"enabled": True}}, machine_secrets())

        for key in ("api_key", "api_key_configured", "api_key_source"):
            self.assertIn(key, redacted["notion"])

    def test_study_settings_never_carry_a_credential_field(self) -> None:
        """The exported study file must have nowhere to put a secret."""
        from study_runner.backend.services.studies.validation import _validate_study_settings

        keys = set(_validate_study_settings({}))

        for forbidden in ("api_key", "password", "notion_api_key", "nextcloud_password", "credentials"):
            self.assertNotIn(forbidden, keys)


if __name__ == "__main__":
    unittest.main()
