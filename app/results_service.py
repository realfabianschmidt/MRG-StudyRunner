import datetime as dt
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any


TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def build_result_filename(study_id: str, now: dt.datetime | None = None) -> str:
    current_time = now or dt.datetime.now()
    timestamp = current_time.strftime(TIMESTAMP_FORMAT)
    safe_study_id = sanitize_identifier_for_filename(study_id)
    return f"{safe_study_id}_{timestamp}.json"


def sanitize_identifier_for_filename(value: str) -> str:
    normalized = UNSAFE_FILENAME_CHARS.sub("_", (value or "study").strip())
    normalized = normalized.strip("._-")
    if not normalized:
        return "study"
    return normalized[:80]


def save_results_payload(
    data_dir: Path,
    study_id: str,
    result_payload: dict[str, Any],
    hardware_config: dict[str, Any] | None = None,
) -> dict[str, str | None]:
    safe_study_id = sanitize_identifier_for_filename(study_id)
    participant_id = str(result_payload.get("participant_id") or "participant")
    safe_participant_id = sanitize_identifier_for_filename(participant_id)
    study_dir = data_dir / safe_study_id
    participant_dir = study_dir / safe_participant_id
    participant_dir.mkdir(parents=True, exist_ok=True)

    json_path = _build_unique_output_path(participant_dir, safe_participant_id, ".json")
    with json_path.open("w", encoding="utf-8") as file_handle:
        json.dump(result_payload, file_handle, indent=2, ensure_ascii=False)

    xdf_path = _maybe_collect_xdf(
        participant_dir=participant_dir,
        safe_participant_id=safe_participant_id,
        result_payload=result_payload,
        hardware_config=hardware_config or {},
    )

    return {
        "study_dir": str(study_dir.relative_to(data_dir.parent)),
        "participant_dir": str(participant_dir.relative_to(data_dir.parent)),
        "json_file": str(json_path.relative_to(data_dir.parent)),
        "xdf_file": str(xdf_path.relative_to(data_dir.parent)) if xdf_path else None,
    }


def _maybe_collect_xdf(
    *,
    participant_dir: Path,
    safe_participant_id: str,
    result_payload: dict[str, Any],
    hardware_config: dict[str, Any],
) -> Path | None:
    labrecorder_config = hardware_config.get("labrecorder", {})
    if not labrecorder_config.get("enabled"):
        return None

    source_dir_value = _resolve_platform_value(labrecorder_config.get("xdf_source_dir"))
    if not source_dir_value:
        return None

    source_dir = _resolve_project_path(source_dir_value, participant_dir.parent.parent)
    if not source_dir.exists() or not source_dir.is_dir():
        return None

    candidate = _find_matching_xdf(source_dir, result_payload, labrecorder_config)
    if candidate is None:
        return None

    target_path = _build_unique_output_path(participant_dir, safe_participant_id, ".xdf")
    try:
        if labrecorder_config.get("move_xdf", False):
            if candidate.resolve() != target_path.resolve():
                shutil.move(str(candidate), str(target_path))
        else:
            shutil.copy2(candidate, target_path)
    except Exception as error:
        print(f"[DATA] Could not collect XDF file: {error}")
        return None

    print(f"[DATA] XDF collected: {target_path.name}")
    return target_path


def _find_matching_xdf(
    source_dir: Path,
    result_payload: dict[str, Any],
    labrecorder_config: dict[str, Any],
) -> Path | None:
    candidates = sorted(source_dir.glob("*.xdf"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        return None

    timestamp_start = _parse_iso_timestamp(result_payload.get("timestamp_start"))
    timestamp_end = _parse_iso_timestamp(result_payload.get("timestamp_end"))
    if timestamp_start is None or timestamp_end is None:
        return candidates[0]

    lookback_minutes = int(labrecorder_config.get("lookback_minutes", 120))
    lookahead_minutes = int(labrecorder_config.get("lookahead_minutes", 120))
    window_start = timestamp_start - dt.timedelta(minutes=max(0, lookback_minutes))
    window_end = timestamp_end + dt.timedelta(minutes=max(0, lookahead_minutes))

    matching = []
    for candidate in candidates:
        modified_time = dt.datetime.fromtimestamp(candidate.stat().st_mtime, tz=dt.timezone.utc)
        if window_start <= modified_time <= window_end:
            matching.append(candidate)

    if matching:
        return matching[0]

    return candidates[0] if len(candidates) == 1 else None


def _parse_iso_timestamp(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        normalized = str(value).replace("Z", "+00:00")
        parsed = dt.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except ValueError:
        return None


def _platform_keys() -> tuple[str, ...]:
    if os.name == "nt":
        return ("windows", "win32", "default")
    if sys.platform == "darwin":
        return ("macos", "mac", "darwin", "default")
    return ("linux", "posix", "default")


def _resolve_platform_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value

    for key in _platform_keys():
        if key in value and value.get(key) not in (None, ""):
            return value.get(key)

    for key in ("default", "windows", "macos", "linux"):
        if key in value and value.get(key) not in (None, ""):
            return value.get(key)

    return None


def _resolve_project_path(value: Any, base_dir: Path) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def build_biosignal_summary(hardware_config: dict[str, Any], saved_output: dict[str, Any]) -> dict[str, Any]:
    """Build a lightweight biosignal metadata summary for Notion upload."""
    summary: dict[str, Any] = {}

    if hardware_config.get("brainbit", {}).get("enabled"):
        summary["brainbit"] = {
            "active": True,
            "xdf_path": saved_output.get("xdf_file"),
        }

    if hardware_config.get("mini_radar", {}).get("enabled"):
        try:
            from .integrations import mini_radar_adapter
            summary["mini_radar"] = {"active": True, **mini_radar_adapter.get_status()}
        except Exception:
            summary["mini_radar"] = {"active": True}

    if hardware_config.get("camera_emotion", {}).get("enabled"):
        try:
            from .integrations import camera_affect_adapter
            summary["camera_emotion"] = {"active": True, **camera_affect_adapter.get_status()}
        except Exception:
            summary["camera_emotion"] = {"active": True}

    return summary


def build_answer_details(
    result_payload: dict[str, Any],
    config_data: dict[str, Any],
    hardware_config: dict[str, Any],
) -> list[dict[str, Any]]:
    questions = config_data.get("questions", [])
    answers = result_payload.get("answers", {})
    participant_id = result_payload.get("participant_id")
    answer_events = {
        int(event.get("question_index")): event
        for event in (result_payload.get("answer_events") or [])
        if isinstance(event, dict) and str(event.get("question_index", "")).isdigit()
    }

    entries: list[dict[str, Any]] = []
    for question_index, question in enumerate(questions):
        question_type = question.get("type")
        if question_type in {"stimulus", "finish"}:
            continue

        answer_key = None if question_type == "participant-id" else f"q{question_index}"
        answer_value = participant_id if question_type == "participant-id" else answers.get(answer_key)
        if answer_value is None and question_type != "participant-id":
            continue

        event = answer_events.get(question_index, {})
        entries.append(
            {
                "question_index": question_index,
                "question_number": question_index + 1,
                "question_key": answer_key or "participant_id",
                "question_type": question_type,
                "question_prompt": _question_prompt(question),
                "answer": answer_value,
                "shown_at": event.get("shown_at") or result_payload.get("timestamp_start"),
                "answered_at": event.get("answered_at") or result_payload.get("timestamp_end"),
            }
        )

    entries.sort(key=lambda item: item.get("answered_at") or "")
    previous_answered_at = result_payload.get("timestamp_start")
    for entry in entries:
        interval_start = previous_answered_at or result_payload.get("timestamp_start")
        interval_end = entry.get("answered_at") or result_payload.get("timestamp_end")
        delta_seconds = _seconds_between(interval_start, interval_end)
        entry["previous_answered_at"] = previous_answered_at
        entry["seconds_since_previous_answer"] = delta_seconds
        entry["biosignal_interval"] = build_interval_biosignal_summary(
            hardware_config,
            interval_start,
            interval_end,
        )
        previous_answered_at = interval_end

    return entries


def build_interval_biosignal_summary(
    hardware_config: dict[str, Any],
    interval_start: Any,
    interval_end: Any,
) -> dict[str, Any]:
    start_dt = _parse_iso_timestamp(interval_start)
    end_dt = _parse_iso_timestamp(interval_end)
    if start_dt is None or end_dt is None:
        return _empty_interval_biosignals()

    start_epoch = start_dt.timestamp()
    end_epoch = end_dt.timestamp()
    if end_epoch < start_epoch:
        start_epoch, end_epoch = end_epoch, start_epoch

    summary = _empty_interval_biosignals()

    if hardware_config.get("brainbit", {}).get("enabled"):
        try:
            from .integrations import brainbit_adapter

            summary["brainbit"] = brainbit_adapter.get_interval_summary(start_epoch, end_epoch)
        except Exception:
            summary["brainbit"] = {"available": False}

    if hardware_config.get("mini_radar", {}).get("enabled"):
        try:
            from .integrations import mini_radar_adapter

            summary["mini_radar"] = mini_radar_adapter.get_interval_summary(start_epoch, end_epoch)
        except Exception:
            summary["mini_radar"] = {"available": False}

    if hardware_config.get("camera_emotion", {}).get("enabled"):
        try:
            from .integrations import camera_affect_adapter

            summary["camera_emotion"] = camera_affect_adapter.get_interval_summary(start_epoch, end_epoch)
        except Exception:
            summary["camera_emotion"] = {"available": False}

    return summary


def _empty_interval_biosignals() -> dict[str, Any]:
    return {
        "brainbit": {"available": False},
        "mini_radar": {"available": False},
        "camera_emotion": {"available": False},
    }


def _question_prompt(question: dict[str, Any]) -> str:
    return str(
        question.get("prompt")
        or question.get("title")
        or question.get("subtitle")
        or ""
    ).strip()


def _seconds_between(start_value: Any, end_value: Any) -> float | None:
    start_dt = _parse_iso_timestamp(start_value)
    end_dt = _parse_iso_timestamp(end_value)
    if start_dt is None or end_dt is None:
        return None
    return round((end_dt - start_dt).total_seconds(), 3)


def _build_unique_output_path(participant_dir: Path, safe_participant_id: str, suffix: str) -> Path:
    base_path = participant_dir / f"{safe_participant_id}{suffix}"
    if not base_path.exists():
        return base_path

    counter = 2
    while True:
        candidate = participant_dir / f"{safe_participant_id}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
