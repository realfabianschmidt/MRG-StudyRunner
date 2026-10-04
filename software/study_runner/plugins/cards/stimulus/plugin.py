"""Card configuration and answer rules, executed in the extension worker."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from study_runner.contracts.card_validation_primitives import (
    CardValidationError,
    normalize_boolean,
    normalize_integer,
    normalize_text,
)
from study_runner.contracts.plugin_api import Plugin

DEFAULTS = {'stimulus': {'type': 'stimulus',
              'title': 'Observe the material',
              'info_top': 'Pay attention to all sensory impressions. The questionnaire '
                          'will appear automatically.',
              'warmup_duration_ms': 0,
              'duration_ms': 30000,
              'trigger_type': 'timer',
              'trigger_content': '',
              'plugin_actions': {},
              # A new card drives no actuator until the researcher picks one.
              'actuator_plugins': [],
              'end_sound': 'none',
              'end_sound_url': '',
              'end_sound_volume': 80,
              'auto_advance': True,
              'overtime_keep_stimulus': True,
              'overtime_keep_actuators': False,
              'overtime_max_ms': 300000}}


ALLOWED_TRIGGER_TYPES = {"timer", "image", "video", "audio", "html", "js"}
ALLOWED_END_SOUNDS = {"none", "gong", "bell", "beep", "custom"}


def _normalize_trigger_type(value: Any, question_index: int) -> str:
    trigger_type = normalize_text(value, default="timer")
    if trigger_type not in ALLOWED_TRIGGER_TYPES:
        raise CardValidationError(
            f"Question {question_index} uses an unknown trigger type: {trigger_type!r}."
        )
    return trigger_type


def _normalize_end_sound(value: Any, question_index: int) -> str:
    end_sound = normalize_text(value, default="none") or "none"
    if end_sound not in ALLOWED_END_SOUNDS:
        raise CardValidationError(
            f"Question {question_index} uses an unknown end sound: {end_sound!r}."
        )
    return end_sound


def _normalize_stimulus_question(question_data: dict[str, Any], question_index: int, host_data: dict) -> dict[str, Any]:
    plugin_actions = host_data["plugin_actions"]
    defaults = DEFAULTS["stimulus"]
    return {
        "type": "stimulus",
        "title": normalize_text(question_data.get("title"), default="Observe the material"),
        "subtitle": normalize_text(question_data.get("subtitle")),
        "warmup_duration_ms": normalize_integer(
            question_data.get("warmup_duration_ms", 0),
            field_name=f"Question {question_index} warm-up duration",
            minimum=0,
            maximum=3_600_000,
        ),
        "duration_ms": normalize_integer(
            question_data.get("duration_ms", 30_000),
            field_name=f"Question {question_index} duration",
            minimum=1_000,
            maximum=3_600_000,
        ),
        "trigger_type": _normalize_trigger_type(
            question_data.get("trigger_type", "timer"),
            question_index=question_index,
        ),
        "trigger_content": normalize_text(question_data.get("trigger_content")),
        "plugin_actions": plugin_actions,
        # The host derives the list from the plugin manifests; the card only
        # carries it, it never decides which plugin is an actuator.
        "actuator_plugins": list(host_data["actuator_plugins"]),
        "end_sound": _normalize_end_sound(question_data.get("end_sound"), question_index),
        "end_sound_url": normalize_text(question_data.get("end_sound_url")),
        "end_sound_volume": normalize_integer(
            question_data.get("end_sound_volume", defaults["end_sound_volume"]),
            field_name=f"Question {question_index} end sound volume",
            minimum=0,
            maximum=100,
        ),
        "auto_advance": normalize_boolean(question_data.get("auto_advance", True)),
        "overtime_keep_stimulus": normalize_boolean(
            question_data.get("overtime_keep_stimulus", defaults["overtime_keep_stimulus"])
        ),
        "overtime_keep_actuators": normalize_boolean(
            question_data.get("overtime_keep_actuators", defaults["overtime_keep_actuators"])
        ),
        "overtime_max_ms": normalize_integer(
            question_data.get("overtime_max_ms", defaults["overtime_max_ms"]),
            field_name=f"Question {question_index} maximum overtime",
            minimum=10_000,
            maximum=3_600_000,
        ),
    }


def get_card_defaults(question_type: str) -> dict:
    return deepcopy(DEFAULTS[question_type])


def normalize_card_config(
    question_type: str, data: dict, index: int, host_data: dict
) -> dict:
    return _normalize_stimulus_question(data, index, host_data)


PLUGIN = Plugin(
    key="stimulus",
    label="stimulus",
    category="card",
    config_key="stimulus",
    can_toggle=False,
    get_card_defaults=get_card_defaults,
    normalize_card_config=normalize_card_config,
    validate_card_answer=None,
)
