"""Card configuration and answer rules, executed in the extension worker."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from study_runner.contracts.card_options import normalize_options_question
from study_runner.contracts.card_validation_primitives import CardValidationError, require_text
from study_runner.contracts.plugin_api import Plugin

DEFAULTS = {'choice': {'type': 'choice',
            'prompt': '',
            'options': ['Option A', 'Option B', 'Option C']},
 'single': {'type': 'single',
            'prompt': '',
            'options': ['Option A', 'Option B', 'Option C']}}


def _validate_choice_answer(*, question: dict[str, Any], answer: Any, question_number: int) -> Any:
    if not isinstance(answer, list):
        raise CardValidationError(f"Question {question_number} answer must be a list.")
    options = question.get("options", [])
    normalized = [require_text(item, f"Question {question_number} answer") for item in answer]
    if not normalized:
        raise CardValidationError(f"Question {question_number} needs at least one selected option.")
    if len(set(normalized)) != len(normalized):
        raise CardValidationError(f"Question {question_number} contains duplicate selected options.")
    invalid = [item for item in normalized if item not in options]
    if invalid:
        raise CardValidationError(
            f"Question {question_number} contains invalid options: {', '.join(invalid)}."
        )
    return normalized


def _validate_single_answer(*, question: dict[str, Any], answer: Any, question_number: int) -> Any:
    selected = require_text(answer, f"Question {question_number} answer")
    if selected not in question.get("options", []):
        raise CardValidationError(f"Question {question_number} answer is not a valid option.")
    return selected


def get_card_defaults(question_type: str) -> dict:
    return deepcopy(DEFAULTS[question_type])


def validate_card_answer(question_type: str, question: dict, answer: Any, number: int) -> Any:
    validator = {"choice": _validate_choice_answer, "single": _validate_single_answer}[question_type]
    return validator(question=question, answer=answer, question_number=number)


def normalize_card_config(
    question_type: str, data: dict, index: int, host_data: dict
) -> dict:
    return normalize_options_question(data, index, question_type=question_type)


PLUGIN = Plugin(
    key="choice",
    label="choice",
    category="card",
    config_key="choice",
    can_toggle=False,
    get_card_defaults=get_card_defaults,
    normalize_card_config=normalize_card_config,
    validate_card_answer=validate_card_answer,
)
