"""Example question-card extension -- Study Runner extension SDK template.

`tools/extension_sdk.py new cards <your_key>` renames the plugin key
(`example_card`) for you. The question type itself (`example-question`,
hyphenated per convention) must be renamed by hand here, in manifest.json,
and in card.js -- it has to be globally unique across every card extension,
which the SDK cannot know in advance.

Card defaults, config normalization, and answer validation all live here,
in the extension's own process (Phase 5g.B5) -- never as a type-specific
branch in core code (CONTRIBUTING.md #7). See docs/developer-guide.md,
"Adding A Card Type".
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from study_runner.contracts.card_validation_primitives import normalize_text
from study_runner.contracts.plugin_api import Plugin

DEFAULTS = {
    "example-question": {
        "type": "example-question",
        "prompt": "",
    }
}


def get_card_defaults(question_type: str) -> dict:
    return deepcopy(DEFAULTS[question_type])


def normalize_card_config(
    question_type: str, data: dict, index: int, host_data: dict
) -> dict:
    del index, host_data  # unused in this minimal example
    return {"type": question_type, "prompt": normalize_text(data.get("prompt"))}


def validate_card_answer(
    question_type: str, question: dict, answer: Any, number: int
) -> Any:
    del question_type, question, number  # unused in this minimal example
    return normalize_text(answer)


PLUGIN = Plugin(
    key="example_card",
    label="Example card",
    category="card",
    config_key="example_card",
    can_toggle=False,
    get_card_defaults=get_card_defaults,
    normalize_card_config=normalize_card_config,
    validate_card_answer=validate_card_answer,
)
