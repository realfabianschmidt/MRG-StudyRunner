"""Affect Map configuration and answer rules, executed in its own worker."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from study_runner.contracts.card_validation_primitives import (
    CardValidationError,
    normalize_boolean,
    normalize_text,
    require_text,
)
from study_runner.contracts.plugin_api import Plugin

# Field and Orbit record the chosen words and a position. Orbit also records
# intensity. This copy is independent of the Mood Meter plugin.
VARIANTS = ("field", "orbit")
POSITION_VARIANTS = frozenset({"field", "orbit"})

DEFAULT_COLORS = {"red": "#B4402E", "yellow": "#BD7A1E", "green": "#2F7A4D", "blue": "#2A6FA0"}
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

DEFAULTS = {'affect-map': {'type': 'affect-map',
                'prompt': 'How do you feel right now?',
                'variant': 'field',
                'allow_multiple': True,
                'word_lists': {'red': ['Enraged',
                                       'Panicked',
                                       'Stressed',
                                       'Jittery',
                                       'Shocked',
                                       'Livid',
                                       'Furious',
                                       'Frustrated',
                                       'Tense',
                                       'Stunned',
                                       'Fuming',
                                       'Frightened',
                                       'Angry',
                                       'Nervous',
                                       'Restless',
                                       'Anxious',
                                       'Apprehensive',
                                       'Worried',
                                       'Irritated',
                                       'Annoyed',
                                       'Repulsed',
                                       'Troubled',
                                       'Concerned',
                                       'Uneasy',
                                       'Peeved'],
                               'yellow': ['Surprised',
                                          'Upbeat',
                                          'Festive',
                                          'Exhilarated',
                                          'Ecstatic',
                                          'Hyper',
                                          'Cheerful',
                                          'Motivated',
                                          'Inspired',
                                          'Elated',
                                          'Energized',
                                          'Lively',
                                          'Excited',
                                          'Optimistic',
                                          'Enthusiastic',
                                          'Pleased',
                                          'Focused',
                                          'Happy',
                                          'Proud',
                                          'Thrilled',
                                          'Pleasant',
                                          'Joyful',
                                          'Hopeful',
                                          'Playful',
                                          'Blissful'],
                               'green': ['At Ease',
                                         'Easygoing',
                                         'Content',
                                         'Loving',
                                         'Fulfilled',
                                         'Calm',
                                         'Secure',
                                         'Satisfied',
                                         'Grateful',
                                         'Touched',
                                         'Relaxed',
                                         'Chill',
                                         'Restful',
                                         'Blessed',
                                         'Balanced',
                                         'Mellow',
                                         'Thoughtful',
                                         'Peaceful',
                                         'Comfortable',
                                         'Carefree',
                                         'Sleepy',
                                         'Complacent',
                                         'Tranquil',
                                         'Cozy',
                                         'Serene'],
                               'blue': ['Disgusted',
                                        'Glum',
                                        'Disappointed',
                                        'Down',
                                        'Apathetic',
                                        'Pessimistic',
                                        'Morose',
                                        'Discouraged',
                                        'Sad',
                                        'Bored',
                                        'Alienated',
                                        'Miserable',
                                        'Lonely',
                                        'Disheartened',
                                        'Tired',
                                        'Despondent',
                                        'Depressed',
                                        'Sullen',
                                        'Exhausted',
                                        'Fatigued',
                                        'Despair',
                                        'Hopeless',
                                        'Desolate',
                                        'Spent',
                                         'Drained']}}}
DEFAULTS["affect-map"].update(
    colors_enabled=True,
    region_colors=DEFAULT_COLORS.copy(),
    # Empty = the default direction captions; a name replaces them in both views.
    region_labels={key: "" for key in DEFAULT_COLORS},
)
REGION_LABEL_MAX = 40


def _normalize_affect_map_question(question_data: dict[str, Any], question_index: int) -> dict[str, Any]:
    word_lists = question_data.get("word_lists")
    if word_lists is not None and not isinstance(word_lists, dict):
        word_lists = None
    variant = str(question_data.get("variant") or "field").strip().lower()
    given_colors = question_data.get("region_colors")
    given_colors = given_colors if isinstance(given_colors, dict) else {}
    colors = {
        key: value.upper() if isinstance(value := given_colors.get(key), str) and HEX_COLOR.fullmatch(value)
        else default
        for key, default in DEFAULT_COLORS.items()
    }
    return {
        "type": "affect-map",
        "prompt": normalize_text(question_data.get("prompt")),
        "variant": variant if variant in VARIANTS else "field",
        "allow_multiple": normalize_boolean(question_data.get("allow_multiple", True)),
        "word_lists": word_lists,
        "colors_enabled": normalize_boolean(question_data.get("colors_enabled", True)),
        "region_colors": colors,
        "region_labels": _region_labels(question_data.get("region_labels")),
    }


def _region_labels(value: Any) -> dict[str, str]:
    given = value if isinstance(value, dict) else {}
    return {key: normalize_text(given.get(key))[:REGION_LABEL_MAX] for key in DEFAULT_COLORS}


def _validate_words(*, question: dict[str, Any], words: Any, question_number: int) -> list[str]:
    if not isinstance(words, list):
        raise CardValidationError(f"Question {question_number} answer must be a list.")
    normalized = [require_text(item, f"Question {question_number} word") for item in words]
    if not normalized:
        raise CardValidationError(f"Question {question_number} needs at least one selected word.")
    if len(set(normalized)) != len(normalized):
        raise CardValidationError(f"Question {question_number} contains duplicate words.")
    if question.get("allow_multiple") is False and len(normalized) != 1:
        raise CardValidationError(f"Question {question_number} allows exactly one selected word.")
    return normalized


def _unit_value(value: Any, name: str, question_number: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CardValidationError(f"Question {question_number} {name} must be a number.")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise CardValidationError(f"Question {question_number} {name} must be between 0 and 1.")
    return round(number, 3)


def _validate_mood_meter_answer(*, question: dict[str, Any], answer: Any, question_number: int) -> Any:
    variant = question.get("variant") or "field"
    if variant not in POSITION_VARIANTS:
        return _validate_words(question=question, words=answer, question_number=question_number)
    if not isinstance(answer, dict):
        raise CardValidationError(
            f"Question {question_number} answer must contain the chosen words and the position."
        )
    normalized: dict[str, Any] = {
        "words": _validate_words(question=question, words=answer.get("words"), question_number=question_number),
        "pleasantness": _unit_value(answer.get("pleasantness"), "pleasantness", question_number),
        "energy": _unit_value(answer.get("energy"), "energy", question_number),
    }
    if variant == "orbit":
        normalized["intensity"] = _unit_value(answer.get("intensity"), "intensity", question_number)
    return normalized


def get_card_defaults(question_type: str) -> dict:
    return deepcopy(DEFAULTS[question_type])


def normalize_card_config(
    question_type: str, data: dict, index: int, host_data: dict
) -> dict:
    return _normalize_affect_map_question(data, index)


def validate_card_answer(
    question_type: str, question: dict, answer: Any, number: int
) -> Any:
    return _validate_mood_meter_answer(
        question=question, answer=answer, question_number=number
    )


PLUGIN = Plugin(
    key="affect_map",
    label="Affect Map",
    category="card",
    config_key="affect_map",
    can_toggle=False,
    get_card_defaults=get_card_defaults,
    normalize_card_config=normalize_card_config,
    validate_card_answer=validate_card_answer,
)
