"""Card configuration and answer rules, executed in the extension worker."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from study_runner.contracts.card_validation_primitives import (
    CardValidationError,
    normalize_boolean,
    normalize_text,
    normalize_text_list,
    require_text,
)
from study_runner.contracts.plugin_api import Plugin

DEFAULTS = {'word-cloud': {'type': 'word-cloud',
                'prompt': 'Which words describe how you feel right now?',
                'words': ['Happy',
                          'Sad',
                          'Excited',
                          'Calm',
                          'Anxious',
                          'Tired',
                          'Focused',
                          'Restless'],
                'allow_multiple': True}}


def _normalize_word_cloud_question(question_data: dict[str, Any], question_index: int) -> dict[str, Any]:
    words = normalize_text_list(question_data.get("words"))
    if not words:
        raise CardValidationError(f"Question {question_index} needs at least one word.")
    return {
        "type": "word-cloud",
        "prompt": normalize_text(question_data.get("prompt")),
        "words": words,
        "allow_multiple": normalize_boolean(question_data.get("allow_multiple", True)),
    }


def _validate_word_cloud_answer(*, question: dict[str, Any], answer: Any, question_number: int) -> Any:
    if not isinstance(answer, list):
        raise CardValidationError(f"Question {question_number} answer must be a list.")
    normalized = [require_text(item, f"Question {question_number} word") for item in answer]
    if not normalized:
        raise CardValidationError(f"Question {question_number} needs at least one selected word.")
    if len(set(normalized)) != len(normalized):
        raise CardValidationError(f"Question {question_number} contains duplicate words.")
    if question.get("allow_multiple") is False and len(normalized) != 1:
        raise CardValidationError(f"Question {question_number} allows exactly one selected word.")
    invalid = [item for item in normalized if item not in question.get("words", [])]
    if invalid:
        raise CardValidationError(
            f"Question {question_number} contains invalid words: {', '.join(invalid)}."
        )
    return normalized


def get_card_defaults(question_type: str) -> dict:
    return deepcopy(DEFAULTS[question_type])


def normalize_card_config(
    question_type: str, data: dict, index: int, host_data: dict
) -> dict:
    return _normalize_word_cloud_question(data, index)


def validate_card_answer(
    question_type: str, question: dict, answer: Any, number: int
) -> Any:
    return _validate_word_cloud_answer(
        question=question, answer=answer, question_number=number
    )


PLUGIN = Plugin(
    key="word_cloud",
    label="word-cloud",
    category="card",
    config_key="word_cloud",
    can_toggle=False,
    get_card_defaults=get_card_defaults,
    normalize_card_config=normalize_card_config,
    validate_card_answer=validate_card_answer,
)
