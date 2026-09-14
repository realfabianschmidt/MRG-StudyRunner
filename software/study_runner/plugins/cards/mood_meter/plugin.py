"""Card configuration and answer rules, executed in the extension worker."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from study_runner.contracts.card_validation_primitives import (
    CardValidationError,
    normalize_boolean,
    normalize_text,
    require_text,
)
from study_runner.contracts.plugin_api import Plugin

DEFAULTS = {'mood-meter': {'type': 'mood-meter',
                'prompt': 'How do you feel right now?',
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


def _normalize_mood_meter_question(question_data: dict[str, Any], question_index: int) -> dict[str, Any]:
    word_lists = question_data.get("word_lists")
    if word_lists is not None and not isinstance(word_lists, dict):
        word_lists = None
    return {
        "type": "mood-meter",
        "prompt": normalize_text(question_data.get("prompt")),
        "allow_multiple": normalize_boolean(question_data.get("allow_multiple", True)),
        "word_lists": word_lists,
    }


def _validate_mood_meter_answer(*, question: dict[str, Any], answer: Any, question_number: int) -> Any:
    if not isinstance(answer, list):
        raise CardValidationError(f"Question {question_number} answer must be a list.")
    normalized = [require_text(item, f"Question {question_number} word") for item in answer]
    if not normalized:
        raise CardValidationError(f"Question {question_number} needs at least one selected word.")
    if len(set(normalized)) != len(normalized):
        raise CardValidationError(f"Question {question_number} contains duplicate words.")
    if question.get("allow_multiple") is False and len(normalized) != 1:
        raise CardValidationError(f"Question {question_number} allows exactly one selected word.")
    return normalized


def get_card_defaults(question_type: str) -> dict:
    return deepcopy(DEFAULTS[question_type])


def normalize_card_config(
    question_type: str, data: dict, index: int, host_data: dict
) -> dict:
    return _normalize_mood_meter_question(data, index)


def validate_card_answer(
    question_type: str, question: dict, answer: Any, number: int
) -> Any:
    return _validate_mood_meter_answer(
        question=question, answer=answer, question_number=number
    )


PLUGIN = Plugin(
    key="mood_meter",
    label="mood-meter",
    category="card",
    config_key="mood_meter",
    can_toggle=False,
    get_card_defaults=get_card_defaults,
    normalize_card_config=normalize_card_config,
    validate_card_answer=validate_card_answer,
)
