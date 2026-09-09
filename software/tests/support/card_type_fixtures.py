"""One golden fixture per registered card type (package 5g.B1).

Package 5g (docs/architecture-1.0-umbau.md): before any card-type logic in
`validation.py` is touched -- moved into a table (5g.B3), delivered as an
extension manifest (5g.B5), or anything else -- there must be a way to prove
the new shape means exactly what the old one meant. This module is that
proof's raw material: for every type in ``ALLOWED_QUESTION_TYPES``, a
minimal but realistic author-written question, and (for every type that
actually collects an answer) a submitted answer.

The *expected* outputs are captured here as frozen values, audited by hand
against the branches in ``validation.py`` that produce them -- not asserted
by re-running the code and trusting whatever it returns today, which would
only prove the code agrees with itself. ``test_card_type_fixtures.py`` is
what checks the two stay in agreement; a failure there means either this
fixture is stale or a real behavior change needs a conscious decision, not
a silent one.

``stimulus`` is the one type excluded from the exact-match half of that
check: its normalized output includes a ``plugin_actions`` block built from
whichever plugin manifests happen to be installed
(``study_plugin_config.normalize_card_plugin_actions`` reads the live
registry), so its exact content is not a property of card-type validation
at all. Pinning it here would make this fixture fail for a reason that has
nothing to do with card types -- a plugin gaining or losing a
``card_actions_schema`` field. ``STIMULUS_EXCLUDED_KEYS`` names the keys
left out of that comparison.
"""
from __future__ import annotations

from typing import Any

STIMULUS_EXCLUDED_KEYS = frozenset({"plugin_actions"})

# question_type -> {"question": ..., "expected_question": ..., and for
# answerable types "answer": ..., "expected_answer": ...}. Non-answer types
# (participant-id, stimulus, finish) carry no "answer" key at all, matching
# NON_ANSWER_QUESTION_TYPES in validation.py -- their absence here is itself
# a check that the fixture set has not drifted from that set (see the test).
CARD_TYPE_FIXTURES: dict[str, dict[str, Any]] = {
    "participant-id": {
        "question": {
            "type": "participant-id",
            "prompt": "Please identify yourself anonymously.",
            "code_label": "Your code",
        },
        "expected_question": {
            "type": "participant-id",
            "prompt": "Please identify yourself anonymously.",
            "code_label": "Your code",
            "fields": {
                "first_name": {"enabled": True, "use_for_key": True, "store": False, "required": True},
                "last_name": {"enabled": True, "use_for_key": True, "store": False, "required": True},
                "age_group": {
                    "enabled": True,
                    "use_for_key": True,
                    "store": True,
                    "required": True,
                    "options": ["18-25", "26-35", "36-45", "46-60", "60+"],
                },
                "gender": {
                    "enabled": False,
                    "use_for_key": False,
                    "store": False,
                    "required": False,
                    "options": ["Female", "Male", "Non-binary", "Prefer not to say"],
                },
                "childhood_area": {"enabled": True, "use_for_key": True, "store": True, "required": True},
                "childhood_nearest_city": {
                    "enabled": True,
                    "use_for_key": True,
                    "store": True,
                    "required": True,
                },
                "birth_place": {"enabled": False, "use_for_key": False, "store": False, "required": False},
                "birth_date": {"enabled": False, "use_for_key": False, "store": False, "required": False},
            },
        },
    },
    "stimulus": {
        "question": {
            "type": "stimulus",
            "title": "Watch the clip",
            "duration_ms": 5000,
            "trigger_type": "timer",
        },
        "expected_question": {
            "type": "stimulus",
            "title": "Watch the clip",
            "subtitle": "",
            "warmup_duration_ms": 0,
            "duration_ms": 5000,
            "trigger_type": "timer",
            "trigger_content": "",
            # plugin_actions deliberately omitted -- see STIMULUS_EXCLUDED_KEYS.
        },
    },
    "finish": {
        "question": {
            "type": "finish",
            "title": "Done",
            "prompt": "Thanks for participating.",
        },
        "expected_question": {
            "type": "finish",
            "title": "Done",
            "prompt": "Thanks for participating.",
        },
    },
    "likert": {
        "question": {
            "type": "likert",
            "prompt": "I felt engaged.",
            "scale": 7,
            "label_min": "Strongly disagree",
            "label_max": "Strongly agree",
        },
        "expected_question": {
            "type": "likert",
            "prompt": "I felt engaged.",
            "required": True,
            "scale": 7,
            "label_min": "Strongly disagree",
            "label_max": "Strongly agree",
        },
        "answer": 5,
        "expected_answer": 5,
    },
    "semantic": {
        "question": {
            "type": "semantic",
            "prompt": "Rate the material.",
            "pairs": [["cold", "warm"], ["boring", "exciting"]],
        },
        "expected_question": {
            "type": "semantic",
            "prompt": "Rate the material.",
            "required": True,
            "pairs": [["cold", "warm"], ["boring", "exciting"]],
        },
        "answer": {"cold_warm": 4, "boring_exciting": 6},
        "expected_answer": {"cold_warm": 4, "boring_exciting": 6},
    },
    "choice": {
        "question": {
            "type": "choice",
            "prompt": "Which colors do you like?",
            "options": ["red", "green", "blue"],
        },
        "expected_question": {
            "type": "choice",
            "prompt": "Which colors do you like?",
            "required": True,
            "options": ["red", "green", "blue"],
        },
        "answer": ["red", "blue"],
        "expected_answer": ["red", "blue"],
    },
    "single": {
        "question": {
            "type": "single",
            "prompt": "Pick one color.",
            "options": ["red", "green", "blue"],
        },
        "expected_question": {
            "type": "single",
            "prompt": "Pick one color.",
            "required": True,
            "options": ["red", "green", "blue"],
        },
        "answer": "green",
        "expected_answer": "green",
    },
    "slider": {
        "question": {
            "type": "slider",
            "prompt": "How relaxed do you feel?",
            "label_min": "not at all",
            "label_max": "very",
        },
        "expected_question": {
            "type": "slider",
            "prompt": "How relaxed do you feel?",
            "required": True,
            "label_min": "not at all",
            "label_max": "very",
        },
        "answer": 72,
        "expected_answer": 72,
    },
    "ranking": {
        "question": {
            "type": "ranking",
            "prompt": "Rank these by preference.",
            "options": ["tea", "coffee", "water"],
        },
        "expected_question": {
            "type": "ranking",
            "prompt": "Rank these by preference.",
            "required": True,
            "options": ["tea", "coffee", "water"],
        },
        "answer": ["water", "tea", "coffee"],
        "expected_answer": ["water", "tea", "coffee"],
    },
    "text": {
        "question": {
            "type": "text",
            "prompt": "Any other comments?",
        },
        "expected_question": {
            "type": "text",
            "prompt": "Any other comments?",
            "required": True,
        },
        "answer": "None, thanks.",
        "expected_answer": "None, thanks.",
    },
    "mood-meter": {
        "question": {
            "type": "mood-meter",
            "prompt": "Pick the words that fit your mood.",
        },
        "expected_question": {
            "type": "mood-meter",
            "prompt": "Pick the words that fit your mood.",
            "required": True,
            "allow_multiple": True,
            "word_lists": None,
        },
        "answer": ["calm", "curious"],
        "expected_answer": ["calm", "curious"],
    },
    "multi-slider": {
        "question": {
            "type": "multi-slider",
            "prompt": "Rate each dimension.",
            "dimensions": [{"label": "energy"}, {"label": "valence"}],
        },
        "expected_question": {
            "type": "multi-slider",
            "prompt": "Rate each dimension.",
            "required": True,
            "dimensions": [
                {"label": "energy", "min_label": "", "max_label": ""},
                {"label": "valence", "min_label": "", "max_label": ""},
            ],
        },
        "answer": {"energy": 40, "valence": -10},
        "expected_answer": {"energy": 40, "valence": -10},
    },
    "word-cloud": {
        "question": {
            "type": "word-cloud",
            "prompt": "Pick words describing the experience.",
            "words": ["calm", "tense", "curious", "bored"],
        },
        "expected_question": {
            "type": "word-cloud",
            "prompt": "Pick words describing the experience.",
            "required": True,
            "words": ["calm", "tense", "curious", "bored"],
            "allow_multiple": True,
        },
        "answer": ["calm", "curious"],
        "expected_answer": ["calm", "curious"],
    },
}
