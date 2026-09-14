"""Card configuration and answer rules, executed in the extension worker."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from study_runner.contracts.card_validation_primitives import normalize_text, require_text
from study_runner.contracts.plugin_api import Plugin

DEFAULTS = {'text': {'type': 'text', 'prompt': ''}}


def _normalize_text_question(question_data: dict[str, Any], question_index: int) -> dict[str, Any]:
    return {
        "type": "text",
        "prompt": normalize_text(question_data.get("prompt")),
    }


def _validate_text_answer(*, question: dict[str, Any], answer: Any, question_number: int) -> Any:
    return require_text(answer, f"Question {question_number} answer")


def get_card_defaults(question_type: str) -> dict:
    return deepcopy(DEFAULTS[question_type])


def normalize_card_config(
    question_type: str, data: dict, index: int, host_data: dict
) -> dict:
    return _normalize_text_question(data, index)


def validate_card_answer(
    question_type: str, question: dict, answer: Any, number: int
) -> Any:
    return _validate_text_answer(
        question=question, answer=answer, question_number=number
    )


PLUGIN = Plugin(
    key="text",
    label="text",
    category="card",
    config_key="text",
    can_toggle=False,
    get_card_defaults=get_card_defaults,
    normalize_card_config=normalize_card_config,
    validate_card_answer=validate_card_answer,
)
