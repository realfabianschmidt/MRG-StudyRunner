"""One-way compatibility migrations for saved study documents."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable


LEGACY_STIMULUS_MIGRATION_TEMPLATE: dict[str, Any] = {
    "type": "stimulus",
    "title": "Observe the material",
    "subtitle": "Pay attention to all sensory impressions. The questionnaire will appear automatically.",
    "warmup_duration_ms": 0,
    "duration_ms": 30000,
    "trigger_type": "timer",
    "trigger_content": "",
    "plugin_actions": {},
}


def migrate_study_config(
    config_data: dict[str, Any],
    *,
    normalize_settings: Callable[[dict[str, Any]], dict[str, Any]],
    normalize_actions: Callable[[dict[str, Any]], dict[str, dict[str, Any]]],
    legacy_card_fields: set[str],
) -> dict[str, Any]:
    """Return the current shape while preserving canonical values."""

    migrated = deepcopy(config_data)
    if "stimulus_duration_ms" in migrated:
        card = dict(LEGACY_STIMULUS_MIGRATION_TEMPLATE)
        card["duration_ms"] = migrated.pop("stimulus_duration_ms")
        questions = migrated.get("questions", [])
        if not any(isinstance(question, dict) and question.get("type") == "stimulus" for question in questions):
            migrated["questions"] = [card] + questions
    migrated.pop("stimulus_duration_ms", None)

    settings = migrated.get("study_settings")
    if not isinstance(settings, dict):
        settings = {"sensors_enabled": False}
    migrated["study_settings"] = normalize_settings(settings)

    questions = migrated.get("questions")
    if isinstance(questions, list):
        for question in questions:
            if not isinstance(question, dict):
                continue
            if question.get("type") == "choice" and question.get("multiple") is False:
                question["type"] = "single"
                question.pop("multiple", None)
            if not question.get("info_top") and question.get("type") == "participant-id":
                legacy_hint = question.get("code_hint")
                if isinstance(legacy_hint, str) and legacy_hint.strip():
                    question["info_top"] = legacy_hint
            question.pop("code_hint", None)
            if question.get("type") == "stimulus":
                question["plugin_actions"] = normalize_actions(question)
                for legacy_key in legacy_card_fields:
                    question.pop(legacy_key, None)
    return migrated


def migrated_info_top(question: dict[str, Any], current: str) -> str:
    """Use the old participant-ID hint only when no current value exists."""

    if current or question.get("type") != "participant-id":
        return current
    legacy_hint = question.get("code_hint")
    return legacy_hint if isinstance(legacy_hint, str) else ""
