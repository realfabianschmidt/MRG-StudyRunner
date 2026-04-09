from __future__ import annotations

from datetime import datetime
from typing import Any


ALLOWED_QUESTION_TYPES = {
    "stimulus",
    "likert",
    "semantic",
    "choice",
    "single",
    "slider",
    "ranking",
    "text",
}

ALLOWED_TRIGGER_TYPES = {"timer", "image", "video", "audio", "html", "js"}


class ValidationError(ValueError):
    """Raised when config or result payloads are incomplete or malformed."""


def validate_and_normalize_config(config_data: Any) -> dict[str, Any]:
    if not isinstance(config_data, dict):
        raise ValidationError("The study configuration must be a JSON object.")

    study_id = _require_text(config_data.get("study_id"), "Study ID")
    questions = config_data.get("questions", [])
    if not isinstance(questions, list):
        raise ValidationError("Questions must be a list.")

    return {
        "study_id": study_id,
        "questions": [
            _validate_question(question_data, question_index)
            for question_index, question_data in enumerate(questions, start=1)
        ],
    }


def validate_and_normalize_results(
    result_payload: Any,
    study_config: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(result_payload, dict):
        raise ValidationError("The result payload must be a JSON object.")

    participant_id = _require_text(result_payload.get("participant_id"), "Participant-ID")
    timestamp_start = _require_iso_timestamp(result_payload.get("timestamp_start"), "Start timestamp")
    timestamp_end = _require_iso_timestamp(result_payload.get("timestamp_end"), "End timestamp")

    answers = result_payload.get("answers")
    if not isinstance(answers, dict):
        raise ValidationError("Answers must be a JSON object.")

    return {
        "participant_id": participant_id,
        "study_id": study_config["study_id"],
        "timestamp_start": timestamp_start,
        "timestamp_end": timestamp_end,
        "answers": answers,
    }


def _validate_question(question_data: Any, question_index: int) -> dict[str, Any]:
    if not isinstance(question_data, dict):
        raise ValidationError(f"Question {question_index} must be a JSON object.")

    question_type = _require_text(question_data.get("type"), f"Question {question_index} type")

    if question_type == "choice" and question_data.get("multiple") is False:
        question_type = "single"

    if question_type not in ALLOWED_QUESTION_TYPES:
        raise ValidationError(
            f"Question {question_index} uses an unknown type: {question_type!r}."
        )

    if question_type == "stimulus":
        return {
            "type": "stimulus",
            "title": _normalize_text(question_data.get("title"), default="Observe the material"),
            "subtitle": _normalize_text(question_data.get("subtitle")),
            "warmup_duration_ms": _normalize_integer(
                question_data.get("warmup_duration_ms", 0),
                field_name=f"Question {question_index} warm-up duration",
                minimum=0,
                maximum=3_600_000,
            ),
            "duration_ms": _normalize_integer(
                question_data.get("duration_ms", 30_000),
                field_name=f"Question {question_index} duration",
                minimum=1_000,
                maximum=3_600_000,
            ),
            "trigger_type": _normalize_trigger_type(
                question_data.get("trigger_type", "timer"),
                question_index=question_index,
            ),
            "trigger_content": _normalize_text(question_data.get("trigger_content")),
            "send_signal": _normalize_boolean(question_data.get("send_signal", True)),
            "brainbit_to_lsl": _normalize_boolean(
                question_data.get("brainbit_to_lsl", question_data.get("send_signal", True))
            ),
            "brainbit_to_touchdesigner": _normalize_boolean(
                question_data.get(
                    "brainbit_to_touchdesigner",
                    question_data.get("send_signal", True),
                )
            ),
        }

    if question_type == "likert":
        return {
            "type": "likert",
            "prompt": _normalize_text(question_data.get("prompt")),
            "scale": _normalize_integer(
                question_data.get("scale", 7),
                field_name=f"Question {question_index} scale",
                minimum=3,
                maximum=11,
            ),
            "label_min": _normalize_text(question_data.get("label_min")),
            "label_max": _normalize_text(question_data.get("label_max")),
        }

    if question_type == "semantic":
        pairs = _normalize_pairs(question_data.get("pairs"), question_index)
        if not pairs:
            raise ValidationError(f"Question {question_index} needs at least one valid word pair.")
        return {
            "type": "semantic",
            "prompt": _normalize_text(question_data.get("prompt")),
            "pairs": pairs,
        }

    if question_type in {"choice", "single", "ranking"}:
        options = _normalize_text_list(question_data.get("options"))
        if not options:
            raise ValidationError(f"Question {question_index} needs at least one option.")
        return {
            "type": question_type,
            "prompt": _normalize_text(question_data.get("prompt")),
            "options": options,
        }

    if question_type == "slider":
        return {
            "type": "slider",
            "prompt": _normalize_text(question_data.get("prompt")),
            "label_min": _normalize_text(question_data.get("label_min")),
            "label_max": _normalize_text(question_data.get("label_max")),
        }

    if question_type == "text":
        return {
            "type": "text",
            "prompt": _normalize_text(question_data.get("prompt")),
        }

    raise ValidationError(f"Question {question_index} could not be validated.")


def _normalize_trigger_type(value: Any, question_index: int) -> str:
    trigger_type = _normalize_text(value, default="timer")
    if trigger_type not in ALLOWED_TRIGGER_TYPES:
        raise ValidationError(
            f"Question {question_index} uses an unknown trigger type: {trigger_type!r}."
        )
    return trigger_type


def _normalize_pairs(value: Any, question_index: int) -> list[list[str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError(f"Question {question_index} word pairs must be a list.")

    pairs: list[list[str]] = []
    for pair in value:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValidationError(
                f"Question {question_index} has an invalid word pair. Use exactly two entries."
            )
        left = _normalize_text(pair[0])
        right = _normalize_text(pair[1])
        if not left or not right:
            raise ValidationError(
                f"Question {question_index} has an empty word in one of its pairs."
            )
        pairs.append([left, right])
    return pairs


def _normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError("Options must be a list of text entries.")
    return [entry for entry in (_normalize_text(item) for item in value) if entry]


def _normalize_integer(
    value: Any,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} must be a whole number.") from exc

    if normalized < minimum or normalized > maximum:
        raise ValidationError(f"{field_name} must be between {minimum} and {maximum}.")
    return normalized


def _normalize_boolean(value: Any) -> bool:
    return bool(value)


def _normalize_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _require_text(value: Any, field_name: str) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        raise ValidationError(f"{field_name} is required.")
    return normalized


def _require_iso_timestamp(value: Any, field_name: str) -> str:
    timestamp = _require_text(value, field_name)
    normalized = timestamp.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValidationError(f"{field_name} must be a valid ISO timestamp.") from exc
    return timestamp
