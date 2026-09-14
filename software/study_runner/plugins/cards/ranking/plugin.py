"""Card configuration and answer rules, executed in the extension worker."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from study_runner.contracts.card_options import normalize_options_question
from study_runner.contracts.card_validation_primitives import CardValidationError, require_text
from study_runner.contracts.plugin_api import Plugin

DEFAULTS = {'ranking': {'type': 'ranking',
             'prompt': '',
             'options': ['Item A', 'Item B', 'Item C']}}


def _validate_ranking_answer(*, question: dict[str, Any], answer: Any, question_number: int) -> Any:
    if not isinstance(answer, list):
        raise CardValidationError(f"Question {question_number} ranking answer must be a list.")
    normalized = [require_text(item, f"Question {question_number} ranking item") for item in answer]
    options = question.get("options", [])
    if len(normalized) == len(options) and set(normalized) == set(options):
        return normalized
    raise CardValidationError(
        f"Question {question_number} ranking must contain each configured option exactly once."
    )


def get_card_defaults(question_type: str) -> dict:
    return deepcopy(DEFAULTS[question_type])


def normalize_card_config(
    question_type: str, data: dict, index: int, host_data: dict
) -> dict:
    return normalize_options_question(data, index, question_type=question_type)


def validate_card_answer(
    question_type: str, question: dict, answer: Any, number: int
) -> Any:
    return _validate_ranking_answer(
        question=question, answer=answer, question_number=number
    )


PLUGIN = Plugin(
    key="ranking",
    label="ranking",
    category="card",
    config_key="ranking",
    can_toggle=False,
    get_card_defaults=get_card_defaults,
    normalize_card_config=normalize_card_config,
    validate_card_answer=validate_card_answer,
)
