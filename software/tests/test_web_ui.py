"""Guardrail tests for the web UI conventions.

These keep three owner rules enforced without a JS test runner:
- only slide toggles (.switch), no plain square checkboxes,
- no untranslated research jargon on the participant page,
- the UI must work offline (no CDN links) and never block with alert().
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

WEB = PROJECT_ROOT / "study_runner" / "web"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class LocaleTests(unittest.TestCase):
    def test_locale_key_sets_are_identical(self) -> None:
        en = json.loads(_read(WEB / "locales" / "en.json"))
        de = json.loads(_read(WEB / "locales" / "de.json"))

        self.assertEqual(
            sorted(en.keys()),
            sorted(de.keys()),
            "en.json and de.json must contain exactly the same translation keys",
        )


class ToggleTests(unittest.TestCase):
    def test_no_legacy_checkbox_row_classes_remain(self) -> None:
        offenders = []
        for path in list(WEB.rglob("*.html")) + list((WEB / "scripts").rglob("*.js")):
            text = _read(path)
            if "consent-row" in text or "checkbox-row" in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [], "convert remaining rows to the .switch pattern")

    def test_every_visible_checkbox_is_a_switch_or_chip(self) -> None:
        # A checkbox is acceptable when it is the hidden input of a .switch
        # or a chips option (hidden by CSS, rendered as pills).
        pattern = re.compile(r'[^\n]*type="checkbox"[^\n]*')
        offenders = []
        for path in list((WEB / "pages").glob("*.html")) + list((WEB / "scripts").rglob("*.js")):
            for match in pattern.findall(_read(path)):
                context = match.strip()
                if "switch" in context or "chips" in context or "querySelector" in context or ".matches(" in context:
                    continue
                if "stimulus-toggle-input" in context or "pid-enabled" in context:
                    continue  # inputs inside existing .switch wrappers (multi-line markup)
                if 'name="q${i}"' in context:
                    continue  # chips option inputs of the choice card
                offenders.append(f"{path.name}: {context[:100]}")
        self.assertEqual(offenders, [])


class ParticipantLanguageTests(unittest.TestCase):
    JARGON = [
        "Visual analog scale</div>",
        "Likert scale</div>",
        "Semantic differential</div>",
        "Word Cloud</div>",
        "Free text</div>",
        "> Ranking</div>",
        "Multi-Slider</div>",
        "Mood Meter</div>",
    ]

    def test_participant_card_tags_are_localized(self) -> None:
        offenders = []
        for path in (WEB / "scripts" / "cards").glob("*.js"):
            text = _read(path)
            for phrase in self.JARGON:
                if phrase in text:
                    offenders.append(f"{path.name}: {phrase}")
        self.assertEqual(offenders, [])

    def test_study_page_never_uses_blocking_alerts(self) -> None:
        text = _read(WEB / "scripts" / "study-controller.js")
        self.assertNotIn("alert(", text, "use showStudyNotice() instead of alert()")


class OfflineTests(unittest.TestCase):
    def test_pages_do_not_load_from_cdns(self) -> None:
        offenders = []
        for path in (WEB / "pages").glob("*.html"):
            text = _read(path)
            if "cdn." in text or "https://unpkg" in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [], "vendored assets only - the lab network has no internet")


if __name__ == "__main__":
    unittest.main()
