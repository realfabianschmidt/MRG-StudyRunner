"""Affect Map keeps its configuration and answers separate from Mood Meter."""
from __future__ import annotations

import unittest

from study_runner.contracts.card_validation_primitives import CardValidationError
from study_runner.plugin_framework.registry import get_plugin_manifests
from study_runner.plugins.cards.affect_map import plugin


def question(**overrides):
    return plugin.normalize_card_config("affect-map", {"type": "affect-map", **overrides}, 0, {})


class AffectMapTests(unittest.TestCase):
    def test_registered_with_only_two_views(self):
        manifest = get_plugin_manifests()["affect_map"]
        self.assertEqual(manifest["capability_config"]["card_contract"]["question_types"], ["affect-map"])
        self.assertEqual(plugin.get_card_defaults("affect-map")["variant"], "field")
        self.assertEqual(question(variant="orbit")["variant"], "orbit")
        for legacy in ("classic", "blobs", "other"):
            self.assertEqual(question(variant=legacy)["variant"], "field")

    def test_colors_normalize_without_changing_answer(self):
        normalized = question(region_colors={"red": "#12ab34", "blue": "red", "extra": "#123456"}, colors_enabled=False)
        self.assertFalse(normalized["colors_enabled"])
        self.assertEqual(normalized["region_colors"]["red"], "#12AB34")
        self.assertEqual(normalized["region_colors"]["blue"], plugin.DEFAULT_COLORS["blue"])
        self.assertEqual(set(normalized["region_colors"]), set(plugin.DEFAULT_COLORS))
        answer = {"words": ["Calm"], "pleasantness": .7, "energy": .3}
        self.assertEqual(plugin.validate_card_answer("affect-map", normalized, answer, 1), answer)

    def test_orbit_adds_intensity_and_requires_it(self):
        orbit = question(variant="orbit")
        answer = {"words": ["Calm"], "pleasantness": .7, "energy": .3, "intensity": .5}
        self.assertEqual(plugin.validate_card_answer("affect-map", orbit, answer, 1), answer)
        with self.assertRaises(CardValidationError):
            plugin.validate_card_answer("affect-map", orbit, {"words": ["Calm"], "pleasantness": .7, "energy": .3}, 1)


if __name__ == "__main__":
    unittest.main()
