"""Info card: text (and optionally an image) for the participant to read.

It asks for nothing, so it is answerless; the participant simply continues.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from study_runner.contracts.card_validation_primitives import CardValidationError
from study_runner.contracts.media_content import MediaContentError, normalize_media_content
from study_runner.contracts.plugin_api import Plugin

DEFAULTS = {'info': {'type': 'info',
            'title': 'Information',
            'text': 'Please read the following information carefully.',
            'image_asset': '',
            'image_alt': '',
            'layout': 'text'}}


def get_card_defaults(question_type: str) -> dict:
    return deepcopy(DEFAULTS[question_type])


def normalize_card_config(
    question_type: str, data: dict, index: int, host_data: dict
) -> dict:
    try:
        content = normalize_media_content(data, label=f"Question {index}")
    except MediaContentError as error:
        raise CardValidationError(str(error)) from error
    return {"type": "info", **content}


PLUGIN = Plugin(
    key="info",
    label="info",
    category="card",
    config_key="info",
    can_toggle=False,
    get_card_defaults=get_card_defaults,
    normalize_card_config=normalize_card_config,
    validate_card_answer=None,
)
