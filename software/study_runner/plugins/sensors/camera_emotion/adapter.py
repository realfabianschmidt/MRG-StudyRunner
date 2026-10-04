"""
Camera affect adapter for tablet selfie-camera snapshots (sensor data contract).

Browser snapshots are forwarded to the Emotion Worker (local or remote), which
returns the face/emotion analysis. Without a reachable worker the result says
so and marks the measurements unavailable -- nothing is guessed.

Each analysed frame is one sample in ``emotion`` and one in ``face_quality``,
published through the shared ``SensorStreams``. The arrival time is taken the
moment a frame arrives, before the analysis. The timestamp goes back to the
tablet's capture time (``source_epoch_ms``) when that is plausible (0-60 s
earlier); ``correction_ms`` records how far, so the arrival time can always be
rebuilt from the XDF.
"""
from __future__ import annotations

import base64
import math
import time
from collections import deque
from threading import Lock
from typing import Any

from study_runner.plugin_framework.adapter_utils import set_state, timestamp
from study_runner.plugin_framework.history_buffer import history_maxlen, max_gap_seconds, samples_in_interval, truncation_info
from study_runner.plugin_framework.sensor_streams import SensorStreams


_state_lock = Lock()
_config: dict[str, Any] = {}
# Browser capture is intentionally throttled to 1 Hz by default to avoid tablet
# and network backpressure during live runs.
MIN_SNAPSHOT_INTERVAL_MS = 1000
DEFAULT_SNAPSHOT_INTERVAL_MS = 1000
_history: deque[dict[str, Any]] = deque(maxlen=history_maxlen(1.0))
_sequence_state: dict[str, dict[str, int]] = {}
_MAX_SEQUENCE_SOURCES = 256
_preview_state: dict[str, Any] = {
    "available": False,
    "active": False,
    "last_message": "No tablet camera live frame received yet.",
}
_latest_state: dict[str, Any] = {
    "status": "not_configured",
    "latest": {},
    "last_message": "Camera affect adapter has not been configured.",
}

_EMOTIONS = ("angry", "disgust", "fear", "happy", "sad", "surprise", "neutral", "unknown")
# Streams, channels, units and the LSL names come from manifest.json only.
_streams = SensorStreams.for_plugin(__file__)
STREAM_CONTRACTS = _streams.declared_contracts()
# The tablet's capture time is used when it is at most this much earlier than
# the arrival here; anything else is not plausible and keeps the arrival time.
MAX_CAPTURE_AGE_SECONDS = 60.0


def initialize(
    *,
    enabled: bool = False,
    snapshot_interval_ms: int = DEFAULT_SNAPSHOT_INTERVAL_MS,
    store_raw_frames: bool = False,
    overlay_enabled: bool = True,
    worker_mode: str = "local_worker",
    emotion_worker_url: str = "",
    emotion_worker_timeout_ms: int = 5000,
    auto_install: bool = True,
    lsl_enabled: bool = False,
    lsl_auto_install: bool = True,
    lsl_stream_name: str = "CameraEmotion",
) -> None:
    # lsl_stream_name is kept for old configurations; the stream names are
    # fixed in the manifest so the recording and TouchDesigner find them.
    """Configure camera affect analysis and optional LSL output."""
    global _config

    _config = {
        "enabled": bool(enabled),
        "snapshot_interval_ms": max(MIN_SNAPSHOT_INTERVAL_MS, int(snapshot_interval_ms)),
        "store_raw_frames": bool(store_raw_frames),
        "overlay_enabled": bool(overlay_enabled),
        "worker_mode": worker_mode or "local_worker",
        "emotion_worker_url": (emotion_worker_url or "").rstrip("/"),
        "emotion_worker_timeout_ms": max(500, int(emotion_worker_timeout_ms)),
        "auto_install": bool(auto_install),
        "lsl_enabled": bool(lsl_enabled),
        "lsl_auto_install": bool(lsl_auto_install),
        "lsl_stream_name": lsl_stream_name or "CameraEmotion",
    }

    _set_state(
        {
            "status": "configured" if enabled else "disabled",
            "enabled": bool(enabled),
            "last_message": "Camera affect adapter configured.",
        }
    )

    _streams.configure(auto_install=bool(lsl_auto_install))
    if _config["enabled"] and _config["lsl_enabled"] and not _streams.open_all():
        _set_state({"status": "failed", "last_message": "Camera LSL outlets could not be opened."})


def start() -> dict[str, Any]:
    # A new run starts with no samples or frame counters from an earlier participant.
    _history.clear()
    _sequence_state.clear()
    if not _config:
        _set_state({"status": "not_configured", "last_message": "Camera affect adapter is not configured."})
    elif not _config.get("enabled"):
        _set_state({"status": "disabled", "last_message": "Camera affect analysis is disabled."})
    elif _config.get("lsl_enabled") and not _streams.open_all():
        _set_state({"status": "failed", "last_message": "Camera LSL outlets could not be opened."})
    else:
        _set_state({"status": "ready", "last_message": "Camera affect analysis is ready."})
    return get_status()


def stop() -> dict[str, Any]:
    set_preview_active(False)
    _sequence_state.clear()
    _set_state({"status": "stopped", "last_message": "Camera affect analysis stopped."})
    return get_status()


def process_frame(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept one browser snapshot and return the current conservative analysis result."""
    if not _config or not _config.get("enabled", False):
        _set_state({"status": "disabled", "last_message": "Camera affect frame ignored because analysis is disabled."})
        return {"accepted": False, "reason": "disabled", **get_status()}

    arrival = _streams.now()
    arrival_epoch = time.time()
    received_at = timestamp()
    sequence_diagnostics = _sequence_diagnostics(payload)
    if sequence_diagnostics["sequence_status"] in {"duplicate", "out_of_order"}:
        reason = f"{sequence_diagnostics['sequence_status']}_sequence"
        rejected = {
            "accepted": False,
            "reason": reason,
            "sequence_number": payload.get("sequence_number"),
            "source_epoch_ms": payload.get("source_epoch_ms", payload.get("source_timestamp")),
            "server_received_at": received_at,
            "sequence_diagnostics": sequence_diagnostics,
            "drop_count": sequence_diagnostics["missing_count"],
        }
        _set_state(
            {
                "status": "degraded",
                "last_rejected": rejected,
                "last_activity_at": received_at,
                "last_message": f"Camera frame rejected: {reason}.",
            }
        )
        return rejected

    frame_info = _extract_frame_info(payload)
    analysis = _analyze_frame(payload)
    if payload.get("preview") is True:
        result = {
            "accepted": True,
            "preview": True,
            "participant_id": str(payload.get("participant_id") or "").strip(),
            "study_id": str(payload.get("study_id") or "").strip(),
            "question_index": payload.get("question_index"),
            "active_phase": False,
            "client_captured_at": payload.get("client_captured_at") or payload.get("client_timestamp"),
            "source_monotonic_ms": payload.get("source_monotonic_ms"),
            "source_epoch_ms": payload.get("source_epoch_ms"),
            "clock_sync_id": payload.get("clock_sync_id"),
            "clock_sync_age_ms": payload.get("clock_sync_age_ms"),
            "clock_sync_rtt_ms": payload.get("clock_sync_rtt_ms"),
            "time_source": payload.get("time_source"),
            "server_received_at": received_at,
            "processed_at": timestamp(),
            "sequence_number": payload.get("sequence_number"),
            "sequence_diagnostics": sequence_diagnostics,
            "drop_count": sequence_diagnostics["missing_count"],
            "frame": frame_info,
            "analysis": analysis,
        }
        _set_preview_state(result, payload)
        return result

    result = {
        "accepted": True,
        "participant_id": str(payload.get("participant_id") or "").strip(),
        "study_id": str(payload.get("study_id") or "").strip(),
        "question_index": payload.get("question_index"),
        "active_phase": bool(payload.get("active_phase", False)),
        "client_captured_at": payload.get("client_captured_at") or payload.get("client_timestamp"),
        "source_monotonic_ms": payload.get("source_monotonic_ms"),
        "source_epoch_ms": payload.get("source_epoch_ms"),
        "clock_sync_id": payload.get("clock_sync_id"),
        "clock_sync_age_ms": payload.get("clock_sync_age_ms"),
        "clock_sync_rtt_ms": payload.get("clock_sync_rtt_ms"),
        "time_source": payload.get("time_source"),
        "server_received_at": received_at,
        "processed_at": timestamp(),
        "sequence_number": payload.get("sequence_number"),
        "sequence_diagnostics": sequence_diagnostics,
        "drop_count": sequence_diagnostics["missing_count"],
        "frame": frame_info,
        "analysis": analysis,
    }
    message = "Camera affect frame processed."
    status = "connected"
    if analysis.get("error"):
        message = f"Camera emotion analysis error: {analysis['error']}"
        status = "failed"
    elif sequence_diagnostics["sequence_status"] == "gap":
        message = (
            "Camera affect frame processed after a sequence gap of "
            f"{sequence_diagnostics['gap_count']}."
        )
        status = "degraded"
    if _config.get("lsl_enabled"):
        publication = _publish(result, arrival, arrival_epoch)
        result["recording"] = publication
        if not publication["complete"]:
            result["accepted"] = False
            result["reason"] = "recording_failed"
            message = "Camera frame was not fully published to LSL."
            status = "failed"
    else:
        result["recording"] = {"required": False, "complete": False, "status": "not_requested"}
    result["_epoch"] = time.time()
    _history.append(dict(result))
    _set_state(
        {
            "status": status,
            "latest": result,
            "last_activity_at": received_at,
            "last_message": message,
        }
    )
    return result


def is_configured() -> bool:
    """Return True after initialize() stored camera emotion settings."""
    return bool(_config)


def get_status() -> dict[str, Any]:
    with _state_lock:
        status = dict(_latest_state)
    status["enabled"] = bool(_config.get("enabled", False))
    status["lsl_enabled"] = bool(_config.get("lsl_enabled", False))
    status["worker_mode"] = _config.get("worker_mode", "local_worker")
    status["snapshot_interval_ms"] = _config.get("snapshot_interval_ms", DEFAULT_SNAPSHOT_INTERVAL_MS)
    status["emotion_worker_url"] = _config.get("emotion_worker_url", "")
    status["streams"] = [contract["key"] for contract in _streams.contracts()]
    return status


def get_preview_status() -> dict[str, Any]:
    with _state_lock:
        return dict(_preview_state)


def set_preview_active(active: bool) -> dict[str, Any]:
    """Set plugin-owned monitor state without relying on Flask globals."""

    global _preview_state
    with _state_lock:
        was_active = bool(_preview_state.get("active", False))
        _preview_state = {
            **_preview_state,
            "active": bool(active),
            "last_message": (
                _preview_state.get("last_message")
                if active and was_active
                else "Tablet camera live monitor is waiting for a frame."
                if active
                else "Tablet camera live monitor stopped."
            ),
        }
        return dict(_preview_state)


def _sequence_diagnostics(payload: dict[str, Any]) -> dict[str, Any]:
    """Track gaps and reject replayed/reordered browser frames per capture."""

    sequence = payload.get("sequence_number")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        return {
            "source_instance_id": str(payload.get("source_instance_id") or ""),
            "sequence_status": "untracked",
            "gap_count": 0,
            "missing_count": 0,
            "duplicate_count": 0,
            "out_of_order_count": 0,
        }

    source_instance_id = str(payload.get("source_instance_id") or "").strip()
    if not source_instance_id:
        source_instance_id = "|".join(
            str(payload.get(field) or "")
            for field in (
                "study_id",
                "participant_id",
                "session_id",
                "question_index",
                "preview",
            )
        ) or "anonymous"

    with _state_lock:
        state = _sequence_state.get(source_instance_id)
        if state is None:
            if len(_sequence_state) >= _MAX_SEQUENCE_SOURCES:
                _sequence_state.pop(next(iter(_sequence_state)))
            state = {
                "last_sequence": -1,
                "missing_count": 0,
                "duplicate_count": 0,
                "out_of_order_count": 0,
            }
            _sequence_state[source_instance_id] = state

        last_sequence = state["last_sequence"]
        if sequence == last_sequence:
            state["duplicate_count"] += 1
            status = "duplicate"
            gap_count = 0
        elif sequence < last_sequence:
            state["out_of_order_count"] += 1
            status = "out_of_order"
            gap_count = 0
        else:
            gap_count = sequence - last_sequence - 1
            if gap_count > 0:
                state["missing_count"] += gap_count
                status = "gap"
            else:
                status = "first" if last_sequence < 0 else "in_order"
            state["last_sequence"] = sequence

        return {
            "source_instance_id": source_instance_id,
            "sequence_status": status,
            "last_sequence": state["last_sequence"],
            "gap_count": gap_count,
            "missing_count": state["missing_count"],
            "duplicate_count": state["duplicate_count"],
            "out_of_order_count": state["out_of_order_count"],
        }


def get_interval_summary(start_epoch: float, end_epoch: float) -> dict[str, Any]:
    samples = samples_in_interval(_history, start_epoch, end_epoch)
    if not samples:
        return {
            "available": False,
            "sample_count": 0,
            "valid_detection_count": 0,
            "valid_emotion_count": 0,
            "avg_face_confidence": None,
            "avg_emotion_confidence": None,
            "face_detected_rate": None,
            "dominant_emotion": None,
            **truncation_info(_history, start_epoch),
        }

    emotion_totals: dict[str, float] = {}
    face_detected = 0
    detection_count = 0
    emotion_count = 0
    face_conf_values: list[float] = []
    emotion_conf_values: list[float] = []

    for sample in samples:
        analysis = sample.get("analysis") or {}
        if sample.get("recording") and (sample["recording"]).get("status") != "recorded":
            continue
        detection_valid = analysis.get(
            "detection_valid",
            not bool(analysis.get("error")) and isinstance(analysis.get("face_detected"), bool),
        ) is True
        emotion_valid = analysis.get("emotion_valid", detection_valid and bool(analysis.get("face_detected"))) is True
        if detection_valid:
            detection_count += 1
            if analysis.get("face_detected") is True:
                face_detected += 1
            face_confidence = _finite_number(analysis.get("face_confidence"))
            if face_confidence is not None:
                face_conf_values.append(face_confidence)
        if not emotion_valid:
            continue
        emotion_count += 1
        confidence = _finite_number(analysis.get("confidence"))
        if confidence is not None:
            emotion_conf_values.append(confidence)
        for emotion, score in (analysis.get("scores") or {}).items():
            value = _finite_number(score)
            if value is not None:
                emotion_totals[emotion] = emotion_totals.get(emotion, 0.0) + value

    dominant_emotion = None
    if emotion_totals:
        dominant_emotion = max(emotion_totals.items(), key=lambda item: item[1])[0]

    return {
        "available": detection_count > 0 or emotion_count > 0,
        "sample_count": len(samples),
        "valid_detection_count": detection_count,
        "valid_emotion_count": emotion_count,
        "avg_face_confidence": _mean(face_conf_values),
        "avg_emotion_confidence": _mean(emotion_conf_values),
        "face_detected_rate": round(face_detected / detection_count, 4) if detection_count else None,
        "dominant_emotion": dominant_emotion,
        "max_gap_seconds": max_gap_seconds(samples),
        **truncation_info(_history, start_epoch),
    }


def export_interval_samples(start_epoch: float, end_epoch: float) -> list[dict[str, Any]]:
    """Return processed emotion samples for the persisted session sidecar."""
    return [dict(sample) for sample in samples_in_interval(_history, start_epoch, end_epoch)]


def _extract_frame_info(payload: dict[str, Any]) -> dict[str, Any]:
    image_data = str(payload.get("image") or payload.get("image_base64") or "")
    image_format = str(payload.get("image_format") or "unknown")
    byte_count = 0

    if image_data:
        encoded = image_data.split(",", 1)[-1]
        try:
            byte_count = len(base64.b64decode(encoded, validate=False))
        except Exception:
            byte_count = len(encoded)

    return {
        "image_format": image_format,
        "byte_count": byte_count,
        "width": payload.get("width"),
        "height": payload.get("height"),
        "raw_frame_stored": False,
    }


def _analyze_frame(payload: dict[str, Any]) -> dict[str, Any]:
    mode = _config.get("worker_mode", "local_worker")
    if mode in {"local_worker", "remote_worker"}:
        return _forward_to_emotion_worker(payload)
    # Never invent a result: without a worker there is no emotion reading.
    return _worker_error_result(f"unsupported worker_mode {mode!r}; use local_worker or remote_worker")


def _forward_to_emotion_worker(payload: dict[str, Any]) -> dict[str, Any]:
    """Forward frame to the local Emotion Worker and return its result."""
    import urllib.error
    import urllib.request

    mode_label = _config.get("worker_mode", "local_worker")
    url = _config.get("emotion_worker_url", "")
    if not url:
        reason = f"{mode_label}: emotion_worker_url not configured"
        _set_state({"last_message": reason})
        return _worker_error_result(reason)

    timeout_s = _config.get("emotion_worker_timeout_ms", 5000) / 1000.0

    import json
    req_body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{url}/analyze",
        data=req_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
            result = json.loads(raw)
        return _normalize_worker_analysis(result)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = f"emotion_worker unreachable: {exc}"
        _set_state({"last_message": reason})
        return _worker_error_result(reason)


def _worker_error_result(reason: str) -> dict[str, Any]:
    return {
        "worker_mode": _config.get("worker_mode", "local_worker"),
        "analysis_status": "error",
        "detection_valid": False,
        "emotion_valid": False,
        "face_detected": None,
        "emotion": "unknown",
        "confidence": None,
        "face_confidence": None,
        "scores": {},
        "overlay": {},
        "error": reason,
        "install_hint": "Run the Study Runner installer again (tools/install-windows.cmd or tools/install-macos.sh), then restart the Emotion Worker from the dashboard.",
    }


def _normalize_worker_analysis(raw: Any) -> dict[str, Any]:
    """Reject incomplete remote/local worker outputs instead of filling measurements with zero."""
    if not isinstance(raw, dict):
        return _worker_error_result("emotion worker returned a non-object analysis")
    if raw.get("error"):
        return _worker_error_result(str(raw["error"]))
    if (
        raw.get("analysis_status") == "no_face"
        and raw.get("detection_valid") is True
        and raw.get("face_detected") is False
    ):
        return {
            **raw,
            "worker_mode": _config.get("worker_mode", "local_worker"),
            "emotion_valid": False,
            "face_detected": False,
            "emotion": "unknown",
            "confidence": None,
            "face_confidence": None,
            "scores": {},
            "overlay": {},
        }
    detector_score = _finite_number(raw.get("face_confidence"))
    confidence = _finite_number(raw.get("confidence"))
    emotion = str(raw.get("emotion") or "").strip().lower()
    scores = raw.get("scores")
    if (
        raw.get("analysis_status") != "ok"
        or raw.get("detection_valid") is not True
        or raw.get("emotion_valid") is not True
        or raw.get("face_detected") is not True
        or detector_score is None
        or detector_score < 0
        or confidence is None
        or emotion not in _EMOTIONS
        or not isinstance(scores, dict)
        or any(
            (score := _finite_number(scores.get(name))) is None or not 0.0 <= score <= 1.0
            for name in _EMOTIONS
        )
        or not 0.0 <= confidence <= 1.0
        or abs(confidence - float(scores[emotion])) > 0.001
    ):
        return _worker_error_result("emotion worker returned incomplete or invalid analysis")
    return {**raw, "worker_mode": _config.get("worker_mode", "local_worker")}


def _publish(result: dict[str, Any], arrival: float, arrival_epoch: float) -> dict[str, Any]:
    """Publish both declared samples and report partial writes explicitly."""
    analysis = result.get("analysis") or {}
    scores = analysis.get("scores") or {}
    frame = result.get("frame") or {}
    detection_valid = analysis.get("detection_valid") is True
    emotion_valid = analysis.get("emotion_valid") is True
    face = float(analysis["face_detected"]) if detection_valid else math.nan
    emotion = {
        name: float(scores[name]) if emotion_valid and name in scores else math.nan
        for name in _EMOTIONS
    }
    emotion.update({
        "confidence": float(analysis["confidence"]) if emotion_valid else math.nan,
        "face_detected": face,
        "valid": float(emotion_valid),
        "sequence": result.get("sequence_number"),
    })
    quality = {
        "face_detected": face,
        "face_confidence": (
            float(analysis["face_confidence"])
            if detection_valid and analysis.get("face_confidence") is not None else math.nan
        ),
        "width": _finite_number(frame.get("width")),
        "height": _finite_number(frame.get("height")),
        "valid": float(detection_valid),
        "sequence": result.get("sequence_number"),
    }
    stamp, timestamp_source = _capture_timestamp(result, arrival, arrival_epoch)
    result["timestamp_source"] = timestamp_source
    evidence = {
        "time_source": 1.0 if timestamp_source == "tablet_sync" else 0.0,
        "source_monotonic_ms": _finite_number(result.get("source_monotonic_ms")),
        "clock_sync_age_ms": _finite_number(result.get("clock_sync_age_ms")) if timestamp_source == "tablet_sync" else None,
        "clock_sync_rtt_ms": _finite_number(result.get("clock_sync_rtt_ms")) if timestamp_source == "tablet_sync" else None,
    }
    emotion.update(evidence)
    quality.update(evidence)
    emotion_published = _streams.push(
        "emotion", _streams.row("emotion", emotion), stamp, arrival=arrival, valid=emotion_valid
    )
    quality_published = _streams.push(
        "face_quality", _streams.row("face_quality", quality), stamp, arrival=arrival, valid=detection_valid
    )
    complete = emotion_published and quality_published
    return {
        "required": True,
        "complete": complete,
        "status": "recorded" if complete else "partial" if emotion_published or quality_published else "failed",
        "emotion_published": emotion_published,
        "face_quality_published": quality_published,
        "errors": {
            key: _streams.last_error(key)
            for key, published in (("emotion", emotion_published), ("face_quality", quality_published))
            if not published
        },
    }


def _capture_timestamp(result: dict[str, Any], arrival: float, arrival_epoch: float) -> tuple[float, str]:
    """Backdate only a plausible capture with fresh shared-clock evidence."""
    if result.get("time_source") != "tablet_sync" or not str(result.get("clock_sync_id") or ""):
        return arrival, "server_receipt"
    estimate_age = _finite_number(result.get("clock_sync_age_ms"))
    rtt = _finite_number(result.get("clock_sync_rtt_ms"))
    source_epoch_ms = _finite_number(result.get("source_epoch_ms"))
    if (
        estimate_age is None or not 0 <= estimate_age <= 120_000
        or rtt is None or rtt < 0
        or source_epoch_ms is None
    ):
        return arrival, "server_receipt"
    age = arrival_epoch - source_epoch_ms / 1000.0
    if not 0.0 <= age <= MAX_CAPTURE_AGE_SECONDS:
        return arrival, "server_receipt"
    return arrival - age, "tablet_sync"


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _set_state(values: dict[str, Any]) -> None:
    set_state(_latest_state, _state_lock, values)


def _set_preview_state(result: dict[str, Any], payload: dict[str, Any]) -> None:
    global _preview_state

    analysis = result.get("analysis") or {}
    image_data = str(payload.get("image") or payload.get("image_base64") or "")
    with _state_lock:
        _preview_state = {
            "available": True,
            "active": True,
            "status": "failed" if analysis.get("error") else "connected",
            "last_message": analysis.get("error") or "Tablet camera live frame processed.",
            "updated_at": result.get("processed_at"),
            "latest": result,
            "image": image_data,
        }


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)
