"""
Emotion analysis module for the Emotion Worker.

Accepts a frame payload (same format as /api/camera/frame) and returns an
analysis dict compatible with camera_affect_adapter's expected shape.

IR image handling:
- NoIR OV5647 frames arrive as BGR (R≈G≈B, quasi-grayscale) — DeepFace handles
  these without modification.
- True single-channel images (shape (H,W)) are converted to BGR before analysis.
"""
from __future__ import annotations

import base64
import threading
from typing import Any

import cv2
import numpy as np

_EMOTIONS = ("angry", "disgust", "fear", "happy", "sad", "surprise", "neutral", "unknown")

# Optional LSL outlet — initialised once if --lsl flag is passed to server.py
_lsl_outlet: Any = None
_lsl_lock = threading.Lock()


def init_lsl(stream_name: str = "CameraEmotion") -> None:
    """Create an LSL outlet for emotion scores (called once from server.py if --lsl)."""
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
            ch = channels.append_child("channel")
            ch.append_child_value("label", label)
        _lsl_outlet = StreamOutlet(info)
        print(f"[EmotionWorker] LSL outlet '{stream_name}' ready")
    except Exception as exc:
        print(f"[EmotionWorker] LSL init failed: {exc}")


def analyze_frame(payload: dict[str, Any]) -> dict[str, Any]:
    """Decode JPEG from payload, run DeepFace, return analysis dict."""
    frame = _decode_image(payload)
    if frame is None:
        return _empty_result("could not decode image")

    # IR normalisation: true single-channel → 3-channel BGR
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
        r = results[0] if isinstance(results, list) else results
        dominant: str = r.get("dominant_emotion", "unknown")
        raw_scores: dict = r.get("emotion", {})
        region: dict = r.get("region", {})
    except Exception as exc:
        return _empty_result(f"DeepFace error: {exc}")

    # Normalise emotion labels to our canonical set
    scores = {name: 0.0 for name in _EMOTIONS}
    for label, value in raw_scores.items():
        normalised = label.lower().strip()
        if normalised in scores:
            scores[normalised] = float(value) / 100.0  # DeepFace returns percentages

    if dominant not in scores:
        dominant = "unknown"

    face_detected = dominant != "unknown"
    confidence = scores.get(dominant, 0.0)

    result = {
        "worker_mode": "remote_worker",
        "face_detected": face_detected,
        "emotion": dominant,
        "confidence": round(confidence, 4),
        "face_confidence": round(confidence, 4),
        "scores": {k: round(v, 4) for k, v in scores.items()},
        "overlay": {
            "face_box": {
                "x": int(region.get("x", 0)),
                "y": int(region.get("y", 0)),
                "width": int(region.get("w", 0)),
                "height": int(region.get("h", 0)),
            }
        } if region else {},
    }

    _push_lsl(result)
    return result


def _decode_image(payload: dict[str, Any]) -> Any:
    image_data = str(payload.get("image") or payload.get("image_base64") or "")
    if not image_data:
        return None
    encoded = image_data.split(",", 1)[-1]
    try:
        raw = base64.b64decode(encoded, validate=False)
        buf = np.frombuffer(raw, dtype=np.uint8)
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except Exception:
        return None


def _empty_result(reason: str) -> dict[str, Any]:
    scores = {name: 0.0 for name in _EMOTIONS}
    return {
        "worker_mode": "remote_worker",
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
