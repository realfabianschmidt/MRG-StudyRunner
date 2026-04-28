from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


ALLOWED_QUESTION_TYPES = {
    "stimulus",
    "participant-id",
    "finish",
    "likert",
    "semantic",
    "choice",
    "single",
    "slider",
    "ranking",
    "text",
    "mood-meter",
    "multi-slider",
    "word-cloud",
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
    submitted_study_id = _normalize_text(result_payload.get("study_id"))
    if submitted_study_id and submitted_study_id != study_config["study_id"]:
        raise ValidationError("Submitted study_id does not match the active study configuration.")

    if _parse_iso_timestamp(timestamp_end) < _parse_iso_timestamp(timestamp_start):
        raise ValidationError("End timestamp must be later than or equal to the start timestamp.")

    answers = result_payload.get("answers")
    if not isinstance(answers, dict):
        raise ValidationError("Answers must be a JSON object.")

    return {
        "participant_id": participant_id,
        "study_id": study_config["study_id"],
        "timestamp_start": timestamp_start,
        "timestamp_end": timestamp_end,
        "answers": _validate_answers(answers, study_config.get("questions", [])),
    }


def validate_and_normalize_trial_options(payload: Any) -> dict[str, Any]:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValidationError("The trial control payload must be a JSON object.")

    return {
        "send_signal": _normalize_boolean(payload.get("send_signal", True)),
        "brainbit_to_lsl": _normalize_boolean(payload.get("brainbit_to_lsl", False)),
        "brainbit_to_touchdesigner": _normalize_boolean(
            payload.get("brainbit_to_touchdesigner", False)
        ),
        "mini_radar_recording_enabled": _normalize_boolean(
            payload.get("mini_radar_recording_enabled", False)
        ),
        "client_trigger_ms": _normalize_float(
            payload.get("client_trigger_ms"),
            field_name="client_trigger_ms",
            minimum=0.0,
            maximum=86_400_000.0,
            allow_none=True,
        ),
        "clock_offset_ms": _normalize_float(
            payload.get("clock_offset_ms"),
            field_name="clock_offset_ms",
            minimum=-3_600_000.0,
            maximum=3_600_000.0,
            allow_none=True,
        ),
    }


def _validate_answers(
    answers: dict[str, Any],
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_answers: dict[str, Any] = {}
    expected_keys = set()

    for question_index, question in enumerate(questions):
        if question.get("type") in {"stimulus", "participant-id", "finish"}:
            continue

        answer_key = f"q{question_index}"
        expected_keys.add(answer_key)
        if answer_key not in answers:
            raise ValidationError(f"Missing answer for question {question_index + 1}.")

        normalized_answers[answer_key] = _validate_answer_value(
            answer_key=answer_key,
            question=question,
            answer=answers.get(answer_key),
            question_number=question_index + 1,
        )

    extra_keys = sorted(set(answers.keys()) - expected_keys)
    if extra_keys:
        raise ValidationError(f"Unexpected answer keys: {', '.join(extra_keys)}.")

    return normalized_answers


def _validate_answer_value(
    *,
    answer_key: str,
    question: dict[str, Any],
    answer: Any,
    question_number: int,
) -> Any:
    question_type = question.get("type")

    if question_type == "likert":
        return _normalize_integer(
            answer,
            field_name=f"Question {question_number} answer",
            minimum=1,
            maximum=int(question.get("scale", 7)),
        )

    if question_type == "semantic":
        expected_pairs = question.get("pairs", [])
        if not isinstance(answer, dict):
            raise ValidationError(f"Question {question_number} answer must be an object.")

        normalized: dict[str, int] = {}
        for pair in expected_pairs:
            pair_key = f"{pair[0]}_{pair[1]}"
            if pair_key not in answer:
                raise ValidationError(f"Question {question_number} is missing a rating for {pair_key}.")
            normalized[pair_key] = _normalize_integer(
                answer.get(pair_key),
                field_name=f"Question {question_number} answer for {pair_key}",
                minimum=1,
                maximum=7,
            )

        extra_keys = sorted(set(answer.keys()) - set(normalized.keys()))
        if extra_keys:
            raise ValidationError(
                f"Question {question_number} contains unexpected semantic keys: {', '.join(extra_keys)}."
            )
        return normalized

    if question_type == "choice":
        if not isinstance(answer, list):
            raise ValidationError(f"Question {question_number} answer must be a list.")
        options = question.get("options", [])
        normalized = [_require_text(item, f"Question {question_number} answer") for item in answer]
        if not normalized:
            raise ValidationError(f"Question {question_number} needs at least one selected option.")
        if len(set(normalized)) != len(normalized):
            raise ValidationError(f"Question {question_number} contains duplicate selected options.")
        invalid = [item for item in normalized if item not in options]
        if invalid:
            raise ValidationError(
                f"Question {question_number} contains invalid options: {', '.join(invalid)}."
            )
        return normalized

    if question_type == "single":
        selected = _require_text(answer, f"Question {question_number} answer")
        if selected not in question.get("options", []):
            raise ValidationError(f"Question {question_number} answer is not a valid option.")
        return selected

    if question_type == "ranking":
        if not isinstance(answer, list):
            raise ValidationError(f"Question {question_number} ranking answer must be a list.")
        normalized = [_require_text(item, f"Question {question_number} ranking item") for item in answer]
        options = question.get("options", [])
        if len(normalized) == len(options) and set(normalized) == set(options):
            return normalized
        raise ValidationError(
            f"Question {question_number} ranking must contain each configured option exactly once."
        )

    if question_type == "slider":
        return _normalize_integer(
            answer,
            field_name=f"Question {question_number} answer",
            minimum=0,
            maximum=100,
        )

    if question_type == "text":
        return _require_text(answer, f"Question {question_number} answer")

    if question_type == "mood-meter":
        if not isinstance(answer, list):
            raise ValidationError(f"Question {question_number} answer must be a list.")
        normalized = [_require_text(item, f"Question {question_number} word") for item in answer]
        if not normalized:
            raise ValidationError(f"Question {question_number} needs at least one selected word.")
        if len(set(normalized)) != len(normalized):
            raise ValidationError(f"Question {question_number} contains duplicate words.")
        if question.get("allow_multiple") is False and len(normalized) != 1:
            raise ValidationError(f"Question {question_number} allows exactly one selected word.")
        return normalized

    if question_type == "multi-slider":
        if not isinstance(answer, dict):
            raise ValidationError(f"Question {question_number} answer must be an object.")
        normalized: dict[str, int] = {}
        dimensions = question.get("dimensions", [])
        for dimension in dimensions:
            label = _require_text(dimension.get("label"), f"Question {question_number} dimension label")
            if label not in answer:
                raise ValidationError(f"Question {question_number} is missing a value for {label}.")
            normalized[label] = _normalize_integer(
                answer.get(label),
                field_name=f"Question {question_number} answer for {label}",
                minimum=-100,
                maximum=100,
            )

        extra_keys = sorted(set(answer.keys()) - set(normalized.keys()))
        if extra_keys:
            raise ValidationError(
                f"Question {question_number} contains unexpected dimensions: {', '.join(extra_keys)}."
            )
        return normalized

    if question_type == "word-cloud":
        if not isinstance(answer, list):
            raise ValidationError(f"Question {question_number} answer must be a list.")
        normalized = [_require_text(item, f"Question {question_number} word") for item in answer]
        if not normalized:
            raise ValidationError(f"Question {question_number} needs at least one selected word.")
        if len(set(normalized)) != len(normalized):
            raise ValidationError(f"Question {question_number} contains duplicate words.")
        if question.get("allow_multiple") is False and len(normalized) != 1:
            raise ValidationError(f"Question {question_number} allows exactly one selected word.")
        invalid = [item for item in normalized if item not in question.get("words", [])]
        if invalid:
            raise ValidationError(
                f"Question {question_number} contains invalid words: {', '.join(invalid)}."
            )
        return normalized

    raise ValidationError(f"{answer_key} uses an unsupported question type: {question_type!r}.")


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
            "camera_capture_enabled": _normalize_boolean(
                question_data.get("camera_capture_enabled", False)
            ),
            "camera_snapshot_interval_ms": _normalize_integer(
                question_data.get("camera_snapshot_interval_ms", 1000),
                field_name=f"Question {question_index} camera snapshot interval",
                minimum=250,
                maximum=60_000,
            ),
            "mini_radar_recording_enabled": _normalize_boolean(
                question_data.get(
                    "mini_radar_recording_enabled",
                    question_data.get("send_signal", True),
                )
            ),
        }

    if question_type == "participant-id":
        return {
            "type": "participant-id",
            "prompt": _normalize_text(question_data.get("prompt")),
        }

    if question_type == "finish":
        return {
            "type": "finish",
            "title": _normalize_text(question_data.get("title"), default="Vielen Dank!"),
            "prompt": _normalize_text(
                question_data.get("prompt"),
                default="Deine Antworten wurden gespeichert.\nDu kannst das Gerät jetzt ablegen.",
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

    if question_type == "mood-meter":
        word_lists = question_data.get("word_lists")
        if word_lists is not None and not isinstance(word_lists, dict):
            word_lists = None
        return {
            "type": "mood-meter",
            "prompt": _normalize_text(question_data.get("prompt")),
            "allow_multiple": _normalize_boolean(question_data.get("allow_multiple", True)),
            "word_lists": word_lists,
        }

    if question_type == "multi-slider":
        dims = question_data.get("dimensions")
        if not isinstance(dims, list) or not dims:
            raise ValidationError(f"Question {question_index} needs at least one dimension.")
        normalized_dims = []
        for d in dims:
            if isinstance(d, dict) and d.get("label"):
                normalized_dims.append({
                    "label": _normalize_text(d.get("label")),
                    "min_label": _normalize_text(d.get("min_label")),
                    "max_label": _normalize_text(d.get("max_label")),
                })
        if not normalized_dims:
            raise ValidationError(f"Question {question_index} needs at least one valid dimension.")
        return {
            "type": "multi-slider",
            "prompt": _normalize_text(question_data.get("prompt")),
            "dimensions": normalized_dims,
        }

    if question_type == "word-cloud":
        words = _normalize_text_list(question_data.get("words"))
        if not words:
            raise ValidationError(f"Question {question_index} needs at least one word.")
        return {
            "type": "word-cloud",
            "prompt": _normalize_text(question_data.get("prompt")),
            "words": words,
            "allow_multiple": _normalize_boolean(question_data.get("allow_multiple", True)),
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


def _normalize_float(
    value: Any,
    *,
    field_name: str,
    minimum: float,
    maximum: float,
    allow_none: bool = False,
) -> float | None:
    if value in (None, ""):
        if allow_none:
            return None
        raise ValidationError(f"{field_name} is required.")

    if isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a number.")

    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} must be a number.") from exc

    if normalized < minimum or normalized > maximum:
        raise ValidationError(f"{field_name} must be between {minimum} and {maximum}.")
    return normalized


def _normalize_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
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
    try:
        _parse_iso_timestamp(timestamp)
    except ValueError as exc:
        raise ValidationError(f"{field_name} must be a valid ISO timestamp.") from exc
    return timestamp


def _parse_iso_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
