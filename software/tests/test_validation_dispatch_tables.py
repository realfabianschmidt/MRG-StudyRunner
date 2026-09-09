"""Package 5g.B3: validation.py's per-card-type dispatch tables stay complete.

22 scattered `if question_type ==` branches (across `_validate_question_by_type`
and `_validate_answer_value`) became two dictionaries, `_QUESTION_NORMALIZERS`
and `_ANSWER_VALIDATORS`. Semantics are unchanged and already proven by
`test_card_type_fixtures.py` (5g.B1) passing unmodified against the new
dispatch. What is new here is checking the *tables themselves* are complete
sets, matching `ALLOWED_QUESTION_TYPES`/`NON_ANSWER_QUESTION_TYPES` exactly --
so a card type added to the registry without a table entry fails loudly
here, at import time in spirit, rather than as a confusing "unsupported
question type" `ValidationError` the first time someone actually uses it.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.runtime_core.studies.validation import (
    ALLOWED_QUESTION_TYPES,
    NON_ANSWER_QUESTION_TYPES,
    _ANSWER_VALIDATORS,
    _QUESTION_NORMALIZERS,
)


class DispatchTableCoverageTests(unittest.TestCase):
    def test_every_allowed_type_has_a_config_normalizer(self) -> None:
        self.assertEqual(set(_QUESTION_NORMALIZERS), ALLOWED_QUESTION_TYPES)

    def test_every_answerable_type_has_an_answer_validator(self) -> None:
        answerable = ALLOWED_QUESTION_TYPES - NON_ANSWER_QUESTION_TYPES
        self.assertEqual(set(_ANSWER_VALIDATORS), answerable)

    def test_non_answer_types_have_no_answer_validator(self) -> None:
        """participant-id/stimulus/finish must never be reachable here."""
        self.assertEqual(set(_ANSWER_VALIDATORS) & NON_ANSWER_QUESTION_TYPES, set())

    def test_choice_single_ranking_share_one_config_normalizer(self) -> None:
        """The one deliberate sharing in the table, pinned so it stays deliberate."""
        self.assertIs(_QUESTION_NORMALIZERS["choice"], _QUESTION_NORMALIZERS["single"])
        self.assertIs(_QUESTION_NORMALIZERS["single"], _QUESTION_NORMALIZERS["ranking"])

    def test_every_other_config_normalizer_is_its_own_function(self) -> None:
        """Catches an accidental alias that would silently merge two types."""
        shared = {"choice", "single", "ranking"}
        distinct_functions = {_QUESTION_NORMALIZERS[t] for t in _QUESTION_NORMALIZERS if t not in shared}
        self.assertEqual(len(distinct_functions), len(ALLOWED_QUESTION_TYPES) - len(shared))


if __name__ == "__main__":
    unittest.main()
