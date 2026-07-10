import datetime as dt
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from study_runner.integrations.plugin_api import IntegrationContext
from study_runner.integrations.registry import (
    build_context,
    build_interval_summary as build_plugin_interval_summary,
    export_interval_sidecars,
)
from .atomic_io import atomic_write_json


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
    context: IntegrationContext | None = None,
) -> dict[str, str | None]:
    safe_study_id = sanitize_identifier_for_filename(study_id)
    participant_id = str(result_payload.get("participant_id") or "participant")
    safe_participant_id = sanitize_identifier_for_filename(participant_id)
    study_dir = data_dir / safe_study_id
    participant_dir = study_dir / safe_participant_id
    participant_dir.mkdir(parents=True, exist_ok=True)

    json_path = _build_unique_output_path(participant_dir, safe_participant_id, ".json")
    atomic_write_json(json_path, result_payload)

    xdf_path = _maybe_collect_xdf(
        participant_dir=participant_dir,
        safe_participant_id=safe_participant_id,
        result_payload=result_payload,
        hardware_config=hardware_config or {},
    )
    sidecar_paths = _maybe_write_biosignal_sidecars(
        participant_dir=participant_dir,
        safe_participant_id=safe_participant_id,
        result_payload=result_payload,
        hardware_config=hardware_config or {},
        context=context or _context_from_hardware_config(data_dir, hardware_config or {}),
    )

    output = {
        "study_dir": study_dir.relative_to(data_dir.parent).as_posix(),
        "participant_dir": participant_dir.relative_to(data_dir.parent).as_posix(),
        "json_file": json_path.relative_to(data_dir.parent).as_posix(),
        "xdf_file": xdf_path.relative_to(data_dir.parent).as_posix() if xdf_path else None,
        "mr60_file": None,
        "brainbit_file": None,
    }
    for output_key, sidecar_path in sidecar_paths.items():
        output[output_key] = sidecar_path.relative_to(data_dir.parent).as_posix()
    return output


def _maybe_write_biosignal_sidecars(
    *,
    participant_dir: Path,
    safe_participant_id: str,
    result_payload: dict[str, Any],
    hardware_config: dict[str, Any],
    context: IntegrationContext,
) -> dict[str, Path]:
    start_dt = _parse_iso_timestamp(result_payload.get("timestamp_start"))
    end_dt = _parse_iso_timestamp(result_payload.get("timestamp_end"))
    if start_dt is None or end_dt is None:
        return {}

    # Session boundaries come from the tablet clock; shift them onto the
    # server clock (which sensor samples use) when the offset is known.
    offset_seconds = _epoch_ms_to_seconds(result_payload.get("client_clock_offset_ms"), allow_negative=True) or 0.0
    start_epoch = start_dt.timestamp() + offset_seconds
    end_epoch = end_dt.timestamp() + offset_seconds
    if end_epoch < start_epoch:
        start_epoch, end_epoch = end_epoch, start_epoch

    written: dict[str, Path] = {}
    for export in export_interval_sidecars(context, start_epoch, end_epoch):
        output_key = str(export.get("output_key") or f"{export.get('plugin_key')}_file")
        sidecar_path = _write_signal_sidecar(
            participant_dir,
            f"{safe_participant_id}_{export['filename_suffix']}",
            str(export["sensor"]),
            result_payload,
            export["samples"],
        )
        written[output_key] = sidecar_path

    return written



def _write_signal_sidecar(
    participant_dir: Path,
    base_name: str,
    sensor: str,
    result_payload: dict[str, Any],
    samples: list[dict[str, Any]],
) -> Path:
    sidecar_path = _build_unique_output_path(participant_dir, base_name, ".json")
    payload = {
        "sensor": sensor,
        "raw_format": "xdf_primary_json_sidecar",
        "study_id": result_payload.get("study_id"),
        "participant_id": result_payload.get("participant_id"),
        "timestamp_start": result_payload.get("timestamp_start"),
        "timestamp_end": result_payload.get("timestamp_end"),
        "card_events": result_payload.get("card_events") or [],
        "sample_count": len(samples),
        "samples": samples,
    }
    atomic_write_json(sidecar_path, payload)
    print(f"[DATA] {sensor} sidecar written: {sidecar_path.name}")
    return sidecar_path

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

    source_dir = _resolve_project_path(source_dir_value, _project_root())
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


def build_biosignal_summary(
    hardware_config: dict[str, Any],
    saved_output: dict[str, Any],
    context: IntegrationContext | None = None,
) -> dict[str, Any]:
    """Build lightweight biosignal metadata for Notion upload."""
    runtime_context = context or _context_from_hardware_config(
        _project_root() / "saved_results",
        hardware_config,
    )
    summary: dict[str, Any] = {}

    for key in ("brainbit", "mini_radar", "camera_emotion"):
        config = hardware_config.get(key, {})
        if not isinstance(config, dict) or not config.get("enabled"):
            continue
        summary[key] = {"active": True}
        if key == "brainbit":
            summary[key]["xdf_path"] = saved_output.get("xdf_file")

    interval_status = build_plugin_interval_summary(runtime_context, 0.0, time_now_epoch())
    for key, value in interval_status.items():
        if key in summary:
            summary[key]["latest_interval_shape"] = value

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
    card_events = {
        int(event.get("question_index")): event
        for event in (result_payload.get("card_events") or [])
        if isinstance(event, dict) and str(event.get("question_index", "")).isdigit()
    }

    entries: list[dict[str, Any]] = []
    for question_index, question in enumerate(questions):
        question_type = question.get("type")
        if question_type == "finish":
            continue

        card_event = card_events.get(question_index, {})
        answer_event = answer_events.get(question_index, {})
        event = {**card_event, **{key: value for key, value in answer_event.items() if value not in (None, "")}}
        is_stimulus = question_type == "stimulus"
        answer_key = None if question_type in {"participant-id", "stimulus"} else f"q{question_index}"
        if is_stimulus:
            answer_value = "stimulus"
            interval_start = event.get("active_started_at") or event.get("shown_at") or result_payload.get("timestamp_start")
            interval_end = (
                event.get("active_ended_at")
                or event.get("completed_at")
                or result_payload.get("timestamp_end")
            )
            interval_kind = "stimulus_active"
        else:
            answer_value = participant_id if question_type == "participant-id" else answers.get(answer_key)
            if answer_value is None and question_type != "participant-id":
                continue
            interval_start = event.get("shown_at") or result_payload.get("timestamp_start")
            interval_end = (
                event.get("answered_at")
                or event.get("completed_at")
                or result_payload.get("timestamp_end")
            )
            interval_kind = "question_visible"

        interval_seconds = _seconds_between(interval_start, interval_end)
        start_epoch, end_epoch, timing_source = _resolve_interval_epochs(
            event=event,
            result_payload=result_payload,
            is_stimulus=is_stimulus,
            interval_start_iso=interval_start,
            interval_end_iso=interval_end,
        )
        entries.append(
            {
                "question_index": question_index,
                "question_number": question_index + 1,
                "question_key": answer_key or ("stimulus" if is_stimulus else "participant_id"),
                "question_type": question_type,
                "question_prompt": _question_prompt(question),
                "answer": answer_value,
                "shown_at": event.get("shown_at") or result_payload.get("timestamp_start"),
                "answered_at": event.get("answered_at") or event.get("completed_at") or result_payload.get("timestamp_end"),
                "active_started_at": event.get("active_started_at"),
                "active_ended_at": event.get("active_ended_at"),
                "server_start_received_at": event.get("server_start_received_at"),
                "server_stop_received_at": event.get("server_stop_received_at"),
                "server_start_received_epoch_ms": event.get("server_start_received_epoch_ms"),
                "server_stop_received_epoch_ms": event.get("server_stop_received_epoch_ms"),
                "client_start_trigger_epoch_ms": event.get("client_start_trigger_epoch_ms"),
                "client_stop_trigger_epoch_ms": event.get("client_stop_trigger_epoch_ms"),
                "start_marker": event.get("start_marker"),
                "stop_marker": event.get("stop_marker"),
                "biosignal_interval_start": interval_start,
                "biosignal_interval_end": interval_end,
                "biosignal_interval_kind": interval_kind,
                "biosignal_interval_timing_source": timing_source,
                "interval_seconds": interval_seconds,
                "seconds_since_previous_answer": interval_seconds,
                "biosignal_interval": _interval_summary_from_epochs(
                    hardware_config,
                    start_epoch,
                    end_epoch,
                ),
            }
        )

    return entries


def _resolve_interval_epochs(
    *,
    event: dict[str, Any],
    result_payload: dict[str, Any],
    is_stimulus: bool,
    interval_start_iso: Any,
    interval_end_iso: Any,
) -> tuple[float | None, float | None, str]:
    """Pick the best available clock for slicing sensor samples per card.

    Sensor samples carry server timestamps, but card timestamps come from
    the tablet. Preference order:
    1. server-clock epochs recorded by the client at the exact moment
       (trigger epochs, marker receipts, shown/answered epochs),
    2. client ISO timestamps shifted by the submitted clock offset,
    3. raw client ISO timestamps (legacy payloads without clock sync).
    """
    if is_stimulus:
        start_epoch = _epoch_ms_to_seconds(
            event.get("client_start_trigger_epoch_ms")
            or event.get("server_start_received_epoch_ms")
        )
        end_epoch = _epoch_ms_to_seconds(
            event.get("client_stop_trigger_epoch_ms")
            or event.get("server_stop_received_epoch_ms")
        )
    else:
        start_epoch = _epoch_ms_to_seconds(event.get("shown_at_server_epoch_ms"))
        end_epoch = _epoch_ms_to_seconds(event.get("answered_at_server_epoch_ms"))
    if start_epoch is not None and end_epoch is not None:
        return start_epoch, end_epoch, "server_clock"

    start_dt = _parse_iso_timestamp(interval_start_iso)
    end_dt = _parse_iso_timestamp(interval_end_iso)
    if start_dt is None or end_dt is None:
        return None, None, "unavailable"
    start_iso_epoch = start_dt.timestamp()
    end_iso_epoch = end_dt.timestamp()

    offset_seconds = _epoch_ms_to_seconds(result_payload.get("client_clock_offset_ms"), allow_negative=True)
    if offset_seconds is not None:
        return start_iso_epoch + offset_seconds, end_iso_epoch + offset_seconds, "client_clock_plus_offset"
    return start_iso_epoch, end_iso_epoch, "client_clock"


def _epoch_ms_to_seconds(value: Any, *, allow_negative: bool = False) -> float | None:
    try:
        epoch_ms = float(value)
    except (TypeError, ValueError):
        return None
    if not allow_negative and epoch_ms <= 0:
        return None
    return epoch_ms / 1000.0


def _interval_summary_from_epochs(
    hardware_config: dict[str, Any],
    start_epoch: float | None,
    end_epoch: float | None,
    context: IntegrationContext | None = None,
) -> dict[str, Any]:
    if start_epoch is None or end_epoch is None:
        return _empty_interval_biosignals()
    if end_epoch < start_epoch:
        start_epoch, end_epoch = end_epoch, start_epoch
    runtime_context = context or _context_from_hardware_config(
        _project_root() / "saved_results",
        hardware_config,
    )
    return build_plugin_interval_summary(runtime_context, start_epoch, end_epoch)


def build_interval_biosignal_summary(
    hardware_config: dict[str, Any],
    interval_start: Any,
    interval_end: Any,
    context: IntegrationContext | None = None,
) -> dict[str, Any]:
    start_dt = _parse_iso_timestamp(interval_start)
    end_dt = _parse_iso_timestamp(interval_end)
    if start_dt is None or end_dt is None:
        return _empty_interval_biosignals()
    return _interval_summary_from_epochs(
        hardware_config,
        start_dt.timestamp(),
        end_dt.timestamp(),
        context=context,
    )

def _empty_interval_biosignals() -> dict[str, Any]:
    return {
        "brainbit": {"available": False},
        "mini_radar": {"available": False},
        "camera_emotion": {"available": False},
    }




def _context_from_hardware_config(data_dir: Path, hardware_config: dict[str, Any]) -> IntegrationContext:
    base_dir = _project_root()
    return build_context(
        base_dir=base_dir,
        data_dir=data_dir,
        hardware_config=hardware_config,
        local_secrets={},
        local_secrets_file=base_dir / "settings" / "local_secrets.json",
    )


def time_now_epoch() -> float:
    return dt.datetime.now(dt.timezone.utc).timestamp()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


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



