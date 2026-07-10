from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .study_sensor_runtime import STUDY_SENSOR_KEYS, normalize_study_sensors


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

PARTICIPANT_FIELD_ORDER = [
    "first_name",
    "last_name",
    "age_group",
    "gender",
    "childhood_area",
    "childhood_nearest_city",
    "birth_place",
    "birth_date",
]

PARTICIPANT_FIELD_DEFAULTS = {
    "first_name": {"enabled": True, "use_for_key": True, "store": False},
    "last_name": {"enabled": True, "use_for_key": True, "store": False},
    "age_group": {"enabled": True, "use_for_key": True, "store": True},
    "gender": {"enabled": False, "use_for_key": False, "store": True},
    "childhood_area": {"enabled": True, "use_for_key": True, "store": True},
    "childhood_nearest_city": {"enabled": True, "use_for_key": True, "store": True},
    "birth_place": {"enabled": False, "use_for_key": False, "store": True},
    "birth_date": {"enabled": False, "use_for_key": False, "store": True},
}

# Fields whose allowed answers can be configured per study.
AGE_GROUP_DEFAULT_OPTIONS = ["18-25", "26-35", "36-45", "46-60", "60+"]
GENDER_DEFAULT_OPTIONS = ["Female", "Male", "Non-binary", "Prefer not to say"]
CONFIGURABLE_OPTION_DEFAULTS = {
    "age_group": AGE_GROUP_DEFAULT_OPTIONS,
    "gender": GENDER_DEFAULT_OPTIONS,
}
CHILDHOOD_AREA_OPTIONS = {"urban", "rural"}


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
        "study_settings": _validate_study_settings(config_data.get("study_settings")),
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

    answer_events = _validate_answer_events(
        result_payload.get("answer_events"),
        study_config.get("questions", []),
    )
    card_events = _validate_card_events(
        result_payload.get("card_events"),
        study_config.get("questions", []),
    )
    participant_metadata = _validate_participant_metadata(
        result_payload.get("participant_metadata"),
        study_config.get("questions", []),
    )

    return {
        "participant_id": participant_id,
        "study_id": study_config["study_id"],
        "timestamp_start": timestamp_start,
        "timestamp_end": timestamp_end,
        # Tablet-vs-server clock offset (server = client + offset), used to
        # align client timestamps with server-clock sensor samples.
        "client_clock_offset_ms": _normalize_float(
            result_payload.get("client_clock_offset_ms"),
            field_name="client_clock_offset_ms",
            minimum=-10_000_000_000.0,
            maximum=10_000_000_000.0,
            allow_none=True,
        ),
        "answers": _validate_answers(answers, study_config.get("questions", [])),
        "participant_metadata": participant_metadata,
        "answer_events": answer_events,
        "card_events": card_events,
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
        "client_trigger_epoch_ms": _normalize_float(
            payload.get("client_trigger_epoch_ms"),
            field_name="client_trigger_epoch_ms",
            minimum=0.0,
            maximum=10_000_000_000_000.0,
            allow_none=True,
        ),
        "study_id": _normalize_text(payload.get("study_id")),
        "participant_id": _normalize_text(payload.get("participant_id")),
        "question_index": _normalize_optional_integer(
            payload.get("question_index"),
            field_name="question_index",
            minimum=0,
            maximum=10_000,
        ),
        "question_type": _normalize_question_type(payload.get("question_type")),
        "phase": _normalize_text(payload.get("phase")),
        "marker_event": _normalize_text(payload.get("marker_event") or payload.get("event")),
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


def _validate_participant_metadata(
    value: Any,
    questions: list[dict[str, Any]],
) -> dict[str, str]:
    stored_fields = _stored_participant_fields(questions)

    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValidationError("participant_metadata must be a JSON object.")

    normalized: dict[str, str] = {}
    for field_key in stored_fields:
        if field_key not in value:
            raise ValidationError(f"participant_metadata is missing {field_key}.")
        normalized[field_key] = _validate_participant_metadata_value(
            field_key,
            value.get(field_key),
            _participant_field_options(questions, field_key) if field_key in CONFIGURABLE_OPTION_DEFAULTS else None,
        )

    extra_keys = sorted(set(value.keys()) - set(stored_fields))
    if extra_keys:
        raise ValidationError(
            f"participant_metadata contains unexpected fields: {', '.join(extra_keys)}."
        )

    return normalized


def _stored_participant_fields(questions: list[dict[str, Any]]) -> list[str]:
    participant_question = next(
        (question for question in questions if question.get("type") == "participant-id"),
        None,
    )
    if not participant_question:
        return []

    fields = participant_question.get("fields") or {}
    stored: list[str] = []
    for field_key in PARTICIPANT_FIELD_ORDER:
        field_config = fields.get(field_key) or {}
        if field_config.get("enabled") and field_config.get("store"):
            stored.append(field_key)
    return stored


def _validate_participant_metadata_value(
    field_key: str,
    value: Any,
    configured_options: list[str] | None = None,
) -> str:
    normalized = _require_text(value, f"participant_metadata {field_key}")

    if field_key in CONFIGURABLE_OPTION_DEFAULTS:
        allowed = configured_options or CONFIGURABLE_OPTION_DEFAULTS[field_key]
        if normalized not in allowed:
            raise ValidationError(
                f"participant_metadata {field_key} must be one of: "
                + ", ".join(allowed)
                + "."
            )
        return normalized

    if field_key == "childhood_area":
        normalized = normalized.lower()
        if normalized not in CHILDHOOD_AREA_OPTIONS:
            raise ValidationError(
                "participant_metadata childhood_area must be urban or rural."
            )
        return normalized

    if field_key == "birth_date":
        try:
            _parse_iso_timestamp(normalized)
        except ValueError as exc:
            raise ValidationError(
                "participant_metadata birth_date must be a valid ISO date (YYYY-MM-DD)."
            ) from exc
        return normalized

    return normalized


def _validate_answer_events(
    value: Any,
    questions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError("answer_events must be a list.")

    normalized_events: list[dict[str, Any]] = []
    seen_indexes: set[int] = set()

    for raw_event in value:
        if not isinstance(raw_event, dict):
            raise ValidationError("Each answer_event must be an object.")

        question_index = _normalize_integer(
            raw_event.get("question_index"),
            field_name="answer_event question_index",
            minimum=0,
            maximum=max(0, len(questions) - 1),
        )
        if question_index in seen_indexes:
            raise ValidationError(f"Duplicate answer_event for question index {question_index}.")
        seen_indexes.add(question_index)

        question = questions[question_index] if question_index < len(questions) else {}
        answer_key = raw_event.get("answer_key")
        normalized_answer_key = _normalize_text(answer_key) if answer_key is not None else ""
        expected_answer_key = "" if question.get("type") == "participant-id" else f"q{question_index}"
        if normalized_answer_key != expected_answer_key:
            raise ValidationError(
                f"answer_event for question index {question_index} has an unexpected answer_key."
            )

        normalized_events.append(
            {
                "question_index": question_index,
                "question_type": _normalize_text(raw_event.get("question_type")),
                "answer_key": normalized_answer_key,
                "shown_at": _require_iso_timestamp(raw_event.get("shown_at"), "answer_event shown_at"),
                "answered_at": _require_iso_timestamp(raw_event.get("answered_at"), "answer_event answered_at"),
            }
        )

    return normalized_events


def _validate_card_events(
    value: Any,
    questions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError("card_events must be a list.")

    normalized_events: list[dict[str, Any]] = []
    seen_indexes: set[int] = set()

    for raw_event in value:
        if not isinstance(raw_event, dict):
            raise ValidationError("Each card_event must be an object.")

        question_index = _normalize_integer(
            raw_event.get("question_index"),
            field_name="card_event question_index",
            minimum=0,
            maximum=max(0, len(questions) - 1),
        )
        if question_index in seen_indexes:
            raise ValidationError(f"Duplicate card_event for question index {question_index}.")
        seen_indexes.add(question_index)

        question = questions[question_index] if question_index < len(questions) else {}
        expected_type = _normalize_text(question.get("type"))
        question_type = _normalize_text(raw_event.get("question_type"), default=expected_type)
        if expected_type and question_type and question_type != expected_type:
            raise ValidationError(
                f"card_event for question index {question_index} has an unexpected question_type."
            )

        normalized_events.append(
            {
                "question_index": question_index,
                "question_type": question_type,
                "shown_at": _require_iso_timestamp(raw_event.get("shown_at"), "card_event shown_at"),
                "shown_at_server_epoch_ms": _normalize_float(
                    raw_event.get("shown_at_server_epoch_ms"),
                    field_name="card_event shown_at_server_epoch_ms",
                    minimum=0.0,
                    maximum=10_000_000_000_000.0,
                    allow_none=True,
                ),
                "answered_at": _optional_iso_timestamp(raw_event.get("answered_at"), "card_event answered_at"),
                "answered_at_server_epoch_ms": _normalize_float(
                    raw_event.get("answered_at_server_epoch_ms"),
                    field_name="card_event answered_at_server_epoch_ms",
                    minimum=0.0,
                    maximum=10_000_000_000_000.0,
                    allow_none=True,
                ),
                "completed_at": _optional_iso_timestamp(raw_event.get("completed_at"), "card_event completed_at"),
                "active_started_at": _optional_iso_timestamp(raw_event.get("active_started_at"), "card_event active_started_at"),
                "active_ended_at": _optional_iso_timestamp(raw_event.get("active_ended_at"), "card_event active_ended_at"),
                "server_start_received_at": _optional_iso_timestamp(raw_event.get("server_start_received_at"), "card_event server_start_received_at"),
                "server_stop_received_at": _optional_iso_timestamp(raw_event.get("server_stop_received_at"), "card_event server_stop_received_at"),
                "server_start_received_epoch_ms": _normalize_float(
                    raw_event.get("server_start_received_epoch_ms"),
                    field_name="card_event server_start_received_epoch_ms",
                    minimum=0.0,
                    maximum=10_000_000_000_000.0,
                    allow_none=True,
                ),
                "server_stop_received_epoch_ms": _normalize_float(
                    raw_event.get("server_stop_received_epoch_ms"),
                    field_name="card_event server_stop_received_epoch_ms",
                    minimum=0.0,
                    maximum=10_000_000_000_000.0,
                    allow_none=True,
                ),
                "client_start_trigger_epoch_ms": _normalize_float(
                    raw_event.get("client_start_trigger_epoch_ms"),
                    field_name="card_event client_start_trigger_epoch_ms",
                    minimum=0.0,
                    maximum=10_000_000_000_000.0,
                    allow_none=True,
                ),
                "client_stop_trigger_epoch_ms": _normalize_float(
                    raw_event.get("client_stop_trigger_epoch_ms"),
                    field_name="card_event client_stop_trigger_epoch_ms",
                    minimum=0.0,
                    maximum=10_000_000_000_000.0,
                    allow_none=True,
                ),
                "start_marker": _normalize_text(raw_event.get("start_marker")),
                "stop_marker": _normalize_text(raw_event.get("stop_marker")),
            }
        )

    return normalized_events


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
    normalized = _validate_question_by_type(question_data, question_index)

    # Optional per-question info text, shared by every card type. Only kept when set.
    info_top = _normalize_text(question_data.get("info_top"))
    if not info_top and normalized.get("type") == "participant-id":
        # Migrate legacy participant-id privacy hint into the shared top callout.
        info_top = _normalize_text(question_data.get("code_hint"))
    info_bottom = _normalize_text(question_data.get("info_bottom"))
    if info_top:
        normalized["info_top"] = info_top
    if info_bottom:
        normalized["info_bottom"] = info_bottom

    return normalized


def _validate_question_by_type(question_data: Any, question_index: int) -> dict[str, Any]:
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
                question_data.get("camera_snapshot_interval_ms", 200),
                field_name=f"Question {question_index} camera snapshot interval",
                minimum=200,
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
        normalized = {
            "type": "participant-id",
            "prompt": _normalize_text(question_data.get("prompt")),
            "fields": _validate_participant_fields(
                question_data.get("fields"),
                question_index,
            ),
        }
        code_label = _normalize_text(question_data.get("code_label"))
        if code_label:
            normalized["code_label"] = code_label
        return normalized

    if question_type == "finish":
        return {
            "type": "finish",
            "title": _normalize_text(question_data.get("title"), default="Thank you!"),
            "prompt": _normalize_text(
                question_data.get("prompt"),
                default="Your answers have been saved.\nYou can now put the device down.",
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


def _validate_study_settings(value: Any) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValidationError("study_settings must be a JSON object.")

    raw_sensors = value.get("sensors")
    if raw_sensors is not None and not isinstance(raw_sensors, dict):
        raise ValidationError("study_settings.sensors must be a JSON object.")
    if isinstance(raw_sensors, dict):
        extra_sensor_keys = sorted(set(raw_sensors) - set(STUDY_SENSOR_KEYS))
        if extra_sensor_keys:
            raise ValidationError(
                "study_settings.sensors contains unsupported entries: "
                + ", ".join(extra_sensor_keys)
                + "."
            )

    sensors_enabled = _normalize_boolean(value.get("sensors_enabled", True))
    return {
        "sensors_enabled": sensors_enabled,
        "sensors": normalize_study_sensors(
            {
                "sensors_enabled": sensors_enabled,
                "sensors": raw_sensors,
            }
        ),
        "progress_bar_enabled": _normalize_boolean(value.get("progress_bar_enabled", False)),
        "notion_enabled": _normalize_boolean(value.get("notion_enabled", False)),
        "notion_parent_page_id": _normalize_text(value.get("notion_parent_page_id")),
        "notion_database_id": _normalize_text(value.get("notion_database_id")),
        "notion_data_source_id": _normalize_text(value.get("notion_data_source_id")),
    }


def _validate_participant_fields(value: Any, question_index: int) -> dict[str, dict[str, bool]]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValidationError(f"Question {question_index} participant fields must be an object.")

    extra_keys = sorted(set(value.keys()) - set(PARTICIPANT_FIELD_ORDER))
    if extra_keys:
        raise ValidationError(
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
            raise ValidationError(
                f"Question {question_index} participant field {field_key} must be an object."
            )

        enabled = _normalize_boolean(raw_field.get("enabled", defaults["enabled"]))
        use_for_key = enabled and _normalize_boolean(
            raw_field.get("use_for_key", defaults["use_for_key"])
        )
        store = enabled and _normalize_boolean(raw_field.get("store", defaults["store"]))
        normalized[field_key] = {
            "enabled": enabled,
            "use_for_key": use_for_key,
            "store": store,
        }
        if field_key in CONFIGURABLE_OPTION_DEFAULTS:
            normalized[field_key]["options"] = _normalize_field_options(
                raw_field.get("options"), field_key
            )

    if not any(field.get("enabled") and field.get("use_for_key") for field in normalized.values()):
        raise ValidationError(
            f"Question {question_index} participant fields need at least one field for key generation."
        )

    return normalized


def _normalize_field_options(value: Any, field_key: str) -> list[str]:
    defaults = CONFIGURABLE_OPTION_DEFAULTS[field_key]
    if not isinstance(value, list):
        return list(defaults)

    cleaned: list[str] = []
    for item in value:
        text = _normalize_text(item)
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned or list(defaults)


def _participant_field_options(questions: list[dict[str, Any]], field_key: str) -> list[str]:
    participant_question = next(
        (question for question in questions if question.get("type") == "participant-id"),
        None,
    )
    fields = (participant_question or {}).get("fields") or {}
    field_config = fields.get(field_key) or {}
    options = field_config.get("options")
    if isinstance(options, list) and options:
        return [opt for opt in options if isinstance(opt, str) and opt]
    return list(CONFIGURABLE_OPTION_DEFAULTS.get(field_key, []))


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


def _normalize_optional_integer(
    value: Any,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
) -> int | None:
    if value in (None, ""):
        return None
    return _normalize_integer(value, field_name=field_name, minimum=minimum, maximum=maximum)


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


def _normalize_question_type(value: Any) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        return ""
    if normalized not in ALLOWED_QUESTION_TYPES:
        raise ValidationError(f"question_type must be one of: {', '.join(sorted(ALLOWED_QUESTION_TYPES))}.")
    return normalized


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


def _optional_iso_timestamp(value: Any, field_name: str) -> str | None:
    if value in (None, ""):
        return None
    return _require_iso_timestamp(value, field_name)


def _parse_iso_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
