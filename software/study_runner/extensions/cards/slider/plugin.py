"""Card configuration and answer rules, executed in the extension worker."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from study_runner.contracts.card_validation_primitives import normalize_integer, normalize_text
from study_runner.contracts.plugin_api import Plugin

DEFAULTS = {'slider': {'type': 'slider',
            'prompt': '',
            'label_min': 'not at all',
            'label_max': 'very strongly'}}


def _normalize_slider_question(question_data: dict[str, Any], question_index: int) -> dict[str, Any]:
    return {
        "type": "slider",
        "prompt": normalize_text(question_data.get("prompt")),
        "label_min": normalize_text(question_data.get("label_min")),
        "label_max": normalize_text(question_data.get("label_max")),
    }


def _validate_slider_answer(*, question: dict[str, Any], answer: Any, question_number: int) -> Any:
    return normalize_integer(
        answer,
        field_name=f"Question {question_number} answer",
        minimum=0,
        maximum=100,
    )


def get_card_defaults(question_type: str) -> dict:
    return deepcopy(DEFAULTS[question_type])


def normalize_card_config(
    question_type: str, data: dict, index: int, host_data: dict
) -> dict:
    return _normalize_slider_question(data, index)


def validate_card_answer(
    question_type: str, question: dict, answer: Any, number: int
) -> Any:
    return _validate_slider_answer(
        question=question, answer=answer, question_number=number
    )


PLUGIN = Plugin(
    key="slider",
    label="slider",
    category="card",
    config_key="slider",
    can_toggle=False,
    get_card_defaults=get_card_defaults,
    normalize_card_config=normalize_card_config,
    validate_card_answer=validate_card_answer,
)
