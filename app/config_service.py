import json
from pathlib import Path
from typing import Any


DEFAULT_STIMULUS_CARD: dict[str, Any] = {
    "type": "stimulus",
    "title": "Observe the material",
    "subtitle": "Pay attention to all sensory impressions. The questionnaire will appear automatically.",
    "warmup_duration_ms": 0,
    "duration_ms": 30000,
    "trigger_type": "timer",
    "trigger_content": "",
    "send_signal": True,
}


def normalize_config(config_data: dict[str, Any]) -> dict[str, Any]:
    """Migrate old config keys into the current card-based study structure."""
    if "stimulus_duration_ms" in config_data:
        card = dict(DEFAULT_STIMULUS_CARD)
        card["duration_ms"] = config_data.pop("stimulus_duration_ms")
        questions = config_data.get("questions", [])
        if not any(q.get("type") == "stimulus" for q in questions):
            config_data["questions"] = [card] + questions
    config_data.pop("stimulus_duration_ms", None)

    for question_data in config_data.get("questions", []):
        if (
            isinstance(question_data, dict)
            and question_data.get("type") == "choice"
            and question_data.get("multiple") is False
        ):
            question_data["type"] = "single"
            question_data.pop("multiple", None)

    return config_data


def load_config(config_file: Path) -> dict[str, Any]:
    with config_file.open(encoding="utf-8") as file_handle:
        return normalize_config(json.load(file_handle))


def save_config(config_file: Path, config_data: dict[str, Any]) -> None:
    with config_file.open("w", encoding="utf-8") as file_handle:
        json.dump(config_data, file_handle, indent=2, ensure_ascii=False)
