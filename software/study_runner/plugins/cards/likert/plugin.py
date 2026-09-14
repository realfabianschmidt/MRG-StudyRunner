"""Card configuration and answer rules, executed in the extension worker."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from study_runner.contracts.card_validation_primitives import normalize_integer, normalize_text
from study_runner.contracts.plugin_api import Plugin

DEFAULTS = {'likert': {'type': 'likert',
            'prompt': '',
            'scale': 7,
            'label_min': 'not at all',
            'label_max': 'very strongly'}}


def _normalize_likert_question(question_data: dict[str, Any], question_index: int) -> dict[str, Any]:
    return {
        "type": "likert",
        "prompt": normalize_text(question_data.get("prompt")),
        "scale": normalize_integer(
            question_data.get("scale", 7),
            field_name=f"Question {question_index} scale",
            minimum=3,
            maximum=11,
        ),
        "label_min": normalize_text(question_data.get("label_min")),
        "label_max": normalize_text(question_data.get("label_max")),
    }


def _validate_likert_answer(*, question: dict[str, Any], answer: Any, question_number: int) -> Any:
    return normalize_integer(
        answer,
        field_name=f"Question {question_number} answer",
        minimum=1,
        maximum=int(question.get("scale", 7)),
    )


def get_card_defaults(question_type: str) -> dict:
    return deepcopy(DEFAULTS[question_type])


def normalize_card_config(
    question_type: str, data: dict, index: int, host_data: dict
) -> dict:
    return _normalize_likert_question(data, index)


def validate_card_answer(
    question_type: str, question: dict, answer: Any, number: int
) -> Any:
    return _validate_likert_answer(
        question=question, answer=answer, question_number=number
    )


PLUGIN = Plugin(
    key="likert",
    label="likert",
    category="card",
    config_key="likert",
    can_toggle=False,
    get_card_defaults=get_card_defaults,
    normalize_card_config=normalize_card_config,
    validate_card_answer=validate_card_answer,
)
