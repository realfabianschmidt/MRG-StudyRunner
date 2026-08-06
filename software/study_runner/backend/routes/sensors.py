"""Sensor and hardware endpoints: hardware config, integration toggles,
BrainBit/radar/camera runtime actions, camera frames, Emotion Worker repair."""
import json

from flask import Blueprint, current_app, jsonify, request
from werkzeug.exceptions import BadRequest, Forbidden, UnsupportedMediaType

from study_runner.plugin_framework.registry import (
    apply_enabled_runtime,
    get_plugin_status,
    ingest_participant_payload,
    initialize_plugin,
    run_admin_action,
    run_participant_action,
    run_runtime_action,
)
from ..services.settings.hardware_settings_service import save_hardware_config
from ..services.settings.secrets_service import redact_hardware_config
from ..services.settings.plugin_settings_service import (
    PluginSettingsError,
    apply_plugin_settings,
    build_plugin_settings_schema,
)
from ..services.recording.study_sensor_runtime import SESSION_OVERRIDE_KEYS, STUDY_SENSOR_KEYS
from .helpers import (
    _apply_plugin_toggle_to_active_runtime,
    _apply_session_override_runtime,
    _copy_config,
    _hardware_disabled,
    _plugin_context,
    _rebuild_active_study_runtime_config,
    _refresh_trial_runtime,
    _request_json_object,
    _require_secure_participant_ingest,
    _save_hardware_secret_payload,
    _sensor_runtime_state,
    _session_overrides,
    _set_runtime_enabled,
    _set_session_override,
)

bp = Blueprint("sensors", __name__)


def _mark_deprecated(response, successor: str):
    """Annotate a fixed-key compatibility route without changing its payload."""

    flask_response = response[0] if isinstance(response, tuple) else response
    flask_response.headers["Deprecation"] = "true"
    flask_response.headers["Warning"] = (
        f'299 Study-Runner "Deprecated compatibility route; use {successor}"'
    )
    flask_response.headers["Link"] = f'<{successor}>; rel="successor-version"'
    return response


@bp.route("/api/hardware-config")
def get_hardware_config():
    return jsonify(redact_hardware_config(current_app.config.get("HARDWARE_CONFIG", {}), current_app.config.get("LOCAL_SECRETS", {})))


@bp.route("/api/admin/plugin-settings", methods=["GET"])
def get_plugin_settings():
    """Schema plus the values actually in force right now.

    The effective-value rule (disk wins, manifest default only fills a missing
    key) lives here on the backend so the UI never has to merge the two.
    """
    return jsonify({
        "ok": True,
        "plugins": build_plugin_settings_schema(current_app.config.get("HARDWARE_CONFIG", {})),
    })


@bp.route("/api/admin/plugin-settings/<plugin_key>", methods=["POST"])
def update_plugin_settings(plugin_key):
    """Write only the named settings, deep-merged into the existing config.

    Deliberately not the whole-document POST /api/hardware-config: a per-panel
    save through that route would wipe every sibling key it did not know about.
    """
    payload = request.get_json(silent=True) or {}
    updates = payload.get("settings")
    if not isinstance(updates, dict) or not updates:
        return jsonify({"ok": False, "error": "settings must be a non-empty object."}), 400

    try:
        updated_config, restart_required = apply_plugin_settings(
            current_app.config.get("HARDWARE_CONFIG", {}),
            plugin_key,
            updates,
        )
    except PluginSettingsError as error:
        return jsonify({"ok": False, "error": str(error)}), 400

    save_hardware_config(current_app.config["HARDWARE_CONFIG_FILE"], updated_config)
    current_app.config["HARDWARE_CONFIG"] = updated_config
    _refresh_trial_runtime()

    # initialize_plugin() from a route bypasses the startup hardware guard, so
    # a settings save must not be able to start real devices during tests.
    if not restart_required and not _hardware_disabled():
        try:
            initialize_plugin(plugin_key, _plugin_context())
        except Exception as error:
            print(f"[SETTINGS] Could not re-initialize {plugin_key}: {error}")

    return jsonify({
        "ok": True,
        "restart_required": restart_required,
        "plugins": build_plugin_settings_schema(updated_config),
    })


@bp.route("/api/hardware-config", methods=["POST"])
def update_hardware_config():
    config_data = request.get_json()
    if not isinstance(config_data, dict):
        return jsonify({"ok": False, "error": "hardware_config payload must be a JSON object."}), 400

    sanitized_config, _secret_updated = _save_hardware_secret_payload(config_data)
    save_hardware_config(current_app.config["HARDWARE_CONFIG_FILE"], sanitized_config)
    current_app.config["HARDWARE_CONFIG"] = sanitized_config
    _refresh_trial_runtime()
    initialize_plugin("notion", _plugin_context())

    return jsonify(
        {
            "ok": True,
            "restart_required": True,
            "message": "Hardware config saved. Secrets stay backend-local. Notion was refreshed immediately; restart is recommended for plugins that start with the server.",
            "notion_runtime": get_plugin_status("notion", _plugin_context()),
        }
    )


@bp.route("/api/admin/plugins/<plugin_key>/enabled", methods=["POST"])
def update_plugin_enabled(plugin_key: str):
    payload = request.get_json() or {}
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"ok": False, "error": "enabled must be true or false."}), 400

    if plugin_key in SESSION_OVERRIDE_KEYS:
        try:
            _set_session_override(plugin_key, enabled)
        except ValueError as error:
            return jsonify({"ok": False, "error": str(error)}), 400
        runtime_result = _apply_session_override_runtime(plugin_key, enabled)
        return jsonify(
            {
                "ok": True,
                "plugin": plugin_key,
                "enabled": enabled,
                "restart_required": False,
                "active_runtime_updated": isinstance(current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG"), dict),
                "study_controlled": False,
                "temporary_override": True,
                "session_overrides": _session_overrides(),
                "sensor_runtime": _sensor_runtime_state(),
                "runtime": runtime_result,
                "runtime_status": get_plugin_status(plugin_key, _plugin_context()),
            }
        )

    active_config = current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG")
    hardware_config = _copy_config(current_app.config.get("HARDWARE_CONFIG", {}))
    try:
        _set_runtime_enabled(hardware_config, plugin_key, enabled)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400

    save_hardware_config(current_app.config["HARDWARE_CONFIG_FILE"], hardware_config)
    current_app.config["HARDWARE_CONFIG"] = hardware_config
    active_runtime_updated = _apply_plugin_toggle_to_active_runtime(plugin_key, enabled)
    study_controlled = bool(active_config) and plugin_key in STUDY_SENSOR_KEYS and not active_runtime_updated
    if not active_runtime_updated:
        _refresh_trial_runtime()

    try:
        if not study_controlled:
            apply_enabled_runtime(plugin_key, enabled, _plugin_context())
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400

    return jsonify(
        {
            "ok": True,
            "plugin": plugin_key,
            "enabled": enabled,
            "restart_required": False,
            "active_runtime_updated": active_runtime_updated,
            "study_controlled": study_controlled,
            "runtime_status": get_plugin_status(plugin_key, _plugin_context()),
        }
    )


def _run_plugin_action_json(plugin_key: str, action: str):
    try:
        normalized_action = str(action or "").strip().lower()
        if plugin_key in STUDY_SENSOR_KEYS and normalized_action in {"start", "stop", "restart"}:
            _set_session_override(plugin_key, normalized_action != "stop")
            _rebuild_active_study_runtime_config()
        coordinator = current_app.config.get("SENSOR_COORDINATOR")
        if coordinator:
            result = coordinator.run_action(plugin_key, action, _plugin_context())
        else:
            result = run_runtime_action(plugin_key, action, _plugin_context())
        result["temporary_override"] = plugin_key in STUDY_SENSOR_KEYS
        result["sensor_runtime"] = _sensor_runtime_state()
        return jsonify(result)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500


@bp.route("/api/admin/plugins/<plugin_key>/<action>", methods=["POST"])
def run_plugin_runtime_action(plugin_key: str, action: str):
    return _run_plugin_action_json(plugin_key, action)


@bp.route("/api/admin/brainbit/start", methods=["POST"])
def start_brainbit():
    return _run_plugin_action_json("brainbit", "start")


@bp.route("/api/admin/brainbit/stop", methods=["POST"])
def stop_brainbit():
    return _run_plugin_action_json("brainbit", "stop")


@bp.route("/api/admin/brainbit/restart", methods=["POST"])
def restart_brainbit():
    return _run_plugin_action_json("brainbit", "restart")


@bp.route("/api/admin/brainbit/select-device", methods=["POST"])
def select_brainbit_device():
    try:
        payload = _request_json_object()
        return jsonify(
            run_admin_action(
                "brainbit",
                "select_device",
                _plugin_context(machine_admin=True),
                payload,
            )
        )
    except UnsupportedMediaType as error:
        return jsonify({"ok": False, "error": error.description}), 415
    except BadRequest as error:
        return jsonify({"ok": False, "error": error.description}), 400
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500


@bp.route("/api/admin/radar/start", methods=["POST"])
def start_mini_radar():
    return _run_plugin_action_json("mini_radar", "start")


@bp.route("/api/admin/radar/stop", methods=["POST"])
def stop_mini_radar():
    return _run_plugin_action_json("mini_radar", "stop")


@bp.route("/api/admin/radar/restart", methods=["POST"])
def restart_mini_radar():
    return _run_plugin_action_json("mini_radar", "restart")


@bp.route("/api/camera/frame", methods=["POST"])
def process_camera_frame():
    """Deprecated fixed-key shim for pre-v3 participant clients."""

    successor = "/api/plugins/camera_emotion/participant/ingest/frame"
    try:
        _require_secure_participant_ingest("camera_emotion")
        dispatched = ingest_participant_payload(
            "camera_emotion",
            "frame",
            _plugin_context(),
            _request_json_object(),
        )
        frame_result = dispatched.get("result") or {}
        return _mark_deprecated(
            jsonify({"ok": bool(dispatched.get("ok", False)), **frame_result}),
            successor,
        )
    except (Forbidden, UnsupportedMediaType, BadRequest) as error:
        status = (
            403
            if isinstance(error, Forbidden)
            else 415
            if isinstance(error, UnsupportedMediaType)
            else 400
        )
        return _mark_deprecated(
            (jsonify({"ok": False, "error": error.description}), status),
            successor,
        )
    except ValueError as error:
        return _mark_deprecated(
            (jsonify({"ok": False, "error": str(error)}), 400),
            successor,
        )


@bp.route("/api/admin/camera/start", methods=["POST"])
def start_camera_affect():
    """Deprecated fixed-key shim for the generic runtime action route."""

    return _mark_deprecated(
        _run_plugin_action_json("camera_emotion", "start"),
        "/api/admin/plugins/camera_emotion/start",
    )


@bp.route("/api/admin/camera/stop", methods=["POST"])
def stop_camera_affect():
    """Deprecated fixed-key shim for the generic runtime action route."""

    return _mark_deprecated(
        _run_plugin_action_json("camera_emotion", "stop"),
        "/api/admin/plugins/camera_emotion/stop",
    )


@bp.route("/api/admin/camera/live/status")
def camera_live_status():
    """Deprecated fixed-key shim; status is now owned by the plugin."""

    successor = "/api/admin/status"
    status = get_plugin_status("camera_emotion", _plugin_context())
    preview = status.get("preview") or {}
    return _mark_deprecated(
        jsonify({"ok": True, "active": bool(preview.get("active", False)), **preview}),
        successor,
    )


@bp.route("/api/study/camera-monitor/start", methods=["POST"])
def start_study_camera_monitor():
    """Deprecated fixed-key shim for pre-v3 participant extensions."""

    successor = "/api/plugins/camera_emotion/participant/actions/start_monitor"
    try:
        dispatched = run_participant_action(
            "camera_emotion",
            "start_monitor",
            _plugin_context(),
            _request_json_object(),
        )
        return _mark_deprecated(
            jsonify({"ok": True, "runtime": dispatched.get("result")}),
            successor,
        )
    except (UnsupportedMediaType, BadRequest) as error:
        status = 415 if isinstance(error, UnsupportedMediaType) else 400
        return _mark_deprecated(
            (jsonify({"ok": False, "error": error.description}), status),
            successor,
        )
    except ValueError as error:
        return _mark_deprecated(
            (jsonify({"ok": False, "error": str(error)}), 400),
            successor,
        )


@bp.route("/api/admin/emotion-worker/repair-runtime", methods=["POST"])
def repair_emotion_worker_runtime():
    try:
        from study_runner.plugins.camera_emotion.worker import plugin as emotion_worker_plugin

        result = emotion_worker_plugin.repair_runtime(_plugin_context())
        return jsonify({"ok": True, **result})
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500


@bp.route("/api/admin/emotion-worker/install-dependencies", methods=["POST"])
def install_emotion_worker_dependencies():
    try:
        from study_runner.plugins.camera_emotion.worker import plugin as emotion_worker_plugin

        result = emotion_worker_plugin.install_dependencies(_plugin_context())
        return jsonify({"ok": True, **result})
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500
