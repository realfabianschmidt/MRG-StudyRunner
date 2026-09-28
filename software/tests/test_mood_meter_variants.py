"""The Mood Meter's four views: config normalization and the answer each records.

classic and blobs record the chosen words; field (Affect Grid) and orbit
(circumplex / Geneva Emotion Wheel) also record the position on the plane.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid

from study_runner.contracts.card_validation_primitives import CardValidationError
from study_runner.plugin_framework.plugin_catalog import discover_plugin_catalog
from study_runner.plugins.cards.mood_meter import plugin


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CARD_TEMPLATE = PROJECT_ROOT.parent / "tools" / "plugin_templates" / "cards"


def _question(**overrides):
    return plugin.normalize_card_config("mood-meter", {"type": "mood-meter", **overrides}, 0, {})


def _validate(question, answer):
    return plugin.validate_card_answer("mood-meter", question, answer, 3)


class MoodMeterVariantConfigTests(unittest.TestCase):
    def test_default_view_is_classic(self) -> None:
        self.assertEqual(plugin.get_card_defaults("mood-meter")["variant"], "classic")
        self.assertEqual(_question()["variant"], "classic")

    def test_every_view_is_kept_and_unknown_views_fall_back_to_classic(self) -> None:
        for variant in ("classic", "blobs", "field", "orbit"):
            self.assertEqual(_question(variant=variant)["variant"], variant)
        self.assertEqual(_question(variant="hologram")["variant"], "classic")
        self.assertEqual(_question(variant=" FIELD ")["variant"], "field")


class MoodMeterVariantAnswerTests(unittest.TestCase):
    def test_word_views_record_a_list_as_before(self) -> None:
        for variant in ("classic", "blobs"):
            with self.subTest(variant=variant):
                self.assertEqual(_validate(_question(variant=variant), ["Calm", "Content"]), ["Calm", "Content"])
                with self.assertRaises(CardValidationError):
                    _validate(_question(variant=variant), {"words": ["Calm"], "pleasantness": 0.5, "energy": 0.5})

    def test_field_records_words_and_position_rounded(self) -> None:
        answer = _validate(
            _question(variant="field"),
            {"words": ["Calm"], "pleasantness": 0.71234, "energy": 0.2},
        )
        self.assertEqual(answer, {"words": ["Calm"], "pleasantness": 0.712, "energy": 0.2})

    def test_orbit_also_records_intensity(self) -> None:
        answer = _validate(
            _question(variant="orbit"),
            {"words": ["Tense"], "pleasantness": 0.1, "energy": 0.9, "intensity": 0.95},
        )
        self.assertEqual(answer["intensity"], 0.95)
        with self.assertRaises(CardValidationError):
            _validate(_question(variant="orbit"), {"words": ["Tense"], "pleasantness": 0.1, "energy": 0.9})

    def test_position_must_be_numbers_between_zero_and_one(self) -> None:
        question = _question(variant="field")
        for bad in ({"pleasantness": 1.2, "energy": 0.5}, {"pleasantness": "0.5", "energy": 0.5},
                    {"pleasantness": True, "energy": 0.5}, {"energy": 0.5}):
            with self.subTest(bad=bad), self.assertRaises(CardValidationError):
                _validate(question, {"words": ["Calm"], **bad})

    def test_word_rules_apply_to_every_view(self) -> None:
        single = _question(variant="field", allow_multiple=False)
        with self.assertRaises(CardValidationError):
            _validate(single, {"words": ["Calm", "Content"], "pleasantness": 0.5, "energy": 0.5})
        with self.assertRaises(CardValidationError):
            _validate(_question(variant="orbit"), {"words": [], "pleasantness": 0.5, "energy": 0.5, "intensity": 0})


class MultiFileCardIsolationTests(unittest.TestCase):
    def test_discovery_refuses_state_in_any_declared_card_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "example_card"
            shutil.copytree(CARD_TEMPLATE, bundle, ignore=shutil.ignore_patterns("__pycache__"))
            (bundle / "helper.js").write_text("let remembered = null;\nexport const x = 1;\n", encoding="utf-8")
            manifest_path = bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["ui"]["assets"] = [*manifest["ui"]["assets"], "helper.js"]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            catalog = discover_plugin_catalog(Path(tmp), package_name=f"card_multi_{uuid.uuid4().hex}")
            entry = next(e for e in catalog.entries if e.directory == "example_card")
            self.assertEqual(entry.status, "invalid")
            self.assertIn("helper.js", " ".join(entry.errors))


@unittest.skipUnless(shutil.which("node"), "Node.js is only required for the mood meter view JS test")
class MoodMeterViewJavaScriptTests(unittest.TestCase):
    def test_word_places_and_answers_per_view(self) -> None:
        subprocess.run(
            [shutil.which("node"), str(PROJECT_ROOT / "tests" / "js" / "mood-meter-views.test.mjs")],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
