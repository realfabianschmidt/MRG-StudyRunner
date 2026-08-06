"""The client and the server must agree on what a study's settings are.

This shape was written out three times (admin-controller.js,
notion-settings-controller.js, and validation.py). The copies drifted, and the
drift cost data: the study-settings modal rebuilt the object without the
Nextcloud keys, so configuring Nextcloud and later toggling the progress bar
silently switched the upload off and dropped the share link.

web/scripts/lib/study-settings.js is now the single client-side definition.
These tests fail if it and _validate_study_settings ever disagree again.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.shared.validation import _validate_study_settings

STUDY_SETTINGS_JS = (
    PROJECT_ROOT / "study_runner" / "frontend" / "scripts" / "shared" / "study-settings.js"
)


def _return_object_keys(source: str, function_name: str) -> set[str]:
    """Top-level keys of the object literal a given JS function returns.

    Deliberately simple: find the function, take the `return {` block by brace
    matching, and read the keys indented exactly one level inside it. Nested
    objects sit deeper and are ignored, which is what we want here.
    """
    signature = re.search(rf"function {re.escape(function_name)}\s*\([^)]*\)\s*{{", source)
    if signature is None:
        raise AssertionError(f"{function_name}() not found in {STUDY_SETTINGS_JS.name}")

    start = source.index("return {", signature.end()) + len("return ")
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                block = source[start : index + 1]
                break
    else:
        raise AssertionError(f"unbalanced braces in {function_name}()")

    return set(re.findall(r"^    (\w+):", block, flags=re.MULTILINE))


class StudySettingsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = STUDY_SETTINGS_JS.read_text(encoding="utf-8")
        cls.backend_keys = set(_validate_study_settings({}).keys())

    def test_backend_shape_is_the_expected_fields(self) -> None:
        # Pinned on purpose: if a field is added or removed here, the JS module
        # and both locales have to be looked at in the same commit.
        self.assertEqual(
            self.backend_keys,
            {
                "sensors_enabled",
                "sensors",
                "plugins",
                "progress_bar_enabled",
            },
        )

    def test_js_defaults_match_the_backend_shape(self) -> None:
        self.assertEqual(_return_object_keys(self.source, "defaultStudySettings"), self.backend_keys)

    def test_js_normalize_matches_the_backend_shape(self) -> None:
        # This is the one that would have caught the reported bug: the old
        # normalize had no nextcloud keys, so nothing could restore them.
        self.assertEqual(_return_object_keys(self.source, "normalizeStudySettings"), self.backend_keys)

    def test_sensor_keys_are_discovered_from_plugin_capabilities(self) -> None:
        self.assertIn("pluginsWithCapability('study_sensor')", self.source)
        self.assertNotRegex(
            self.source,
            r"STUDY_SENSOR_KEYS\s*=\s*\[",
            "sensor keys belong in plugin manifests, not the web client",
        )

    def test_no_controller_redefines_the_shape(self) -> None:
        """The whole point: exactly one client-side definition, no copies."""
        scripts_dir = PROJECT_ROOT / "study_runner" / "frontend" / "scripts"
        offenders = []
        for path in scripts_dir.rglob("*.js"):
            if path == STUDY_SETTINGS_JS:
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r"function (defaultStudySettings|normalizeStudySettings)\s*\(", text):
                offenders.append(path.relative_to(PROJECT_ROOT).as_posix())
        self.assertEqual(
            offenders,
            [],
            "import from lib/study-settings.js instead of redefining the shape",
        )


class StudySettingsRoundTripTests(unittest.TestCase):
    def test_nextcloud_settings_survive_validation(self) -> None:
        settings = _validate_study_settings(
            {
                "nextcloud_enabled": True,
                "nextcloud_share_link": "https://cloud.example.com/s/AbCdEf123",
                "progress_bar_enabled": True,
            }
        )

        self.assertTrue(settings["plugins"]["nextcloud"]["enabled"])
        self.assertEqual(
            settings["plugins"]["nextcloud"]["settings"]["share_link"],
            "https://cloud.example.com/s/AbCdEf123",
        )
        self.assertNotIn("nextcloud_enabled", settings)
        self.assertNotIn("nextcloud_share_link", settings)

    def test_unknown_keys_are_dropped(self) -> None:
        settings = _validate_study_settings({"not_a_real_setting": "x", "notion_enabled": True})

        self.assertNotIn("not_a_real_setting", settings)
        self.assertTrue(settings["plugins"]["notion"]["enabled"])
        self.assertNotIn("notion_enabled", settings)

    def test_notion_data_source_id_round_trips(self) -> None:
        # Written back by the adapter at runtime, never edited by hand - but it
        # must survive a save or the next upload has to re-resolve it.
        settings = _validate_study_settings({"notion_data_source_id": "417c4c30-bdb1-4953-8088-b71251164e28"})

        self.assertEqual(
            settings["plugins"]["notion"]["settings"]["data_source_id"],
            "417c4c30-bdb1-4953-8088-b71251164e28",
        )
        self.assertNotIn("notion_data_source_id", settings)


if __name__ == "__main__":
    unittest.main()
