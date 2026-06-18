from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from ..plugin_api import IntegrationContext, IntegrationPlugin


EMOTION_WORKER_MODES = {"local_worker", "remote_worker"}


def _status(context: IntegrationContext) -> dict[str, Any]:
    config = context.hardware_config.get("camera_emotion") or context.hardware_config.get("camera") or {}
    config = config if isinstance(config, dict) else {}
    worker_mode = str(config.get("worker_mode") or "local_worker")
    worker_url = str(config.get("emotion_worker_url") or "").rstrip("/")
    configured_enabled = bool(config.get("enabled", False))
    worker_enabled = configured_enabled and worker_mode in EMOTION_WORKER_MODES

    status = {
        "configured_enabled": worker_enabled,
        "runtime_enabled": False,
        "enabled": worker_enabled,
        "status": "disabled" if not worker_enabled else "unknown",
        "worker_mode": worker_mode,
        "url": worker_url,
        "last_message": "Emotion Worker is disabled or camera emotion is not using worker mode.",
        "device_label": "Local Emotion Worker",
    }
    if not worker_enabled:
        return status
    if not worker_url:
        return {**status, "status": "not_configured", "last_message": "camera_emotion.emotion_worker_url is not configured."}

    request = urllib.request.Request(f"{worker_url}/status", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=0.35) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as error:
        return {**status, "status": "unreachable", "last_message": f"Could not reach local Emotion Worker: {error}"}

    if not isinstance(payload, dict):
        payload = {}
    ready = bool(payload.get("ready", payload.get("ok", False)))
    return {
        **status,
        "runtime_enabled": ready,
        "status": "connected" if ready else "starting",
        "connected": ready,
        "last_message": str(payload.get("message") or ("Emotion Worker ready." if ready else "Emotion Worker responded but is not ready.")),
        "latest": payload,
    }


PLUGIN = IntegrationPlugin(
    key="emotion_worker",
    label="Local Emotion Worker",
    category="processing",
    config_key="camera_emotion",
    can_toggle=False,
    get_status=_status,
)
