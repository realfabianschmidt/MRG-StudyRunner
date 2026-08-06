"""
Emotion analysis module for the local Emotion Worker.

Accepts a frame payload in the same format as /api/camera/frame and returns an
analysis dict compatible with camera_affect_adapter's expected shape.
"""
from __future__ import annotations

import base64
import threading
from typing import Any

import cv2
import numpy as np

_EMOTIONS = ("angry", "disgust", "fear", "happy", "sad", "surprise", "neutral", "unknown")

_lsl_outlet: Any = None
_lsl_lock = threading.Lock()


def init_lsl(stream_name: str = "CameraEmotion") -> None:
    """Create an LSL outlet for emotion scores."""
    global _lsl_outlet
    try:
        from pylsl import StreamInfo, StreamOutlet
        info = StreamInfo(
            name=stream_name,
            type="CameraEmotion",
            channel_count=len(_EMOTIONS) + 2,
            nominal_srate=0,
            channel_format="float32",
            source_id="emotion_worker",
        )
        channels = info.desc().append_child("channels")
        for label in (*_EMOTIONS, "confidence", "face_detected"):
            channel = channels.append_child("channel")
            channel.append_child_value("label", label)
        _lsl_outlet = StreamOutlet(info)
        print(f"[EmotionWorker] LSL outlet '{stream_name}' ready")
    except Exception as exc:
        print(f"[EmotionWorker] LSL init failed: {exc}")


def analyze_frame(payload: dict[str, Any]) -> dict[str, Any]:
    """Decode JPEG from payload, run DeepFace, and return an analysis dict."""
    frame = _decode_image(payload)
    if frame is None:
        return _empty_result("could not decode image")

    if len(frame.shape) == 2:
        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

    try:
        from deepface import DeepFace
        results = DeepFace.analyze(
            frame,
            actions=["emotion"],
            enforce_detection=False,
            detector_backend="opencv",
            silent=True,
        )
        result = results[0] if isinstance(results, list) else results
        dominant = str(result.get("dominant_emotion", "unknown")).lower().strip()
        raw_scores = result.get("emotion", {}) or {}
        region = result.get("region", {}) or {}
    except Exception as exc:
        return _empty_result(f"DeepFace error: {exc}")

    scores = {name: 0.0 for name in _EMOTIONS}
    for label, value in raw_scores.items():
        normalized = str(label).lower().strip()
        if normalized in scores:
            scores[normalized] = float(value) / 100.0

    if dominant not in scores:
        dominant = "unknown"

    face_detected = dominant != "unknown"
    confidence = scores.get(dominant, 0.0)
    analysis = {
        "worker_mode": "local_worker",
        "face_detected": face_detected,
        "emotion": dominant,
        "confidence": round(confidence, 4),
        "face_confidence": round(confidence, 4),
        "scores": {key: round(value, 4) for key, value in scores.items()},
        "overlay": {
            "face_box": {
                "x": int(region.get("x", 0)),
                "y": int(region.get("y", 0)),
                "width": int(region.get("w", 0)),
                "height": int(region.get("h", 0)),
            }
        } if region else {},
    }

    _push_lsl(analysis)
    return analysis


def _decode_image(payload: dict[str, Any]) -> Any:
    image_data = str(payload.get("image") or payload.get("image_base64") or "")
    if not image_data:
        return None
    encoded = image_data.split(",", 1)[-1]
    try:
        raw = base64.b64decode(encoded, validate=False)
        buffer = np.frombuffer(raw, dtype=np.uint8)
        return cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    except Exception:
        return None


def _empty_result(reason: str) -> dict[str, Any]:
    scores = {name: 0.0 for name in _EMOTIONS}
    return {
        "worker_mode": "local_worker",
        "face_detected": False,
        "emotion": "unknown",
        "confidence": 0.0,
        "face_confidence": 0.0,
        "scores": scores,
        "overlay": {},
        "error": reason,
    }


def _push_lsl(result: dict[str, Any]) -> None:
    with _lsl_lock:
        if _lsl_outlet is None:
            return
    scores = result.get("scores", {})
    sample = [float(scores.get(name, 0.0)) for name in _EMOTIONS]
    sample.append(float(result.get("confidence", 0.0)))
    sample.append(1.0 if result.get("face_detected") else 0.0)
    try:
        _lsl_outlet.push_sample(sample)
    except Exception as exc:
        print(f"[EmotionWorker] LSL push failed: {exc}")
