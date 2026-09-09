"""Card configuration and answer rules, executed in the extension worker."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from study_runner.contracts.card_validation_primitives import (
    CardValidationError,
    normalize_integer,
    normalize_text,
    require_text,
)
from study_runner.contracts.plugin_api import Plugin

DEFAULTS = {'multi-slider': {'type': 'multi-slider',
                  'prompt': 'Rate how you feel on each dimension:',
                  'dimensions': [{'label': 'Valence',
                                  'min_label': 'Very negative',
                                  'max_label': 'Very positive'},
                                 {'label': 'Arousal',
                                  'min_label': 'Very calm',
                                  'max_label': 'Very excited'}]}}


def _normalize_multi_slider_question(question_data: dict[str, Any], question_index: int) -> dict[str, Any]:
    dims = question_data.get("dimensions")
    if not isinstance(dims, list) or not dims:
        raise CardValidationError(f"Question {question_index} needs at least one dimension.")
    normalized_dims = []
    for d in dims:
        if isinstance(d, dict) and d.get("label"):
            normalized_dims.append({
                "label": normalize_text(d.get("label")),
                "min_label": normalize_text(d.get("min_label")),
                "max_label": normalize_text(d.get("max_label")),
            })
    if not normalized_dims:
        raise CardValidationError(f"Question {question_index} needs at least one valid dimension.")
    return {
        "type": "multi-slider",
        "prompt": normalize_text(question_data.get("prompt")),
        "dimensions": normalized_dims,
    }


def _validate_multi_slider_answer(*, question: dict[str, Any], answer: Any, question_number: int) -> Any:
    if not isinstance(answer, dict):
        raise CardValidationError(f"Question {question_number} answer must be an object.")
    normalized: dict[str, int] = {}
    dimensions = question.get("dimensions", [])
    for dimension in dimensions:
        label = require_text(dimension.get("label"), f"Question {question_number} dimension label")
        if label not in answer:
            raise CardValidationError(f"Question {question_number} is missing a value for {label}.")
        normalized[label] = normalize_integer(
            answer.get(label),
            field_name=f"Question {question_number} answer for {label}",
            minimum=-100,
            maximum=100,
        )

    extra_keys = sorted(set(answer.keys()) - set(normalized.keys()))
    if extra_keys:
        raise CardValidationError(
            f"Question {question_number} contains unexpected dimensions: {', '.join(extra_keys)}."
        )
    return normalized


def get_card_defaults(question_type: str) -> dict:
    return deepcopy(DEFAULTS[question_type])


def normalize_card_config(
    question_type: str, data: dict, index: int, host_data: dict
) -> dict:
    return _normalize_multi_slider_question(data, index)


def validate_card_answer(
    question_type: str, question: dict, answer: Any, number: int
) -> Any:
    return _validate_multi_slider_answer(
        question=question, answer=answer, question_number=number
    )


PLUGIN = Plugin(
    key="multi_slider",
    label="multi-slider",
    category="card",
    config_key="multi_slider",
    can_toggle=False,
    get_card_defaults=get_card_defaults,
    normalize_card_config=normalize_card_config,
    validate_card_answer=validate_card_answer,
)
