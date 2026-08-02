"""Read-only index and timeline access for canonical finalized sessions.

Legacy flat result directories are intentionally never scanned. They remain
untouched on disk, while this browser only exposes the immutable v3 layout:
``<study>/participants/<participant>/sessions/<UTC>__<session-id>``.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import math
from pathlib import Path, PurePosixPath
import threading
from typing import Any

from .card_summary_service import CardSummaryError, PyXdfSampleReader


DEFAULT_MAX_POINTS = 2000
MAX_MAX_POINTS = 10_000
_INDEX_CACHE: dict[str, tuple[tuple[tuple[str, int, int], ...], list[dict[str, Any]]]] = {}
_STREAM_CACHE_LOCK = threading.RLock()
_STREAM_CACHE: tuple[str, int, int, list[dict[str, Any]]] | None = None
_PUBLIC_CONTROL_FILES = {
    "submission.json",
    "result.json",
    "card-summary.json",
    "manifest.json",
    "checksums.sha256",
    "session-identity.json",
    "COMPLETE.json",
    "ATTENTION_REQUIRED.json",
}


class SessionNotFoundError(LookupError):
    pass


def list_sessions(data_dir: Path) -> list[dict[str, Any]]:
    data_root = Path(data_dir).resolve()
    signature = _directory_signature(data_root)
    cache_key = str(data_root)
    cached = _INDEX_CACHE.get(cache_key)
    if cached is not None and cached[0] == signature:
        return copy.deepcopy(cached[1])

    sessions = [
        _session_summary(data_root, session_root, result_file, payload)
        for session_root, result_file, payload in _canonical_records(data_root)
    ]
    indexed = sorted(
        sessions,
        key=lambda item: (str(item.get("saved_at") or ""), str(item["session_path"])),
        reverse=True,
    )
    _INDEX_CACHE[cache_key] = (signature, indexed)
    return copy.deepcopy(indexed)


def load_session(
    data_dir: Path,
    study_id: str,
    participant_id: str,
    *,
    session_id: str | None = None,
    session_folder: str | None = None,
) -> dict[str, Any]:
    data_root = Path(data_dir).resolve()
    session_root, result_file, payload = _select_session(
        data_root,
        study_id,
        participant_id,
        session_id=session_id,
        session_folder=session_folder,
    )
    streams = _read_merged_streams(session_root)
    stream_metadata = [_stream_metadata(session_root, stream) for stream in streams]
    return {
        **_session_summary(data_root, session_root, result_file, payload),
        "result": payload,
        "streams": stream_metadata,
        # Kept as an alias while the existing timeline component moves from
        # JSON-sidecar terminology to generic XDF streams.
        "sidecars": stream_metadata,
    }


def load_signal_samples(
    data_dir: Path,
    study_id: str,
    participant_id: str,
    sensor: str,
    *,
    session_id: str | None = None,
    session_folder: str | None = None,
    max_points: int = DEFAULT_MAX_POINTS,
) -> dict[str, Any]:
    # Validate before opening a potentially large XDF.
    if max_points < 1 or max_points > MAX_MAX_POINTS:
        raise ValueError(f"max_points must be between 1 and {MAX_MAX_POINTS}.")
    data_root = Path(data_dir).resolve()
    session_root, _result_file, payload = _select_session(
        data_root,
        study_id,
        participant_id,
        session_id=session_id,
        session_folder=session_folder,
    )
    stream_key = str(sensor or "").strip()
    matching = [stream for stream in _read_merged_streams(session_root) if str(stream.get("stream_key") or "") == stream_key]
    if not matching:
        raise SessionNotFoundError(f"No {stream_key} stream was recorded for this session.")

    stream = matching[0]
    timestamps = list(stream.get("timestamps") or [])
    rows = list(stream.get("samples") or [])
    samples = []
    for timestamp, row in zip(timestamps, rows):
        sample = _json_safe(dict(row) if isinstance(row, dict) else {"value": row})
        sample["_epoch"] = _json_safe(timestamp)
        samples.append(sample)
    downsampled = min_max_envelope(samples, max_points)
    return {
        "study_id": str(payload.get("study_id") or study_id),
        "participant_id": str(payload.get("participant_id") or participant_id),
        "session_id": str(payload.get("session_id") or ""),
        "session_folder": session_root.name,
        "stream_key": stream_key,
        "sensor": stream_key,
        "plugin_key": str(stream.get("plugin_key") or ""),
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


def _canonical_records(data_root: Path) -> list[tuple[Path, Path, dict[str, Any]]]:
    records = []
    for session_root in _canonical_session_roots(data_root):
        selected = _select_result_payload(session_root)
        if selected is not None:
            result_file, payload = selected
            records.append((session_root, result_file, payload))
    return records


def _canonical_session_roots(data_root: Path) -> list[Path]:
    roots = []
    if not data_root.is_dir():
        return roots
    for study_dir in data_root.iterdir():
        sessions_by_participant = study_dir / "participants"
        if not study_dir.is_dir() or study_dir.name.startswith("_") or not sessions_by_participant.is_dir():
            continue
        for participant_dir in sessions_by_participant.iterdir():
            sessions_dir = participant_dir / "sessions"
            if not participant_dir.is_dir() or not sessions_dir.is_dir():
                continue
            for session_root in sessions_dir.iterdir():
                if not session_root.is_dir() or session_root.name.startswith(".") or not _has_final_marker(session_root):
                    continue
                resolved = session_root.resolve()
                if resolved.is_relative_to(data_root.resolve()):
                    roots.append(resolved)
    return roots


def _select_session(
    data_root: Path,
    study_id: str,
    participant_id: str,
    *,
    session_id: str | None,
    session_folder: str | None,
) -> tuple[Path, Path, dict[str, Any]]:
    study = _required_selector(study_id, "study_id")
    participant = _required_selector(participant_id, "participant_id")
    wanted_session = str(session_id or "").strip()
    wanted_folder = str(session_folder or "").strip()
    if wanted_folder and (Path(wanted_folder).name != wanted_folder or wanted_folder in {".", ".."}):
        raise ValueError("Invalid session_folder.")

    matches = []
    for session_root, result_file, payload in _canonical_records(data_root):
        identity = _identity(session_root)
        actual_study = str(payload.get("study_id") or identity.get("study_id") or "")
        actual_participant = str(payload.get("participant_id") or identity.get("participant_id") or "")
        actual_session = str(payload.get("session_id") or identity.get("session_id") or "")
        if actual_study != study or actual_participant != participant:
            continue
        if wanted_session and actual_session != wanted_session:
            continue
        if wanted_folder and session_root.name != wanted_folder:
            continue
        matches.append((session_root, result_file, payload))
    if not matches:
        raise SessionNotFoundError("Completed canonical session not found.")
    return max(matches, key=lambda item: (_saved_at(item[0], item[1], item[2]), item[0].name))


def _session_summary(
    data_root: Path,
    session_root: Path,
    result_file: Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    identity = _identity(session_root)
    manifest = _optional_json(session_root / "manifest.json")
    marker = _marker_payload(session_root)
    answers = payload.get("answers")
    answer_details = payload.get("answer_details")
    return {
        "study_id": str(payload.get("study_id") or identity.get("study_id") or session_root.parents[3].name),
        "participant_id": str(payload.get("participant_id") or identity.get("participant_id") or session_root.parents[1].name),
        "session_id": str(payload.get("session_id") or identity.get("session_id") or session_root.name),
        "session_folder": session_root.name,
        "session_path": session_root.relative_to(data_root).as_posix(),
        "result_file": result_file.name,
        "saved_at": _saved_at(session_root, result_file, payload),
        "status": str(marker.get("status") or ("attention_required" if (session_root / "ATTENTION_REQUIRED.json").is_file() else "completed")),
        "quality_status": str(manifest.get("quality_status") or ""),
        "answers_count": (
            len(answers)
            if isinstance(answers, dict)
            else len(answer_details)
            if isinstance(answer_details, list)
            else 0
        ),
        "files": [_file_metadata(session_root, path) for path in _related_files(session_root, manifest)],
        "recovered": bool(payload.get("recovered")),
    }


def _related_files(session_root: Path, manifest: dict[str, Any]) -> list[Path]:
    files: set[Path] = set()
    for name in _PUBLIC_CONTROL_FILES:
        candidate = session_root / name
        if candidate.is_file():
            files.add(candidate)
    for item in manifest.get("artifacts", []):
        if not isinstance(item, dict):
            continue
        relative = _safe_artifact_path(item.get("path"))
        if relative is None:
            continue
        candidate = (session_root / Path(*relative.parts)).resolve()
        if candidate.is_relative_to(session_root.resolve()) and candidate.is_file():
            files.add(candidate)
    for container in (session_root / "raw", session_root / "derived"):
        if container.is_dir():
            files.update(path for path in container.rglob("*.xdf") if path.is_file())
    return sorted(files, key=lambda path: path.relative_to(session_root).as_posix())


def _read_merged_streams(session_root: Path) -> list[dict[str, Any]]:
    global _STREAM_CACHE
    merged = session_root / "derived" / "session.xdf"
    if not merged.is_file() or not merged.resolve().is_relative_to(session_root.resolve()):
        return []
    try:
        stat = merged.stat()
    except OSError:
        return []
    cache_key = str(merged.resolve())
    with _STREAM_CACHE_LOCK:
        if _STREAM_CACHE is not None and _STREAM_CACHE[:3] == (cache_key, stat.st_mtime_ns, stat.st_size):
            return _STREAM_CACHE[3]
        try:
            streams = list(PyXdfSampleReader().read_streams(merged))
        except (CardSummaryError, OSError, TypeError, ValueError) as error:
            print(f"[SESSIONS] Could not read merged XDF {merged}: {error}")
            return []
        # One session is enough: detail loads it once, then parallel lane
        # requests reuse the same parsed XDF without retaining the whole study.
        _STREAM_CACHE = (cache_key, stat.st_mtime_ns, stat.st_size, streams)
        return streams


def _stream_metadata(session_root: Path, stream: dict[str, Any]) -> dict[str, Any]:
    timestamps = list(stream.get("timestamps") or [])
    merged = session_root / "derived" / "session.xdf"
    metadata = _file_metadata(session_root, merged)
    return {
        **metadata,
        "sensor": str(stream.get("stream_key") or ""),
        "stream_key": str(stream.get("stream_key") or ""),
        "stream_name": str(stream.get("name") or ""),
        "plugin_key": str(stream.get("plugin_key") or ""),
        "sample_count": min(len(timestamps), len(list(stream.get("samples") or []))),
        "timestamp_start": _json_safe(timestamps[0]) if timestamps else None,
        "timestamp_end": _json_safe(timestamps[-1]) if timestamps else None,
        "nominal_rate_hz": _json_safe(stream.get("nominal_rate_hz")),
    }


def _select_result_payload(session_root: Path) -> tuple[Path, dict[str, Any]] | None:
    for name in ("result.json", "submission.json"):
        path = session_root / name
        if not path.is_file() or not path.resolve().is_relative_to(session_root.resolve()):
            continue
        try:
            payload = _read_json_object(path)
        except (OSError, ValueError):
            continue
        if _is_result_payload(payload):
            return path, payload
    return None


def _directory_signature(data_root: Path) -> tuple[tuple[str, int, int], ...]:
    entries = []
    if not data_root.is_dir():
        return ()
    for session_root in _canonical_session_roots(data_root):
        for path in session_root.rglob("*"):
            relative = path.relative_to(session_root)
            if not path.is_file() or "logs" in relative.parts or path.name.startswith("."):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((path.relative_to(data_root).as_posix(), stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(entries))


def _has_final_marker(session_root: Path) -> bool:
    return (session_root / "COMPLETE.json").is_file() or (session_root / "ATTENTION_REQUIRED.json").is_file()


def _marker_payload(session_root: Path) -> dict[str, Any]:
    attention = session_root / "ATTENTION_REQUIRED.json"
    return _optional_json(attention if attention.is_file() else session_root / "COMPLETE.json")


def _saved_at(session_root: Path, result_file: Path, payload: dict[str, Any]) -> str:
    marker = _marker_payload(session_root)
    return str(
        payload.get("timestamp_end")
        or payload.get("server_received_at")
        or marker.get("published_at")
        or dt.datetime.fromtimestamp(result_file.stat().st_mtime, tz=dt.timezone.utc).isoformat()
    )


def _identity(session_root: Path) -> dict[str, Any]:
    return _optional_json(session_root / "session-identity.json")


def _file_metadata(session_root: Path, path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.relative_to(session_root).as_posix(),
        "type": path.suffix.lower().lstrip("."),
        "size": stat.st_size,
        "modified_at": dt.datetime.fromtimestamp(stat.st_mtime, tz=dt.timezone.utc).isoformat(),
    }


def _safe_artifact_path(value: Any) -> PurePosixPath | None:
    normalized = str(value or "").strip().replace("\\", "/")
    relative = PurePosixPath(normalized)
    if not normalized or relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        return None
    return relative


def _required_selector(value: Any, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 256:
        raise ValueError(f"Invalid {label}.")
    return normalized


def _is_result_payload(payload: dict[str, Any]) -> bool:
    return not payload.get("sensor") and (
        isinstance(payload.get("answers"), dict) or isinstance(payload.get("answer_details"), list)
    )


def _optional_json(path: Path) -> dict[str, Any]:
    try:
        return _read_json_object(path)
    except (OSError, ValueError):
        return {}


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


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(child) for child in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
