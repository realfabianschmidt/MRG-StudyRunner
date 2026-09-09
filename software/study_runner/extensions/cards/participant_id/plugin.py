"""Card configuration and answer rules, executed in the extension worker."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from study_runner.contracts.card_validation_primitives import (
    CardValidationError,
    normalize_boolean,
    normalize_text,
)
from study_runner.contracts.participant_fields import (
    CONFIGURABLE_OPTION_DEFAULTS,
    PARTICIPANT_FIELD_DEFAULTS,
    PARTICIPANT_FIELD_ORDER,
)
from study_runner.contracts.plugin_api import Plugin

DEFAULTS = {'participant-id': {'type': 'participant-id',
                    'prompt': 'Please enter your data for anonymous identification.',
                    'code_label': 'Your anonymous code',
                    'info_top': 'Your input is transformed locally into a one-way '
                                'SHA-256 hash. Only fields marked for storage are '
                                'saved.'}}
DEFAULTS["participant-id"]["fields"] = deepcopy(PARTICIPANT_FIELD_DEFAULTS)
for _field, _options in CONFIGURABLE_OPTION_DEFAULTS.items():
    DEFAULTS["participant-id"]["fields"][_field]["options"] = list(_options)


def _validate_participant_fields(value: Any, question_index: int) -> dict[str, dict[str, bool]]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise CardValidationError(f"Question {question_index} participant fields must be an object.")

    extra_keys = sorted(set(value.keys()) - set(PARTICIPANT_FIELD_ORDER))
    if extra_keys:
        raise CardValidationError(
            f"Question {question_index} participant fields contain unsupported entries: "
            + ", ".join(extra_keys)
            + "."
        )

    normalized: dict[str, dict[str, bool]] = {}
    for field_key in PARTICIPANT_FIELD_ORDER:
        defaults = PARTICIPANT_FIELD_DEFAULTS[field_key]
        raw_field = value.get(field_key, {})
        if raw_field is None:
            raw_field = {}
        if not isinstance(raw_field, dict):
            raise CardValidationError(
                f"Question {question_index} participant field {field_key} must be an object."
            )

        enabled = normalize_boolean(raw_field.get("enabled", defaults["enabled"]))
        use_for_key = enabled and normalize_boolean(
            raw_field.get("use_for_key", defaults["use_for_key"])
        )
        store = enabled and normalize_boolean(raw_field.get("store", defaults["store"]))
        required = enabled and normalize_boolean(raw_field.get("required", defaults["required"]))
        if use_for_key and raw_field.get("required") is not None and not required:
            raise CardValidationError(
                f"Question {question_index} participant field {field_key} cannot be optional because it is used for the anonymous code."
            )
        normalized[field_key] = {
            "enabled": enabled,
            "use_for_key": use_for_key,
            "store": store,
            "required": True if use_for_key else required,
        }
        if field_key in CONFIGURABLE_OPTION_DEFAULTS:
            normalized[field_key]["options"] = _normalize_field_options(
                raw_field.get("options"), field_key
            )

    if not any(field.get("enabled") and field.get("use_for_key") for field in normalized.values()):
        raise CardValidationError(
            f"Question {question_index} participant fields need at least one field for key generation."
        )

    return normalized


def _normalize_field_options(value: Any, field_key: str) -> list[str]:
    defaults = CONFIGURABLE_OPTION_DEFAULTS[field_key]
    if not isinstance(value, list):
        return list(defaults)

    cleaned: list[str] = []
    for item in value:
        text = normalize_text(item)
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned or list(defaults)


def _normalize_participant_id_question(question_data: dict[str, Any], question_index: int) -> dict[str, Any]:
    normalized = {
        "type": "participant-id",
        "prompt": normalize_text(question_data.get("prompt")),
        "fields": _validate_participant_fields(
            question_data.get("fields"),
            question_index,
        ),
    }
    code_label = normalize_text(question_data.get("code_label"))
    if code_label:
        normalized["code_label"] = code_label
    return normalized


def get_card_defaults(question_type: str) -> dict:
    return deepcopy(DEFAULTS[question_type])


def normalize_card_config(
    question_type: str, data: dict, index: int, host_data: dict
) -> dict:
    return _normalize_participant_id_question(data, index)


PLUGIN = Plugin(
    key="participant_id",
    label="participant-id",
    category="card",
    config_key="participant_id",
    can_toggle=False,
    get_card_defaults=get_card_defaults,
    normalize_card_config=normalize_card_config,
    validate_card_answer=None,
)
