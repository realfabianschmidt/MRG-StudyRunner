"""Sensor and hardware endpoints: hardware config, integration toggles,
BrainBit/radar/camera runtime actions, camera frames, Emotion Worker repair."""
import json

from flask import Blueprint, current_app, jsonify, request

from study_runner.integrations.registry import (
    apply_enabled_runtime,
    get_plugin_status,
    initialize_plugin,
    run_runtime_action,
)
from ..services.hardware_settings_service import save_hardware_config
from ..services.secrets_service import redact_hardware_config
from ..services.study_sensor_runtime import SESSION_OVERRIDE_KEYS, STUDY_SENSOR_KEYS
from .helpers import (
    _apply_integration_toggle_to_active_runtime,
    _apply_session_override_runtime,
    _copy_config,
    _integration_context,
    _rebuild_active_study_runtime_config,
    _refresh_trial_runtime,
    _save_hardware_secret_payload,
    _sensor_runtime_state,
    _session_overrides,
    _set_runtime_enabled,
    _set_session_override,
)

bp = Blueprint("sensors", __name__)


@bp.route("/api/hardware-config")
def get_hardware_config():
    return jsonify(redact_hardware_config(current_app.config.get("HARDWARE_CONFIG", {}), current_app.config.get("LOCAL_SECRETS", {})))


@bp.route("/api/hardware-config", methods=["POST"])
def update_hardware_config():
    config_data = request.get_json()
    if not isinstance(config_data, dict):
        return jsonify({"ok": False, "error": "hardware_config payload must be a JSON object."}), 400

    sanitized_config, _secret_updated = _save_hardware_secret_payload(config_data)
    save_hardware_config(current_app.config["HARDWARE_CONFIG_FILE"], sanitized_config)
    current_app.config["HARDWARE_CONFIG"] = sanitized_config
    _refresh_trial_runtime()
    initialize_plugin("notion", _integration_context())

    return jsonify(
        {
            "ok": True,
            "restart_required": True,
            "message": "Hardware config saved. Secrets stay backend-local. Notion was refreshed immediately; restart is recommended for startup integrations.",
            "notion_runtime": get_plugin_status("notion", _integration_context()),
        }
    )


@bp.route("/api/admin/integrations/<integration_key>/enabled", methods=["POST"])
def update_integration_enabled(integration_key: str):
    payload = request.get_json() or {}
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"ok": False, "error": "enabled must be true or false."}), 400

    if integration_key in SESSION_OVERRIDE_KEYS:
        try:
            _set_session_override(integration_key, enabled)
        except ValueError as error:
            return jsonify({"ok": False, "error": str(error)}), 400
        runtime_result = _apply_session_override_runtime(integration_key, enabled)
        return jsonify(
            {
                "ok": True,
                "integration": integration_key,
                "enabled": enabled,
                "restart_required": False,
                "active_runtime_updated": isinstance(current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG"), dict),
                "study_controlled": False,
                "temporary_override": True,
                "session_overrides": _session_overrides(),
                "sensor_runtime": _sensor_runtime_state(),
                "runtime": runtime_result,
                "runtime_status": get_plugin_status(integration_key, _integration_context()),
            }
        )

    active_config = current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG")
    hardware_config = _copy_config(current_app.config.get("HARDWARE_CONFIG", {}))
    try:
        _set_runtime_enabled(hardware_config, integration_key, enabled)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400

    save_hardware_config(current_app.config["HARDWARE_CONFIG_FILE"], hardware_config)
    current_app.config["HARDWARE_CONFIG"] = hardware_config
    active_runtime_updated = _apply_integration_toggle_to_active_runtime(integration_key, enabled)
    study_controlled = bool(active_config) and integration_key in STUDY_SENSOR_KEYS and not active_runtime_updated
    if not active_runtime_updated:
        _refresh_trial_runtime()

    try:
        if not study_controlled:
            apply_enabled_runtime(integration_key, enabled, _integration_context())
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400

    return jsonify(
        {
            "ok": True,
            "integration": integration_key,
            "enabled": enabled,
            "restart_required": False,
            "active_runtime_updated": active_runtime_updated,
            "study_controlled": study_controlled,
            "runtime_status": get_plugin_status(integration_key, _integration_context()),
        }
    )


def _run_integration_action_json(integration_key: str, action: str):
    try:
        normalized_action = str(action or "").strip().lower()
        if integration_key in STUDY_SENSOR_KEYS and normalized_action in {"start", "stop", "restart"}:
            _set_session_override(integration_key, normalized_action != "stop")
            _rebuild_active_study_runtime_config()
        coordinator = current_app.config.get("SENSOR_COORDINATOR")
        if coordinator:
            result = coordinator.run_action(integration_key, action, _integration_context())
        else:
            result = run_runtime_action(integration_key, action, _integration_context())
        if integration_key == "camera_emotion" and str(action).strip().lower() == "stop":
            current_app.config["CAMERA_PREVIEW_ACTIVE"] = False
        result["temporary_override"] = integration_key in STUDY_SENSOR_KEYS
        result["sensor_runtime"] = _sensor_runtime_state()
        return jsonify(result)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500


@bp.route("/api/admin/integrations/<integration_key>/<action>", methods=["POST"])
def run_integration_runtime_action(integration_key: str, action: str):
    return _run_integration_action_json(integration_key, action)


@bp.route("/api/admin/brainbit/start", methods=["POST"])
def start_brainbit():
    return _run_integration_action_json("brainbit", "start")


@bp.route("/api/admin/brainbit/stop", methods=["POST"])
def stop_brainbit():
    return _run_integration_action_json("brainbit", "stop")


@bp.route("/api/admin/brainbit/restart", methods=["POST"])
def restart_brainbit():
    return _run_integration_action_json("brainbit", "restart")


@bp.route("/api/admin/brainbit/select-device", methods=["POST"])
def select_brainbit_device():
    if current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG"):
        return jsonify(
            {
                "ok": True,
                "study_controlled": True,
                "message": "BrainBit band selection is locked while a study is running.",
                "status": get_plugin_status("brainbit", _integration_context()),
            }
        )

    payload = request.get_json() or {}
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "payload must be a JSON object."}), 400

    serial_number = str(payload.get("serial_number") or payload.get("serial") or "").strip()
    device_address = str(payload.get("device_address") or payload.get("address") or "").strip()
    device_name = str(payload.get("device_name") or payload.get("name") or "").strip()
    raw_index = payload.get("device_index", payload.get("index"))
    device_index = None
    if raw_index not in (None, ""):
        try:
            device_index = int(raw_index)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "device index must be an integer."}), 400

    if not any((serial_number, device_address, device_name, device_index is not None)):
        return jsonify({"ok": False, "error": "No BrainBit device identity was provided."}), 400

    hardware_config = json.loads(json.dumps(current_app.config.get("HARDWARE_CONFIG", {})))
    brainbit_config = hardware_config.setdefault("brainbit", {})
    if not isinstance(brainbit_config, dict):
        brainbit_config = {}
        hardware_config["brainbit"] = brainbit_config
    brainbit_config["serial_number"] = serial_number
    brainbit_config["device_address"] = device_address
    brainbit_config["device_name"] = device_name
    if device_index is not None:
        brainbit_config["device_index"] = device_index

    save_hardware_config(current_app.config["HARDWARE_CONFIG_FILE"], hardware_config)
    current_app.config["HARDWARE_CONFIG"] = hardware_config
    _refresh_trial_runtime()

    restart_result = None
    try:
        restart_result = run_runtime_action("brainbit", "restart", _integration_context())
    except Exception as error:
        return jsonify(
            {
                "ok": True,
                "restart_required": False,
                "restart_error": str(error),
                "target_device": {
                    "serial_number": serial_number,
                    "address": device_address,
                    "name": device_name,
                    "index": device_index,
                },
            }
        )

    return jsonify(
        {
            "ok": True,
            "restart_required": False,
            "target_device": {
                "serial_number": serial_number,
                "address": device_address,
                "name": device_name,
                "index": device_index,
            },
            "restart": restart_result,
        }
    )


@bp.route("/api/admin/radar/start", methods=["POST"])
def start_mini_radar():
    return _run_integration_action_json("mini_radar", "start")


@bp.route("/api/admin/radar/stop", methods=["POST"])
def stop_mini_radar():
    return _run_integration_action_json("mini_radar", "stop")


@bp.route("/api/admin/radar/restart", methods=["POST"])
def restart_mini_radar():
    return _run_integration_action_json("mini_radar", "restart")


@bp.route("/api/camera/frame", methods=["POST"])
def process_camera_frame():
    from study_runner.integrations.tablet_camera_emotion import adapter as camera_affect_adapter

    frame_result = camera_affect_adapter.process_frame(request.get_json() or {})
    return jsonify({"ok": bool(frame_result.get("accepted", False)), **frame_result})


@bp.route("/api/admin/camera/start", methods=["POST"])
def start_camera_affect():
    return _run_integration_action_json("camera_emotion", "start")


@bp.route("/api/admin/camera/stop", methods=["POST"])
def stop_camera_affect():
    return _run_integration_action_json("camera_emotion", "stop")


@bp.route("/api/admin/camera/live/status")
def camera_live_status():
    from study_runner.integrations.tablet_camera_emotion import adapter as camera_affect_adapter

    return jsonify(
        {
            "ok": True,
            "active": bool(current_app.config.get("CAMERA_PREVIEW_ACTIVE", False)),
            **camera_affect_adapter.get_preview_status(),
        }
    )


@bp.route("/api/admin/emotion-worker/repair-runtime", methods=["POST"])
def repair_emotion_worker_runtime():
    try:
        from study_runner.integrations.local_emotion_worker import plugin as emotion_worker_plugin

        result = emotion_worker_plugin.repair_runtime(_integration_context())
        return jsonify({"ok": True, **result})
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500


@bp.route("/api/admin/emotion-worker/install-dependencies", methods=["POST"])
def install_emotion_worker_dependencies():
    try:
        from study_runner.integrations.local_emotion_worker import plugin as emotion_worker_plugin

        result = emotion_worker_plugin.install_dependencies(_integration_context())
        return jsonify({"ok": True, **result})
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500
