"""
Camera affect adapter for iPad selfie snapshots.

The first implementation accepts browser snapshots and produces a conservative placeholder
emotion result. A later worker can replace the placeholder analysis with a stronger model while
keeping the same routes, timestamps, and LSL stream shape.
"""
from __future__ import annotations

import base64
import time
from collections import deque
from threading import Lock
from typing import Any

from .dependency_utils import ensure_requirements


_state_lock = Lock()
_config: dict[str, Any] = {}
_lsl_outlets: dict[str, Any] = {}
_cv2: Any = None
_np: Any = None
_face_cascade: Any = None
_history: deque[dict[str, Any]] = deque(maxlen=2048)
_latest_state: dict[str, Any] = {
    "status": "not_configured",
    "latest": {},
    "last_message": "Camera affect adapter has not been configured.",
}

_EMOTIONS = ("angry", "disgust", "fear", "happy", "sad", "surprise", "neutral", "unknown")


def initialize(
    *,
    enabled: bool = False,
    snapshot_interval_ms: int = 1000,
    store_raw_frames: bool = False,
    overlay_enabled: bool = True,
    worker_mode: str = "mock",
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
        "snapshot_interval_ms": max(250, int(snapshot_interval_ms)),
        "store_raw_frames": bool(store_raw_frames),
        "overlay_enabled": bool(overlay_enabled),
        "worker_mode": worker_mode or "mock",
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

    if _config["enabled"] and _config["worker_mode"] in {"opencv_haar", "opencv_cnn"}:
        _initialize_opencv()


def start() -> dict[str, Any]:
    if not _config:
        _set_state({"status": "not_configured", "last_message": "Camera affect adapter is not configured."})
    elif not _config.get("enabled"):
        _set_state({"status": "disabled", "last_message": "Camera affect analysis is disabled."})
    else:
        _set_state({"status": "ready", "last_message": "Camera affect analysis is ready."})
    return get_status()


def stop() -> dict[str, Any]:
    _set_state({"status": "stopped", "last_message": "Camera affect analysis stopped."})
    return get_status()


def process_frame(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept one browser snapshot and return the current conservative analysis result."""
    if not _config or not _config.get("enabled", False):
        _set_state({"status": "disabled", "last_message": "Camera affect frame ignored because analysis is disabled."})
        return {"accepted": False, "reason": "disabled", **get_status()}

    received_at = _timestamp()
    frame_info = _extract_frame_info(payload)
    analysis = _analyze_frame(payload)
    result = {
        "accepted": True,
        "participant_id": str(payload.get("participant_id") or "").strip(),
        "study_id": str(payload.get("study_id") or "").strip(),
        "question_index": payload.get("question_index"),
        "active_phase": bool(payload.get("active_phase", False)),
        "client_captured_at": payload.get("client_captured_at") or payload.get("client_timestamp"),
        "server_received_at": received_at,
        "processed_at": _timestamp(),
        "sequence_number": payload.get("sequence_number"),
        "frame": frame_info,
        "analysis": analysis,
    }
    result["_epoch"] = time.time()
    _history.append(dict(result))

    _set_state(
        {
            "status": "connected",
            "latest": result,
            "last_activity_at": received_at,
            "last_message": "Camera affect frame processed.",
        }
    )
    _push_lsl_result(result)
    return result


def get_status() -> dict[str, Any]:
    with _state_lock:
        status = dict(_latest_state)
    status["enabled"] = bool(_config.get("enabled", False))
    status["lsl_enabled"] = bool(_config.get("lsl_enabled", False))
    status["worker_mode"] = _config.get("worker_mode", "mock")
    status["snapshot_interval_ms"] = _config.get("snapshot_interval_ms", 1000)
    status["streams"] = list(_lsl_outlets.keys())
    return status


def get_interval_summary(start_epoch: float, end_epoch: float) -> dict[str, Any]:
    samples = [
        sample for sample in list(_history)
        if start_epoch <= float(sample.get("_epoch", 0.0)) <= end_epoch
    ]
    if not samples:
        return {
            "available": False,
            "sample_count": 0,
            "avg_face_confidence": None,
            "avg_emotion_confidence": None,
            "face_detected_rate": None,
            "dominant_emotion": None,
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
    }


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
    mode = _config.get("worker_mode", "mock")
    if mode in {"opencv_haar", "opencv_cnn"}:
        opencv_result = _analyze_frame_with_opencv(payload)
        if opencv_result is not None:
            return opencv_result
    elif mode == "remote_worker":
        remote_result = _forward_to_emotion_worker(payload)
        if remote_result is not None:
            return remote_result
    return _analyze_frame_placeholder(payload)


def _forward_to_emotion_worker(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Forward frame to the remote Emotion Worker (Mac/Windows) and return its result."""
    import urllib.error
    import urllib.request

    url = _config.get("emotion_worker_url", "")
    if not url:
        _set_state({"last_message": "remote_worker: emotion_worker_url not configured"})
        return None

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
        result.setdefault("worker_mode", "remote_worker")
        result.setdefault("face_detected", False)
        result.setdefault("emotion", "unknown")
        result.setdefault("confidence", 0.0)
        result.setdefault("face_confidence", 0.0)
        result.setdefault("scores", {name: 0.0 for name in _EMOTIONS})
        result.setdefault("overlay", {})
        return result
    except (urllib.error.URLError, OSError, ValueError) as exc:
        _set_state({"last_message": f"emotion_worker unreachable: {exc}"})
        return None


def _analyze_frame_placeholder(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a stable shape when no real face/emotion model is active."""
    face_detected = bool(payload.get("face_detected", False))
    emotion = str(payload.get("emotion") or "unknown")
    if emotion not in _EMOTIONS:
        emotion = "unknown"

    scores = {name: 0.0 for name in _EMOTIONS}
    scores[emotion] = 1.0 if emotion != "unknown" else 0.0
    scores["unknown"] = 1.0 if emotion == "unknown" else 0.0

    return {
        "worker_mode": _config.get("worker_mode", "mock"),
        "face_detected": face_detected,
        "emotion": emotion,
        "confidence": 0.0 if emotion == "unknown" else 1.0,
        "face_confidence": 1.0 if face_detected else 0.0,
        "scores": scores,
        "overlay": payload.get("overlay") if isinstance(payload.get("overlay"), dict) else {},
    }


def _analyze_frame_with_opencv(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not _initialize_opencv():
        return None

    frame = _decode_image(payload)
    if frame is None:
        return _analyze_frame_placeholder(payload)

    gray = _cv2.cvtColor(frame, _cv2.COLOR_BGR2GRAY)
    faces = _face_cascade.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(30, 30),
    )

    scores = {name: 0.0 for name in _EMOTIONS}
    scores["unknown"] = 1.0

    if len(faces) == 0:
        return {
            "worker_mode": _config.get("worker_mode", "opencv_haar"),
            "face_detected": False,
            "emotion": "unknown",
            "confidence": 0.0,
            "face_confidence": 0.0,
            "scores": scores,
            "overlay": {},
        }

    x, y, width, height = max(faces, key=lambda face: face[2] * face[3])
    image_area = max(1, frame.shape[0] * frame.shape[1])
    face_area_ratio = (float(width) * float(height)) / float(image_area)
    face_confidence = max(0.0, min(1.0, face_area_ratio / 0.35))

    return {
        "worker_mode": _config.get("worker_mode", "opencv_haar"),
        "face_detected": True,
        "emotion": "unknown",
        "confidence": 0.0,
        "face_confidence": round(face_confidence, 4),
        "scores": scores,
        "overlay": {
            "face_box": {
                "x": int(x),
                "y": int(y),
                "width": int(width),
                "height": int(height),
            }
        },
    }


def _initialize_opencv() -> bool:
    global _cv2, _np, _face_cascade

    if _face_cascade is not None:
        return True

    if not ensure_requirements(
        [("numpy", "numpy")],
        auto_install=bool(_config.get("auto_install", True)),
        label="Camera emotion NumPy",
    ):
        _set_state({"status": "failed", "last_message": "NumPy is unavailable for camera analysis."})
        return False

    try:
        import numpy as np
    except Exception as error:
        _set_state({"status": "failed", "last_message": f"NumPy initialization failed: {error}"})
        return False

    if _get_numpy_major_version(np) >= 2:
        _set_state(
            {
                "status": "failed",
                "last_message": "OpenCV camera analysis needs numpy<2.0 in this environment. Run pip install -r requirements.txt.",
            }
        )
        return False

    if not ensure_requirements(
        [("cv2", "opencv-python-headless")],
        auto_install=bool(_config.get("auto_install", True)),
        label="Camera emotion OpenCV",
    ):
        _set_state({"status": "failed", "last_message": "OpenCV is unavailable for camera analysis."})
        return False

    try:
        import cv2

        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        face_cascade = cv2.CascadeClassifier(cascade_path)
        if face_cascade.empty():
            raise RuntimeError("OpenCV Haar cascade could not be loaded.")

        _cv2 = cv2
        _np = np
        _face_cascade = face_cascade
        return True
    except Exception as error:
        _set_state({"status": "failed", "last_message": f"OpenCV initialization failed: {error}"})
        return False


def _get_numpy_major_version(np_module: Any) -> int:
    version = str(getattr(np_module, "__version__", "0"))
    try:
        return int(version.split(".", 1)[0])
    except (TypeError, ValueError):
        return 0


def _decode_image(payload: dict[str, Any]) -> Any:
    image_data = str(payload.get("image") or payload.get("image_base64") or "")
    if not image_data:
        return None

    encoded = image_data.split(",", 1)[-1]
    try:
        image_bytes = base64.b64decode(encoded, validate=False)
        buffer = _np.frombuffer(image_bytes, dtype=_np.uint8)
        return _cv2.imdecode(buffer, _cv2.IMREAD_COLOR)
    except Exception:
        return None


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
        channel_count=len(_EMOTIONS) + 2,
        nominal_srate=0,
        channel_format="float32",
        source_id="camera_emotion",
    )
    channels = info.desc().append_child("channels")
    for label in (*_EMOTIONS, "confidence", "face_detected"):
        channel = channels.append_child("channel")
        channel.append_child_value("label", label)

    quality_info = StreamInfo(
        name="CameraFaceQuality",
        type="CameraFaceQuality",
        channel_count=4,
        nominal_srate=0,
        channel_format="float32",
        source_id="camera_face_quality",
    )
    quality_channels = quality_info.desc().append_child("channels")
    for label in ("face_detected", "face_confidence", "width", "height"):
        channel = quality_channels.append_child("channel")
        channel.append_child_value("label", label)

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

    frame = result.get("frame") or {}
    quality_values = [
        1.0 if analysis.get("face_detected") else 0.0,
        float(analysis.get("face_confidence") or 0.0),
        float(frame.get("width") or 0.0),
        float(frame.get("height") or 0.0),
    ]

    try:
        _lsl_outlets["CameraEmotion"].push_sample(emotion_values)
        _lsl_outlets["CameraFaceQuality"].push_sample(quality_values)
    except Exception as error:
        print(f"[CameraEmotion] Could not push LSL sample: {error}")


def _set_state(values: dict[str, Any]) -> None:
    with _state_lock:
        _latest_state.update(values)
        _latest_state["updated_at"] = _timestamp()


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)
