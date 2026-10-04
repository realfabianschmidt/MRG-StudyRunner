"""The repository ships example studies, never a real one.

`study_content/settings/study_config.json` is tracked, and it is also the
file the running app rewrites every time an operator loads or edits a study.
That combination means a lab's own study design lands in `git status` as a
modification to a tracked file, one `git commit -a` away from being published.

It has already happened: a real study replaced the shipped example in this
worktree, and the same folder previously leaked a physical BrainBit MAC
address and serial (see `test_hardware_settings_service.py`, which guards
that half).

So this test pins the shipped active study to the tracked Basic example.
Working on a real study is fine -- save it under
`study_content/studies/`, where `.gitignore` keeps every non-example preset
out of the repository, and restore the template before committing:

    git checkout -- software/study_content/settings/study_config.json
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import subprocess
import unittest


SOFTWARE_ROOT = Path(__file__).resolve().parents[1]
if str(SOFTWARE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOFTWARE_ROOT))
STUDY_CONTENT = SOFTWARE_ROOT / "study_content"
ACTIVE_STUDY = STUDY_CONTENT / "settings" / "study_config.json"
EXAMPLE_PRESETS = (
    STUDY_CONTENT / "studies" / "Example Basic Study.study-runner",
    STUDY_CONTENT / "studies" / "Example Sensors Study.study-runner",
    STUDY_CONTENT / "studies" / "Example Card Gallery Study.study-runner",
)
BASIC_PRESET, SENSORS_PRESET, CARD_GALLERY_PRESET = EXAMPLE_PRESETS


def _load(path: Path) -> dict:
    data = path.read_bytes()
    if data.startswith(b"PK"):
        from study_runner.runtime_core.studies.study_package_service import read_package

        return read_package(data)[0]
    return json.loads(data.decode("utf-8"))


class ShippedStudyContentTests(unittest.TestCase):
    def test_only_examples_are_tracked_under_studies(self) -> None:
        if not (SOFTWARE_ROOT.parent / ".git").exists():
            self.skipTest("Source archive has no Git index; archive membership is checked by release_tools.")
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", "software/study_content/studies"],
            cwd=SOFTWARE_ROOT.parent, capture_output=True, check=True,
        )
        tracked = {path.decode("utf-8") for path in result.stdout.split(b"\0") if path}
        allowed = {str(path.relative_to(SOFTWARE_ROOT.parent)).replace("\\", "/") for path in EXAMPLE_PRESETS}
        self.assertTrue(tracked == allowed, "Only the three curated example studies may be tracked.")

    def test_the_example_presets_are_present(self) -> None:
        """A renamed example would make the guard below vacuously pass."""
        missing = [str(p.relative_to(SOFTWARE_ROOT)) for p in EXAMPLE_PRESETS if not p.is_file()]

        self.assertEqual(
            missing,
            [],
            "the tracked example presets moved or were renamed; update this test "
            "in the same commit: " + ", ".join(missing),
        )

    def test_the_shipped_active_study_is_the_basic_example(self) -> None:
        active = _load(ACTIVE_STUDY)
        basic = _load(BASIC_PRESET)

        # assertTrue, not assertIn: a failing assertIn prints both operands, which
        # would dump the very study this test exists to keep private into the CI log.
        self.assertTrue(
            active == basic,
            "software/study_content/settings/study_config.json holds a study that is "
            f"not a shipped example (study_id {active.get('study_id')!r}). A real study "
            "must not be committed. Save it under software/study_content/studies/ -- "
            "gitignored -- and run: git checkout -- "
            "software/study_content/settings/study_config.json",
        )

    def test_every_example_is_already_canonical_and_reproducible(self) -> None:
        from study_runner.plugin_framework.registry import get_plugin_manifests
        from study_runner.runtime_core.studies.study_package_service import build_package
        from study_runner.runtime_core.studies.validation import validate_and_normalize_config

        supported_plugins = {
            key for key, manifest in get_plugin_manifests().items()
            if manifest.get("category") != "card"
        }
        for preset in EXAMPLE_PRESETS:
            with self.subTest(preset=preset.name):
                config = _load(preset)
                self.assertEqual(validate_and_normalize_config(config), config)
                self.assertEqual(build_package(preset.parent, config), preset.read_bytes())
                settings = config["study_settings"]
                self.assertTrue(settings["card_frame_enabled"])
                self.assertIn("cover_page", settings)
                self.assertGreater(settings["planned_session_duration_minutes"], 0)
                self.assertEqual(set(settings["plugins"]), supported_plugins)
                for question in config["questions"]:
                    if question["type"] == "mood-meter":
                        self.assertIn(question["variant"], {"classic", "blobs", "field", "orbit"})

    def test_card_gallery_covers_every_registered_question_type(self) -> None:
        from study_runner.plugin_framework.registry import get_plugin_manifests

        expected = {
            question_type
            for manifest in get_plugin_manifests().values()
            for question_type in ((manifest.get("capability_config") or {}).get("card_contract") or {}).get("question_types", [])
        }
        actual = {question["type"] for question in _load(CARD_GALLERY_PRESET)["questions"]}
        self.assertEqual(actual, expected)
        self.assertIn("choice", actual)
        self.assertIn("single", actual)
        config = _load(CARD_GALLERY_PRESET)
        self.assertTrue(all(not entry["enabled"] for entry in config["study_settings"]["plugins"].values()))
        stimulus = next(question for question in config["questions"] if question["type"] == "stimulus")
        # A sensor-free gallery drives no actuator.
        self.assertEqual(stimulus["actuator_plugins"], [])

    def test_sensor_example_covers_the_active_sensor_catalog_but_starts_disabled(self) -> None:
        from study_runner.plugin_framework.registry import get_plugin_manifests

        expected = {
            key
            for key, manifest in get_plugin_manifests().items()
            if "study_sensor" in set(manifest.get("capabilities") or [])
        }
        settings = _load(SENSORS_PRESET)["study_settings"]
        self.assertEqual(set(settings["sensors"]), expected)
        self.assertTrue(all(enabled is False for enabled in settings["sensors"].values()))
        self.assertTrue(all(settings["plugins"][key]["enabled"] is False for key in expected))
        self.assertFalse(settings["sensors_enabled"])


if __name__ == "__main__":
    unittest.main()
