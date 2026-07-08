"""
Emotion Worker - standalone Flask server for local DeepFace emotion analysis.

Run on Windows, macOS, or Linux alongside Study Runner. The tablet study page sends
selfie-camera frames to Study Runner, and Study Runner forwards enabled frames here.

Usage:
    python server.py [--port 3001] [--lsl] [--lsl-stream CameraEmotion]

Endpoints:
    GET  /status         Health check, returns {"ready": true, "worker_mode": "local_worker"}
    POST /analyze        Accept frame payload, return emotion analysis dict
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from flask import Flask, jsonify, request


DEEPFACE_EMOTION_MODEL_NAME = "facial_expression_model_weights.h5"
DEEPFACE_EMOTION_MODEL_URL = (
    "https://github.com/serengil/deepface_models/releases/download/v1.0/"
    "facial_expression_model_weights.h5"
)

MODEL_STATE = {
    "model_checked": False,
    "model_ready": False,
    "model_error": None,
    "model_error_class": None,
    "model_asset_name": DEEPFACE_EMOTION_MODEL_NAME,
    "model_asset_url": DEEPFACE_EMOTION_MODEL_URL,
    "model_asset_path": "",
    "suggested_action": None,
}


def create_app(lsl: bool = False, lsl_stream: str = "CameraEmotion") -> Flask:
    try:
        from .analyzer import analyze_frame, init_lsl
    except ImportError:
        from analyzer import analyze_frame, init_lsl

    if lsl:
        init_lsl(stream_name=lsl_stream)

    app = Flask(__name__)

    @app.route("/status")
    def status():
        model_error = MODEL_STATE.get("model_error")
        return jsonify({
            "ready": True,
            "worker_mode": "local_worker",
            "lsl_enabled": lsl,
            "lsl_stream": lsl_stream if lsl else None,
            "model_checked": bool(MODEL_STATE.get("model_checked")),
            "model_ready": bool(MODEL_STATE.get("model_ready")),
            "model_error": model_error,
            "model_error_class": MODEL_STATE.get("model_error_class"),
            "model_asset_name": MODEL_STATE.get("model_asset_name"),
            "model_asset_url": MODEL_STATE.get("model_asset_url"),
            "model_asset_path": MODEL_STATE.get("model_asset_path"),
            "suggested_action": MODEL_STATE.get("suggested_action"),
            "message": (
                "Local Emotion Worker ready."
                if not model_error
                else "Local Emotion Worker is running, but DeepFace is not ready."
            ),
        })

    @app.route("/analyze", methods=["POST"])
    def analyze():
        payload = request.get_json(force=True) or {}
        result = analyze_frame(payload)
        result["server_received_at"] = time.time()
        return jsonify(result)

    @app.errorhandler(Exception)
    def handle_error(exc: Exception):
        return jsonify({"error": str(exc)}), 500

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="MRG Study Runner - local Emotion Worker")
    parser.add_argument("--port", type=int, default=3001, help="Port to listen on (default: 3001)")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)")
    parser.add_argument("--lsl", action="store_true", help="Publish emotion scores as LSL stream")
    parser.add_argument("--lsl-stream", default="CameraEmotion", help="LSL stream name (default: CameraEmotion)")
    args = parser.parse_args()

    print(f"[EmotionWorker] Starting on {args.host}:{args.port}")
    print(f"[EmotionWorker] LSL: {'enabled - stream: ' + args.lsl_stream if args.lsl else 'disabled'}")
    print(f"[EmotionWorker] Study Runner should forward frames to: http://{args.host}:{args.port}/analyze")

    _warmup_deepface()

    app = create_app(lsl=args.lsl, lsl_stream=args.lsl_stream)
    app.run(host=args.host, port=args.port, threaded=True)


def _warmup_deepface() -> None:
    print("[EmotionWorker] Pre-loading DeepFace model...")
    MODEL_STATE["model_checked"] = True
    MODEL_STATE["model_asset_path"] = str(_deepface_model_path())
    try:
        from deepface import DeepFace
        import numpy as np

        dummy = np.zeros((100, 100, 3), dtype=np.uint8)
        DeepFace.analyze(dummy, actions=["emotion"], enforce_detection=False, silent=True)
        MODEL_STATE["model_ready"] = True
        MODEL_STATE["model_error"] = None
        MODEL_STATE["model_error_class"] = None
        MODEL_STATE["suggested_action"] = None
        print("[EmotionWorker] DeepFace ready")
    except Exception as exc:
        details = _classify_model_error(str(exc))
        MODEL_STATE["model_ready"] = False
        MODEL_STATE["model_error"] = str(exc)
        MODEL_STATE.update(details)
        print(f"[EmotionWorker] DeepFace warmup failed: {exc}", file=sys.stderr)


def _classify_model_error(error: str) -> dict[str, str]:
    normalized = error.lower()
    asset_path = str(_deepface_model_path())
    if "no module named" in normalized or "tf-keras" in normalized or ("tensorflow" in normalized and "requires" in normalized):
        return {
            "model_error_class": "missing_package",
            "model_asset_name": DEEPFACE_EMOTION_MODEL_NAME,
            "model_asset_url": DEEPFACE_EMOTION_MODEL_URL,
            "model_asset_path": asset_path,
            "suggested_action": "Run 'pip install -r software/requirements.txt', then restart the Local Emotion Worker.",
        }
    if DEEPFACE_EMOTION_MODEL_NAME.lower() in normalized and "downloading" in normalized:
        return {
            "model_error_class": "model_download_failed",
            "model_asset_name": DEEPFACE_EMOTION_MODEL_NAME,
            "model_asset_url": DEEPFACE_EMOTION_MODEL_URL,
            "model_asset_path": asset_path,
            "suggested_action": (
                "Run the dashboard action 'Repair DeepFace runtime', or manually download "
                f"{DEEPFACE_EMOTION_MODEL_NAME} to {asset_path}."
            ),
        }
    if DEEPFACE_EMOTION_MODEL_NAME.lower() in normalized:
        return {
            "model_error_class": "model_file_missing",
            "model_asset_name": DEEPFACE_EMOTION_MODEL_NAME,
            "model_asset_url": DEEPFACE_EMOTION_MODEL_URL,
            "model_asset_path": asset_path,
            "suggested_action": (
                "Run the dashboard action 'Repair DeepFace runtime', or manually place "
                f"{DEEPFACE_EMOTION_MODEL_NAME} at {asset_path}."
            ),
        }
    return {
        "model_error_class": "model_warmup_failed",
        "model_asset_name": DEEPFACE_EMOTION_MODEL_NAME,
        "model_asset_url": DEEPFACE_EMOTION_MODEL_URL,
        "model_asset_path": asset_path,
        "suggested_action": "Run the dashboard action 'Repair DeepFace runtime', then restart the Local Emotion Worker.",
    }


def _deepface_model_path() -> Path:
    # DEEPFACE_HOME is the PARENT of `.deepface`; DeepFace stores weights under
    # `<DEEPFACE_HOME>/.deepface/weights`. Mirror that exactly so the path we
    # report/echo in errors matches where DeepFace actually looks.
    deepface_home = Path(os.environ.get("DEEPFACE_HOME") or Path.home())
    if deepface_home.name == ".deepface":
        deepface_home = deepface_home.parent
    return deepface_home / ".deepface" / "weights" / DEEPFACE_EMOTION_MODEL_NAME


if __name__ == "__main__":
    main()
