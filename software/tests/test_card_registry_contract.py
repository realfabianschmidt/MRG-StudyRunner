"""Package 5g.B4: the card registry is a checkable contract, not a claim.

Ties together what 5g.B1-B3 built separately: the JS registry
(`cards/index.js`), `validation.py`'s two dispatch tables, and the golden
fixtures. Before this, "the registry is a complete abstraction" was true
only because every type happened to have been added carefully by hand --
nothing would have failed if a new type were registered in one place and
forgotten in another. This is that missing check.

No JS test runner is asked to cooperate with Python here: JS source is read
as text and matched with plain regexes, the same established pattern
`test_web_ui.py` already uses for cross-language guardrails (see its
`WEB`/`_read` helpers, which this module mirrors). A JS parser would be
more precise; it would also be the first JS dependency this project has
ever needed for its tests, for a check simple regexes already answer.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_runner.runtime_core.studies.validation import (
    ALLOWED_QUESTION_TYPES,
    NON_ANSWER_QUESTION_TYPES,
    _ANSWER_VALIDATORS,
    _QUESTION_NORMALIZERS,
)
from support.card_type_fixtures import CARD_TYPE_FIXTURES

WEB = PROJECT_ROOT / "study_runner" / "apps" / "ui"
CARDS_DIR = WEB / "scripts" / "cards"
CARDS_INDEX = CARDS_DIR / "index.js"

# The shared interface every card module implements (developer-guide.md's
# "Adding a new card type" section names the same six exports).
REQUIRED_EXPORTS = ("meta", "defaultQuestion", "renderStudy", "renderEditor", "collectConfig", "collectAnswer")

# type -> its module file, for every type with its own module. choice.js
# alone serves two types (choice, single); every other type has one file.
TYPE_TO_MODULE_FILE = {
    "participant-id": "card-participant-id.js",
    "stimulus": "card-stimulus.js",
    "likert": "card-likert.js",
    "semantic": "card-semantic.js",
    "choice": "card-choice.js",
    "single": "card-choice.js",
    "slider": "card-slider.js",
    "multi-slider": "card-multi-slider.js",
    "ranking": "card-ranking.js",
    "text": "card-text.js",
    "word-cloud": "card-word-cloud.js",
    "mood-meter": "card-mood-meter.js",
    "finish": "card-finish.js",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _registered_card_types() -> set[str]:
    """The type strings in `CARD_TYPES`, read as text rather than executed."""
    source = _read(CARDS_INDEX)
    match = re.search(r"export const CARD_TYPES = \[(.*?)\n\];", source, re.DOTALL)
    if not match:
        raise AssertionError("Could not find CARD_TYPES in cards/index.js -- did it get renamed?")
    return set(re.findall(r"type:\s*'([a-z-]+)'", match.group(1)))


class CardTypesRegistrySyncTests(unittest.TestCase):
    """The one canonical list of card types, agreed on both sides.

    `ALLOWED_QUESTION_TYPES` (Python) and `CARD_TYPES` (JS) are
    independently maintained lists of the same thirteen names. Nothing
    forces them to agree except a human remembering to update both -- this
    is what turns "remembering" into "checked".
    """

    def test_js_and_python_agree_on_which_types_exist(self) -> None:
        self.assertEqual(_registered_card_types(), ALLOWED_QUESTION_TYPES)

    def test_every_registered_type_is_mapped_to_a_module_file(self) -> None:
        """Catches this test file itself going stale before it catches anything else."""
        self.assertEqual(set(TYPE_TO_MODULE_FILE), ALLOWED_QUESTION_TYPES)


class CardModuleExportContractTests(unittest.TestCase):
    """Every registered type's module implements the shared interface."""

    def test_every_module_exports_the_required_interface(self) -> None:
        missing: dict[str, list[str]] = {}
        for card_type, filename in TYPE_TO_MODULE_FILE.items():
            source = _read(CARDS_DIR / filename)
            gaps = [
                name
                for name in REQUIRED_EXPORTS
                if not re.search(rf"export (?:const|function) {name}\b", source)
            ]
            if gaps:
                missing[card_type] = gaps
        self.assertEqual(missing, {}, f"card modules missing required exports: {missing}")


class ValidationDispatchSyncTests(unittest.TestCase):
    """validation.py's two tables, checked against the registry itself.

    test_validation_dispatch_tables.py (5g.B3) already checks these tables
    against ALLOWED_QUESTION_TYPES; this repeats the check against the JS
    registry directly, so a drift between validation.py and
    ALLOWED_QUESTION_TYPES itself would not slip through both files.
    """

    def test_every_registered_type_has_a_config_normalizer(self) -> None:
        self.assertEqual(_registered_card_types(), set(_QUESTION_NORMALIZERS))

    def test_every_answerable_registered_type_has_an_answer_validator(self) -> None:
        answerable = _registered_card_types() - NON_ANSWER_QUESTION_TYPES
        self.assertEqual(answerable, set(_ANSWER_VALIDATORS))


class GoldenFixtureSyncTests(unittest.TestCase):
    """Every registered type has the 5g.B1 safety net covering it."""

    def test_every_registered_type_has_a_golden_fixture(self) -> None:
        self.assertEqual(_registered_card_types(), set(CARD_TYPE_FIXTURES))


if __name__ == "__main__":
    unittest.main()
