"""
Camera affect adapter for tablet selfie-camera snapshots.

Browser snapshots are forwarded to the Emotion Worker (local or remote), which
returns the face/emotion analysis. Without a reachable worker the result says
so (emotion "unknown", confidence 0 and an error) -- nothing is guessed.
"""
from __future__ import annotations

import base64
import math
import time
from collections import deque
from threading import Lock
from typing import Any

from study_runner.plugin_framework.adapter_utils import set_state, timestamp
from study_runner.shared.dependency_utils import ensure_requirements
from study_runner.plugin_framework.history_buffer import history_maxlen, max_gap_seconds, samples_in_interval, truncation_info
from study_runner.contracts.stream_contract import apply_stream_contract_desc, load_own_stream_contracts


_state_lock = Lock()
_config: dict[str, Any] = {}
_lsl_outlets: dict[str, Any] = {}
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
LSL_SOURCE_IDS = {
    "emotion": "study_runner.tablet_camera.emotion",
    "face_quality": "study_runner.tablet_camera.face_quality",
}
LSL_CHANNEL_UNITS = {
    "emotion": ("probability",) * len(_EMOTIONS) + ("probability", "boolean", "count"),
    "face_quality": ("boolean", "probability", "pixel", "pixel", "count"),
}
# Package 5d (docs/archive/architecture-1.0-umbau.md): this plugin's own frozen
# stream contract, for the desc/study_runner XDF header block only -- the
# LSL_SOURCE_IDS/LSL_CHANNEL_UNITS constants above remain the source of
# truth for outlet creation itself, unchanged.
STREAM_CONTRACTS = load_own_stream_contracts(__file__)


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

    if _config["enabled"] and _config["lsl_enabled"]:
        _initialize_lsl_outlets()


def start() -> dict[str, Any]:
    # A new run starts with no samples from an earlier participant.
    _history.clear()
    if not _config:
        _set_state({"status": "not_configured", "last_message": "Camera affect adapter is not configured."})
    elif not _config.get("enabled"):
        _set_state({"status": "disabled", "last_message": "Camera affect analysis is disabled."})
    else:
        _set_state({"status": "ready", "last_message": "Camera affect analysis is ready."})
    return get_status()


def stop() -> dict[str, Any]:
    set_preview_active(False)
    _set_state({"status": "stopped", "last_message": "Camera affect analysis stopped."})
    return get_status()


def process_frame(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept one browser snapshot and return the current conservative analysis result."""
    if not _config or not _config.get("enabled", False):
        _set_state({"status": "disabled", "last_message": "Camera affect frame ignored because analysis is disabled."})
        return {"accepted": False, "reason": "disabled", **get_status()}

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
        "server_received_at": received_at,
        "processed_at": timestamp(),
        "sequence_number": payload.get("sequence_number"),
        "sequence_diagnostics": sequence_diagnostics,
        "drop_count": sequence_diagnostics["missing_count"],
        "frame": frame_info,
        "analysis": analysis,
    }
    result["_epoch"] = time.time()
    _history.append(dict(result))

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
    _set_state(
        {
            "status": status,
            "latest": result,
            "last_activity_at": received_at,
            "last_message": message,
        }
    )
    _push_lsl_result(result)
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
    status["streams"] = list(_lsl_outlets.keys())
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
            "avg_face_confidence": None,
            "avg_emotion_confidence": None,
            "face_detected_rate": None,
            "dominant_emotion": None,
            **truncation_info(_history, start_epoch),
        }

    emotion_totals: dict[str, float] = {}
    face_detected = 0
    face_conf_values: list[float] = []
    emotion_conf_values: list[float] = []

    for sample in samples:
        analysis = sample.get("analysis") or {}
        if analysis.get("face_detected"):
            face_detected += 1
        if analysis.get("face_confidence") is not None:
            face_conf_values.append(float(analysis.get("face_confidence") or 0.0))
        if analysis.get("confidence") is not None:
            emotion_conf_values.append(float(analysis.get("confidence") or 0.0))
        for emotion, score in (analysis.get("scores") or {}).items():
            if score is None:
                continue
            emotion_totals[emotion] = emotion_totals.get(emotion, 0.0) + float(score)

    dominant_emotion = None
    if emotion_totals:
        dominant_emotion = max(emotion_totals.items(), key=lambda item: item[1])[0]

    return {
        "available": True,
        "sample_count": len(samples),
        "avg_face_confidence": _mean(face_conf_values),
        "avg_emotion_confidence": _mean(emotion_conf_values),
        "face_detected_rate": round(face_detected / len(samples), 4),
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
        # Ensure the result has the expected shape
        result.setdefault("worker_mode", _config.get("worker_mode", "local_worker"))
        result.setdefault("face_detected", False)
        result.setdefault("emotion", "unknown")
        result.setdefault("confidence", 0.0)
        result.setdefault("face_confidence", 0.0)
        result.setdefault("scores", {name: 0.0 for name in _EMOTIONS})
        result.setdefault("overlay", {})
        return result
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = f"emotion_worker unreachable: {exc}"
        _set_state({"last_message": reason})
        return _worker_error_result(reason)


def _worker_error_result(reason: str) -> dict[str, Any]:
    scores = {name: 0.0 for name in _EMOTIONS}
    return {
        "worker_mode": _config.get("worker_mode", "local_worker"),
        "face_detected": False,
        "emotion": "unknown",
        "confidence": 0.0,
        "face_confidence": 0.0,
        "scores": scores,
        "overlay": {},
        "error": reason,
        "install_hint": "Run the Study Runner installer again (tools/install-windows.cmd or tools/install-macos.sh), then restart the Emotion Worker from the dashboard.",
    }


def _initialize_lsl_outlets() -> None:
    global _lsl_outlets

    if not ensure_requirements(
        [("pylsl", "pylsl")],
        auto_install=bool(_config.get("lsl_auto_install", True)),
        label="Camera emotion LSL",
    ):
        _lsl_outlets = {}
        return

    from pylsl import StreamInfo, StreamOutlet

    info = StreamInfo(
        name=_config.get("lsl_stream_name", "CameraEmotion"),
        type="CameraEmotion",
        channel_count=len(_EMOTIONS) + 3,
        nominal_srate=0,
        channel_format="float32",
        source_id=LSL_SOURCE_IDS["emotion"],
    )
    channels = info.desc().append_child("channels")
    for label, unit in zip(
        (*_EMOTIONS, "confidence", "face_detected", "sequence"),
        LSL_CHANNEL_UNITS["emotion"],
        strict=True,
    ):
        channel = channels.append_child("channel")
        channel.append_child_value("label", label)
        channel.append_child_value("unit", unit)
    apply_stream_contract_desc(info, STREAM_CONTRACTS["emotion"])

    quality_info = StreamInfo(
        name="CameraFaceQuality",
        type="CameraFaceQuality",
        channel_count=5,
        nominal_srate=0,
        channel_format="float32",
        source_id=LSL_SOURCE_IDS["face_quality"],
    )
    quality_channels = quality_info.desc().append_child("channels")
    for label, unit in zip(
        ("face_detected", "face_confidence", "width", "height", "sequence"),
        LSL_CHANNEL_UNITS["face_quality"],
        strict=True,
    ):
        channel = quality_channels.append_child("channel")
        channel.append_child_value("label", label)
        channel.append_child_value("unit", unit)
    apply_stream_contract_desc(quality_info, STREAM_CONTRACTS["face_quality"])

    _lsl_outlets = {
        "CameraEmotion": StreamOutlet(info),
        "CameraFaceQuality": StreamOutlet(quality_info),
    }
    print("[CameraEmotion] LSL outlets ready.")


def _push_lsl_result(result: dict[str, Any]) -> None:
    if not _lsl_outlets:
        return

    analysis = result.get("analysis") or {}
    scores = analysis.get("scores") or {}
    emotion_values = [float(scores.get(name, 0.0)) for name in _EMOTIONS]
    emotion_values.append(float(analysis.get("confidence") or 0.0))
    emotion_values.append(1.0 if analysis.get("face_detected") else 0.0)
    sequence = result.get("sequence_number")
    try:
        sequence_value = float(sequence)
    except (TypeError, ValueError):
        sequence_value = math.nan
    emotion_values.append(sequence_value)

    frame = result.get("frame") or {}
    quality_values = [
        1.0 if analysis.get("face_detected") else 0.0,
        float(analysis.get("face_confidence") or 0.0),
        float(frame.get("width") or 0.0),
        float(frame.get("height") or 0.0),
        sequence_value,
    ]

    try:
        from pylsl import local_clock

        lsl_timestamp = local_clock()
        try:
            source_epoch = float(result.get("source_epoch_ms")) / 1000.0
            if abs(source_epoch - time.time()) <= 60.0:
                lsl_timestamp += source_epoch - time.time()
        except (TypeError, ValueError):
            pass
        _lsl_outlets["CameraEmotion"].push_sample(emotion_values, timestamp=lsl_timestamp)
        _lsl_outlets["CameraFaceQuality"].push_sample(quality_values, timestamp=lsl_timestamp)
    except Exception as error:
        print(f"[CameraEmotion] Could not push LSL sample: {error}")


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

