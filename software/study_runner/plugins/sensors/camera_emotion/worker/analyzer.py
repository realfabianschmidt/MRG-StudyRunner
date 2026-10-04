"""
Emotion analysis module for the local Emotion Worker.

Accepts a frame payload from the generic participant-ingest route and returns an
analysis dict compatible with camera_affect_adapter's expected shape.
"""
from __future__ import annotations

import base64
import math
from typing import Any

import cv2
import numpy as np

_EMOTIONS = ("angry", "disgust", "fear", "happy", "sad", "surprise", "neutral")

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
            enforce_detection=True,
            detector_backend="opencv",
            silent=True,
        )
        result = results[0] if isinstance(results, list) else results
        if not isinstance(result, dict):
            raise ValueError("DeepFace returned no face analysis")
        dominant = str(result.get("dominant_emotion", "unknown")).lower().strip()
        raw_scores = result.get("emotion", {}) or {}
        region = result.get("region", {}) or {}
        detector_score = _finite_score(result.get("face_confidence"))
        if detector_score is None or detector_score < 0:
            raise ValueError("DeepFace returned no finite detector confidence")
        if dominant not in _EMOTIONS or not isinstance(raw_scores, dict):
            raise ValueError("DeepFace returned no usable emotion analysis")
        if not set(_EMOTIONS).issubset({str(key).lower().strip() for key in raw_scores}):
            raise ValueError("DeepFace omitted emotion scores")
        scores = {name: 0.0 for name in (*_EMOTIONS, "unknown")}
        for label, value in raw_scores.items():
            normalized = str(label).lower().strip()
            if normalized in _EMOTIONS:
                score = _finite_score(value)
                if score is None or not 0 <= score <= 100:
                    raise ValueError("DeepFace returned an invalid emotion score")
                scores[normalized] = score / 100.0
        if dominant not in {str(key).lower().strip() for key in raw_scores}:
            raise ValueError("DeepFace omitted its dominant emotion score")
    except Exception as exc:
        if _is_no_face_error(exc):
            return _no_face_result()
        return _empty_result(f"DeepFace error: {exc}")

    confidence = scores.get(dominant, 0.0)
    analysis = {
        "worker_mode": "local_worker",
        "analysis_status": "ok",
        "detection_valid": True,
        "emotion_valid": True,
        "face_detected": True,
        "emotion": dominant,
        "confidence": round(confidence, 4),
        "face_confidence": detector_score,
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

    return analysis


def _finite_score(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return score if math.isfinite(score) else None


def _is_no_face_error(error: Exception) -> bool:
    # DeepFace 0.0.92 raises this ValueError when enforce_detection is True.
    return isinstance(error, ValueError) and str(error).startswith("Face could not be detected.")


def _no_face_result() -> dict[str, Any]:
    return {
        "worker_mode": "local_worker",
        "analysis_status": "no_face",
        "detection_valid": True,
        "emotion_valid": False,
        "face_detected": False,
        "emotion": "unknown",
        "confidence": None,
        "face_confidence": None,
        "scores": {},
        "overlay": {},
    }


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
    return {
        "worker_mode": "local_worker",
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
    }
