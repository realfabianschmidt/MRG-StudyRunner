"""Read-only index and timeline access for completed study sessions."""
from __future__ import annotations

import copy
import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

from .results_service import sanitize_identifier_for_filename


DEFAULT_MAX_POINTS = 2000
MAX_MAX_POINTS = 10_000
_INDEX_CACHE: dict[str, tuple[tuple[tuple[str, int, int], ...], list[dict[str, Any]]]] = {}


class SessionNotFoundError(LookupError):
    pass


def list_sessions(data_dir: Path) -> list[dict[str, Any]]:
    data_root = Path(data_dir).resolve()
    signature = _directory_signature(data_root)
    cache_key = str(data_root)
    cached = _INDEX_CACHE.get(cache_key)
    if cached is not None and cached[0] == signature:
        return copy.deepcopy(cached[1])

    sessions: list[dict[str, Any]] = []
    for result_file in _result_files(data_root):
        try:
            payload = _read_json_object(result_file)
        except (OSError, ValueError):
            continue
        related_files = _related_files(result_file, payload)
        sessions.append(_session_summary(result_file, payload, related_files))
    indexed = sorted(
        sessions,
        key=lambda item: (str(item.get("saved_at") or ""), str(item["result_file"])),
        reverse=True,
    )
    _INDEX_CACHE[cache_key] = (signature, indexed)
    return copy.deepcopy(indexed)


def load_session(
    data_dir: Path,
    study_id: str,
    participant_id: str,
    *,
    result_file: str | None = None,
) -> dict[str, Any]:
    participant_dir = _participant_dir(data_dir, study_id, participant_id)
    selected_file = _select_result_file(participant_dir, result_file)
    payload = _read_json_object(selected_file)
    related_files = _related_files(selected_file, payload)
    sidecars = [
        metadata
        for path in related_files
        if (metadata := _sidecar_metadata(path)) is not None
    ]
    return {
        **_session_summary(selected_file, payload, related_files),
        "result": payload,
        "sidecars": sidecars,
    }


def load_signal_samples(
    data_dir: Path,
    study_id: str,
    participant_id: str,
    sensor: str,
    *,
    result_file: str | None = None,
    max_points: int = DEFAULT_MAX_POINTS,
) -> dict[str, Any]:
    participant_dir = _participant_dir(data_dir, study_id, participant_id)
    selected_file = _select_result_file(participant_dir, result_file)
    result_payload = _read_json_object(selected_file)

    matching_sidecars: list[tuple[Path, dict[str, Any]]] = []
    for path in _related_files(selected_file, result_payload):
        try:
            payload = _read_json_object(path)
        except (OSError, ValueError):
            continue
        if payload.get("sensor") == sensor and isinstance(payload.get("samples"), list):
            matching_sidecars.append((path, payload))
    if not matching_sidecars:
        raise SessionNotFoundError(f"No {sensor} signals were recorded for this session.")

    sidecar_file, sidecar = max(
        matching_sidecars,
        key=lambda item: item[0].stat().st_mtime_ns,
    )
    samples = [sample for sample in sidecar["samples"] if isinstance(sample, dict)]
    downsampled = min_max_envelope(samples, max_points)
    return {
        "study_id": study_id,
        "participant_id": participant_id,
        "result_file": selected_file.name,
        "sensor": sensor,
        "sidecar_file": sidecar_file.name,
        "sample_count": len(samples),
        **downsampled,
    }


def min_max_envelope(samples: list[dict[str, Any]], max_points: int) -> dict[str, Any]:
    if max_points < 1 or max_points > MAX_MAX_POINTS:
        raise ValueError(f"max_points must be between 1 and {MAX_MAX_POINTS}.")
    if len(samples) <= max_points:
        return {"mode": "raw", "points": samples}

    bucket_size = len(samples) / max_points
    points: list[dict[str, Any]] = []
    for bucket_index in range(max_points):
        start = math.floor(bucket_index * bucket_size)
        end = math.floor((bucket_index + 1) * bucket_size)
        bucket = samples[start : max(start + 1, end)]
        numeric_values: dict[str, list[float]] = {}
        for sample in bucket:
            for key, value in _flatten_numeric(sample).items():
                numeric_values.setdefault(key, []).append(value)
        points.append(
            {
                "start_epoch": _sample_epoch(bucket[0]),
                "end_epoch": _sample_epoch(bucket[-1]),
                "sample_count": len(bucket),
                "min": {key: min(values) for key, values in numeric_values.items()},
                "max": {key: max(values) for key, values in numeric_values.items()},
            }
        )
    return {"mode": "min_max_envelope", "points": points}


def _result_files(data_dir: Path) -> list[Path]:
    if not data_dir.is_dir():
        return []
    candidates: list[Path] = []
    for study_dir in data_dir.iterdir():
        if not study_dir.is_dir() or study_dir.name.startswith("_"):
            continue
        for participant_dir in study_dir.iterdir():
            if not participant_dir.is_dir() or participant_dir.name.startswith("_"):
                continue
            for path in participant_dir.glob("*.json"):
                try:
                    payload = _read_json_object(path)
                except (OSError, ValueError):
                    continue
                if _is_result_payload(payload):
                    candidates.append(path)
    return candidates


def _directory_signature(data_dir: Path) -> tuple[tuple[str, int, int], ...]:
    if not data_dir.is_dir():
        return ()
    entries: list[tuple[str, int, int]] = []
    for study_dir in data_dir.iterdir():
        if not study_dir.is_dir() or study_dir.name.startswith("_"):
            continue
        for participant_dir in study_dir.iterdir():
            if not participant_dir.is_dir() or participant_dir.name.startswith("_"):
                continue
            for path in participant_dir.iterdir():
                if not path.is_file() or path.suffix.lower() not in {".json", ".xdf"}:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                entries.append(
                    (
                        path.relative_to(data_dir).as_posix(),
                        stat.st_mtime_ns,
                        stat.st_size,
                    )
                )
    return tuple(sorted(entries))


def _participant_dir(data_dir: Path, study_id: str, participant_id: str) -> Path:
    for value, label in ((study_id, "study_id"), (participant_id, "participant_id")):
        if sanitize_identifier_for_filename(value) != value:
            raise ValueError(f"Invalid {label}.")
    participant_dir = Path(data_dir) / study_id / participant_id
    if not participant_dir.is_dir():
        raise SessionNotFoundError("Completed session not found.")
    return participant_dir


def _select_result_file(participant_dir: Path, result_file: str | None) -> Path:
    if result_file:
        if Path(result_file).name != result_file or not result_file.endswith(".json"):
            raise ValueError("Invalid result_file.")
        candidate = participant_dir / result_file
        if not candidate.is_file():
            raise SessionNotFoundError("Completed session not found.")
        try:
            payload = _read_json_object(candidate)
        except (OSError, ValueError) as error:
            raise SessionNotFoundError("Completed session not found.") from error
        if not _is_result_payload(payload):
            raise SessionNotFoundError("Completed session not found.")
        return candidate

    candidates = []
    for candidate in participant_dir.glob("*.json"):
        try:
            payload = _read_json_object(candidate)
        except (OSError, ValueError):
            continue
        if _is_result_payload(payload):
            candidates.append(candidate)
    if not candidates:
        raise SessionNotFoundError("Completed session not found.")
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def _session_summary(
    result_file: Path,
    payload: dict[str, Any],
    related_files: list[Path],
) -> dict[str, Any]:
    saved_at = (
        payload.get("timestamp_end")
        or payload.get("server_received_at")
        or dt.datetime.fromtimestamp(
            result_file.stat().st_mtime,
            tz=dt.timezone.utc,
        ).isoformat()
    )
    answers = payload.get("answers")
    answer_details = payload.get("answer_details")
    return {
        "study_id": str(payload.get("study_id") or result_file.parent.parent.name),
        "participant_id": str(payload.get("participant_id") or result_file.parent.name),
        "session_id": str(payload.get("session_id") or result_file.stem),
        "result_file": result_file.name,
        "saved_at": saved_at,
        "answers_count": (
            len(answers)
            if isinstance(answers, dict)
            else len(answer_details)
            if isinstance(answer_details, list)
            else 0
        ),
        "files": [_file_metadata(path) for path in related_files],
        "recovered": bool(payload.get("recovered")),
    }


def _related_files(result_file: Path, result_payload: dict[str, Any]) -> list[Path]:
    related = [result_file]
    timestamp_start = result_payload.get("timestamp_start")
    timestamp_end = result_payload.get("timestamp_end")
    session_id = result_payload.get("session_id")
    for path in result_file.parent.iterdir():
        if not path.is_file() or path == result_file:
            continue
        if path.suffix.lower() == ".json":
            try:
                payload = _read_json_object(path)
            except (OSError, ValueError):
                continue
            if not payload.get("sensor"):
                continue
            same_session = bool(session_id and payload.get("session_id") == session_id)
            same_interval = (
                payload.get("timestamp_start") == timestamp_start
                and payload.get("timestamp_end") == timestamp_end
            )
            if same_session or same_interval:
                related.append(path)
        elif path.suffix.lower() == ".xdf" and _filename_generation(path) == _filename_generation(result_file):
            related.append(path)
    return sorted(related, key=lambda path: (path != result_file, path.name))


def _sidecar_metadata(path: Path) -> dict[str, Any] | None:
    if path.suffix.lower() != ".json":
        return None
    try:
        payload = _read_json_object(path)
    except (OSError, ValueError):
        return None
    if not payload.get("sensor"):
        return None
    return {
        **_file_metadata(path),
        "sensor": payload.get("sensor"),
        "sample_count": int(payload.get("sample_count") or 0),
        "timestamp_start": payload.get("timestamp_start"),
        "timestamp_end": payload.get("timestamp_end"),
    }


def _file_metadata(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "type": path.suffix.lower().lstrip("."),
        "size": stat.st_size,
        "modified_at": dt.datetime.fromtimestamp(
            stat.st_mtime,
            tz=dt.timezone.utc,
        ).isoformat(),
    }


def _filename_generation(path: Path) -> int:
    suffix = path.stem.rsplit("_", 1)[-1]
    return int(suffix) if suffix.isdigit() else 1


def _is_result_payload(payload: dict[str, Any]) -> bool:
    return (
        not payload.get("sensor")
        and (
            isinstance(payload.get("answers"), dict)
            or isinstance(payload.get("answer_details"), list)
        )
    )


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"Invalid JSON file: {path.name}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path.name}")
    return payload


def _flatten_numeric(value: Any, prefix: str = "") -> dict[str, float]:
    flattened: dict[str, float] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(_flatten_numeric(child, child_prefix))
    elif isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
        flattened[prefix] = float(value)
    return flattened


def _sample_epoch(sample: dict[str, Any]) -> float | None:
    for key in ("server_received_epoch", "_epoch", "processed_epoch"):
        value = sample.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None
