"""
Emotion Worker — standalone Flask server for DeepFace emotion analysis.

Run on Mac or Windows alongside LabRecorder.
The Pi Flask server forwards camera frames here for heavy emotion processing.

Usage:
    python server.py [--port 3001] [--lsl] [--lsl-stream CameraEmotion]

Endpoints:
    GET  /status         Health check — returns {"ready": true, "worker_mode": "remote_worker"}
    POST /analyze        Accept frame payload, return emotion analysis dict
"""
from __future__ import annotations

import argparse
import sys
import time

from flask import Flask, jsonify, request


def create_app(lsl: bool = False, lsl_stream: str = "CameraEmotion") -> Flask:
    from analyzer import analyze_frame, init_lsl

    if lsl:
        init_lsl(stream_name=lsl_stream)

    app = Flask(__name__)

    @app.route("/status")
    def status():
        return jsonify({
            "ready": True,
            "worker_mode": "remote_worker",
            "lsl_enabled": lsl,
            "lsl_stream": lsl_stream if lsl else None,
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
    parser = argparse.ArgumentParser(description="MRG Study Runner — Emotion Worker")
    parser.add_argument("--port", type=int, default=3001, help="Port to listen on (default: 3001)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    parser.add_argument("--lsl", action="store_true", help="Publish emotion scores as LSL stream")
    parser.add_argument("--lsl-stream", default="CameraEmotion", help="LSL stream name (default: CameraEmotion)")
    args = parser.parse_args()

    print(f"[EmotionWorker] Starting on {args.host}:{args.port}")
    print(f"[EmotionWorker] LSL: {'enabled — stream: ' + args.lsl_stream if args.lsl else 'disabled'}")
    print(f"[EmotionWorker] Pi should forward frames to: http://<this-machine>:{args.port}/analyze")

    # Warm up DeepFace (loads model weights on first call)
    print("[EmotionWorker] Pre-loading DeepFace model...")
    try:
        from deepface import DeepFace
        import numpy as np
        import cv2
        dummy = np.zeros((100, 100, 3), dtype=np.uint8)
        DeepFace.analyze(dummy, actions=["emotion"], enforce_detection=False, silent=True)
        print("[EmotionWorker] DeepFace ready")
    except Exception as exc:
        print(f"[EmotionWorker] DeepFace warmup failed: {exc}", file=sys.stderr)

    app = create_app(lsl=args.lsl, lsl_stream=args.lsl_stream)
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
