"""Card configuration and answer rules, executed in the extension worker."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from study_runner.contracts.card_validation_primitives import normalize_text
from study_runner.contracts.plugin_api import Plugin

DEFAULTS = {'finish': {'type': 'finish',
            'title': 'Thank you!',
            'prompt': 'Your answers have been saved.\n'
                      'You can now put the device down.'}}


def _normalize_finish_question(question_data: dict[str, Any], question_index: int) -> dict[str, Any]:
    return {
        "type": "finish",
        "title": normalize_text(question_data.get("title"), default="Thank you!"),
        "prompt": normalize_text(
            question_data.get("prompt"),
            default="Your answers have been saved.\nYou can now put the device down.",
        ),
    }


def get_card_defaults(question_type: str) -> dict:
    return deepcopy(DEFAULTS[question_type])


def normalize_card_config(
    question_type: str, data: dict, index: int, host_data: dict
) -> dict:
    return _normalize_finish_question(data, index)


PLUGIN = Plugin(
    key="finish",
    label="finish",
    category="card",
    config_key="finish",
    can_toggle=False,
    get_card_defaults=get_card_defaults,
    normalize_card_config=normalize_card_config,
    validate_card_answer=None,
)
