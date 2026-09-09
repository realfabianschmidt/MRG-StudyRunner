"""Card configuration and answer rules, executed in the extension worker."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from study_runner.contracts.card_validation_primitives import (
    CardValidationError,
    normalize_integer,
    normalize_text,
)
from study_runner.contracts.plugin_api import Plugin

DEFAULTS = {'semantic': {'type': 'semantic',
              'prompt': '',
              'pairs': [['alive', 'mechanical'], ['familiar', 'unfamiliar']]}}


def _normalize_pairs(value: Any, question_index: int) -> list[list[str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CardValidationError(f"Question {question_index} word pairs must be a list.")

    pairs: list[list[str]] = []
    for pair in value:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise CardValidationError(
                f"Question {question_index} has an invalid word pair. Use exactly two entries."
            )
        left = normalize_text(pair[0])
        right = normalize_text(pair[1])
        if not left or not right:
            raise CardValidationError(
                f"Question {question_index} has an empty word in one of its pairs."
            )
        pairs.append([left, right])
    return pairs


def _normalize_semantic_question(question_data: dict[str, Any], question_index: int) -> dict[str, Any]:
    pairs = _normalize_pairs(question_data.get("pairs"), question_index)
    if not pairs:
        raise CardValidationError(f"Question {question_index} needs at least one valid word pair.")
    return {
        "type": "semantic",
        "prompt": normalize_text(question_data.get("prompt")),
        "pairs": pairs,
    }


def _validate_semantic_answer(*, question: dict[str, Any], answer: Any, question_number: int) -> Any:
    expected_pairs = question.get("pairs", [])
    if not isinstance(answer, dict):
        raise CardValidationError(f"Question {question_number} answer must be an object.")

    normalized: dict[str, int] = {}
    for pair in expected_pairs:
        pair_key = f"{pair[0]}_{pair[1]}"
        if pair_key not in answer:
            raise CardValidationError(f"Question {question_number} is missing a rating for {pair_key}.")
        normalized[pair_key] = normalize_integer(
            answer.get(pair_key),
            field_name=f"Question {question_number} answer for {pair_key}",
            minimum=1,
            maximum=7,
        )

    extra_keys = sorted(set(answer.keys()) - set(normalized.keys()))
    if extra_keys:
        raise CardValidationError(
            f"Question {question_number} contains unexpected semantic keys: {', '.join(extra_keys)}."
        )
    return normalized


def get_card_defaults(question_type: str) -> dict:
    return deepcopy(DEFAULTS[question_type])


def normalize_card_config(
    question_type: str, data: dict, index: int, host_data: dict
) -> dict:
    return _normalize_semantic_question(data, index)


def validate_card_answer(
    question_type: str, question: dict, answer: Any, number: int
) -> Any:
    return _validate_semantic_answer(
        question=question, answer=answer, question_number=number
    )


PLUGIN = Plugin(
    key="semantic",
    label="semantic",
    category="card",
    config_key="semantic",
    can_toggle=False,
    get_card_defaults=get_card_defaults,
    normalize_card_config=normalize_card_config,
    validate_card_answer=validate_card_answer,
)
