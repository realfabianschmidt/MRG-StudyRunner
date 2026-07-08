import json
import os
import subprocess
import sys
import threading
import time
import uuid

from flask import Flask, current_app, jsonify, request, send_from_directory

from study_runner.integrations.registry import (
    apply_enabled_runtime,
    build_context,
    get_plugin_status,
    initialize_plugin,
    run_runtime_action,
)
from .services.admin_status_service import build_admin_status
from .services.study_config_service import (
    delete_study,
    list_studies,
    load_config,
    load_study,
    save_config,
    save_study,
)
from .services.hardware_settings_service import save_hardware_config, set_integration_enabled
from .services.results_service import build_answer_details, build_biosignal_summary, save_results_payload
from .services.runtime_config import build_runtime_info
from .services.secrets_service import (
    describe_notion_api_key_source,
    describe_notion_api_key_storage,
    load_local_secrets,
    redact_hardware_config,
    resolve_notion_api_key,
    save_local_secrets,
)
from .services.study_sensor_runtime import (
    SESSION_OVERRIDE_KEYS,
    STUDY_SENSOR_KEYS,
    build_effective_hardware_config,
    build_sensor_runtime_state,
    normalize_session_overrides,
)
from .services.study_client_service import register_heartbeat
from .services.trial_service import (
    configure_runtime,
    send_trial_marker,
    start_trial_session,
    stop_trial_session,
)
from .services.update_service import (
    UpdateError,
    build_update_status,
    check_for_update,
    download_and_stage_update,
    request_update_install,
)
from .services.validation import (
    ValidationError,
    validate_and_normalize_config,
    validate_and_normalize_results,
    validate_and_normalize_trial_options,
)

ACTIVE_RUNTIME_TOGGLE_KEYS = {"lsl", "labrecorder"}


def _runtime_hardware_config() -> dict:
    return current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG") or _effective_hardware_config_for_current_study()


def _integration_context(hardware_config: dict | None = None):
    return build_context(
        base_dir=current_app.config["BASE_DIR"],
        data_dir=current_app.config["DATA_DIR"],
        hardware_config=hardware_config if hardware_config is not None else _runtime_hardware_config(),
        local_secrets=current_app.config.get("LOCAL_SECRETS", {}),
        local_secrets_file=current_app.config["LOCAL_SECRETS_FILE"],
    )


def _refresh_trial_runtime() -> None:
    configure_runtime(
        base_dir=current_app.config["BASE_DIR"],
        data_dir=current_app.config["DATA_DIR"],
        hardware_config=_runtime_hardware_config(),
        local_secrets=current_app.config.get("LOCAL_SECRETS", {}),
        local_secrets_file=current_app.config["LOCAL_SECRETS_FILE"],
    )


def _copy_config(config_data: dict | None) -> dict:
    return json.loads(json.dumps(config_data or {}))


def _current_config_data() -> dict:
    return validate_and_normalize_config(load_config(current_app.config["CONFIG_FILE"]))


def _current_study_settings() -> dict:
    return _current_config_data().get("study_settings", {})


def _session_overrides() -> dict[str, bool]:
    return normalize_session_overrides(current_app.config.get("SESSION_SENSOR_OVERRIDES", {}))


def _set_session_override(integration_key: str, enabled: bool) -> dict[str, bool]:
    if integration_key not in SESSION_OVERRIDE_KEYS:
        raise ValueError(f"Integration '{integration_key}' cannot be used as a session override.")
    overrides = _session_overrides()
    overrides[integration_key] = bool(enabled)
    current_app.config["SESSION_SENSOR_OVERRIDES"] = overrides
    return overrides


def _clear_session_overrides() -> None:
    current_app.config["SESSION_SENSOR_OVERRIDES"] = {}


def _sensor_runtime_state(study_settings: dict | None = None) -> dict:
    settings = study_settings if isinstance(study_settings, dict) else _current_study_settings()
    return build_sensor_runtime_state(
        current_app.config.get("HARDWARE_CONFIG", {}),
        settings,
        _session_overrides(),
    )


def _effective_hardware_config_for_current_study(study_settings: dict | None = None) -> dict:
    settings = study_settings if isinstance(study_settings, dict) else _current_study_settings()
    return build_effective_hardware_config(
        current_app.config.get("HARDWARE_CONFIG", {}),
        settings,
        _session_overrides(),
    )


def _set_runtime_enabled(config_data: dict, integration_key: str, enabled: bool) -> None:
    set_integration_enabled(config_data, integration_key, enabled)


def _apply_integration_toggle_to_active_runtime(integration_key: str, enabled: bool) -> bool:
    active_config = current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG")
    if not isinstance(active_config, dict) or integration_key not in ACTIVE_RUNTIME_TOGGLE_KEYS:
        return False

    active_copy = _copy_config(active_config)
    _set_runtime_enabled(active_copy, integration_key, enabled)
    current_app.config["ACTIVE_STUDY_HARDWARE_CONFIG"] = active_copy
    _refresh_trial_runtime()
    return True


def _rebuild_active_study_runtime_config(study_settings: dict | None = None) -> dict | None:
    if not isinstance(current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG"), dict):
        return None
    active_config = _effective_hardware_config_for_current_study(study_settings)
    current_app.config["ACTIVE_STUDY_HARDWARE_CONFIG"] = active_config
    _refresh_trial_runtime()
    return active_config


def _apply_session_override_runtime(integration_key: str, enabled: bool) -> dict:
    active_config = _rebuild_active_study_runtime_config()
    context = _integration_context(active_config) if isinstance(active_config, dict) else _integration_context()
    result: dict = {}

    if integration_key in STUDY_SENSOR_KEYS:
        try:
            if enabled:
                initialize_plugin(integration_key, context)
                result = run_runtime_action(integration_key, "start", context)
                active_plugins = list(current_app.config.get("ACTIVE_STUDY_SENSOR_PLUGINS") or [])
                if isinstance(active_config, dict) and integration_key not in active_plugins:
                    active_plugins.append(integration_key)
                    current_app.config["ACTIVE_STUDY_SENSOR_PLUGINS"] = active_plugins
            else:
                result = run_runtime_action(integration_key, "stop", context)
                if integration_key == "camera_emotion":
                    current_app.config["CAMERA_PREVIEW_ACTIVE"] = False
                active_plugins = [
                    key for key in list(current_app.config.get("ACTIVE_STUDY_SENSOR_PLUGINS") or [])
                    if key != integration_key
                ]
                current_app.config["ACTIVE_STUDY_SENSOR_PLUGINS"] = active_plugins
        except Exception as error:
            result = {"ok": False, "error": str(error)}
        return result

    try:
        apply_enabled_runtime(integration_key, enabled, context)
        result = {"ok": True, "integration": integration_key, "enabled": enabled}
    except Exception as error:
        result = {"ok": False, "error": str(error)}
    return result


def _valid_participant_id(value: object) -> bool:
    participant_id = str(value or "").strip()
    return bool(participant_id and participant_id.lower() != "unknown")


def _spawn_server_restart(base_dir) -> None:
    server_file = base_dir / "server.py"
    if not server_file.exists():
        server_file = base_dir / "study_runner" / "app_server.py"
    server_path = str(server_file)
    helper_code = (
        "import os, subprocess, sys, time; "
        "time.sleep(1.2); "
        f"cmd={[sys.executable, server_path]!r}; "
        f"cwd={str(base_dir)!r}; "
        "kwargs={'cwd': cwd, 'env': os.environ.copy(), 'close_fds': True}; "
        "if os.name == 'nt': "
        " kwargs['creationflags'] = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0); "
        "else: "
        " kwargs['start_new_session'] = True; "
        "subprocess.Popen(cmd, **kwargs)"
    )
    subprocess.Popen(
        [sys.executable, "-c", helper_code],
        cwd=str(base_dir),
        close_fds=True,
        env=os.environ.copy(),
    )


def _delayed_shutdown(shutdown_func) -> None:
    time.sleep(0.3)
    shutdown_func()


def _save_notion_secret_payload(config_data: dict) -> tuple[dict, bool]:
    sanitized_config = json.loads(json.dumps(config_data))
    notion_config = sanitized_config.get("notion")
    local_secrets = dict(current_app.config.get("LOCAL_SECRETS", {}))
    secret_updated = False

    if isinstance(notion_config, dict):
        provided_api_key = str(notion_config.get("api_key") or "").strip()
        if provided_api_key:
            local_secrets.setdefault("notion", {})["api_key"] = provided_api_key
            secret_updated = True

        if notion_config.get("clear_api_key"):
            local_secrets.setdefault("notion", {}).pop("api_key", None)
            if not local_secrets.get("notion"):
                local_secrets.pop("notion", None)
            secret_updated = True

        notion_config.pop("api_key", None)
        notion_config.pop("api_key_configured", None)
        notion_config.pop("api_key_source", None)
        notion_config.pop("clear_api_key", None)

    if secret_updated:
        save_local_secrets(current_app.config["LOCAL_SECRETS_FILE"], local_secrets)
        current_app.config["LOCAL_SECRETS"] = load_local_secrets(current_app.config["LOCAL_SECRETS_FILE"])

    return sanitized_config, secret_updated


def _start_study_sensor_runtime(study_settings: dict) -> dict:
    base_hardware_config = current_app.config.get("HARDWARE_CONFIG", {})
    effective_hardware_config = build_effective_hardware_config(base_hardware_config, study_settings, _session_overrides())
    runtime_state = build_sensor_runtime_state(base_hardware_config, study_settings, _session_overrides())
    selected_sensors = runtime_state["effective"]
    current_app.config["ACTIVE_STUDY_HARDWARE_CONFIG"] = effective_hardware_config
    current_app.config["ACTIVE_STUDY_SENSOR_PLUGINS"] = []
    _refresh_trial_runtime()

    context = _integration_context(effective_hardware_config)
    results: dict[str, dict] = {}
    for sensor_key in STUDY_SENSOR_KEYS:
        if selected_sensors.get(sensor_key):
            try:
                initialize_plugin(sensor_key, context)
                results[sensor_key] = run_runtime_action(sensor_key, "start", context)
                current_app.config["ACTIVE_STUDY_SENSOR_PLUGINS"].append(sensor_key)
            except Exception as error:
                results[sensor_key] = {"ok": False, "error": str(error)}
            continue

        try:
            results[sensor_key] = run_runtime_action(sensor_key, "stop", context)
        except Exception as error:
            results[sensor_key] = {"ok": False, "error": str(error)}

    return {
        "sensors": selected_sensors,
        "sensor_runtime": runtime_state,
        "active_plugins": list(current_app.config.get("ACTIVE_STUDY_SENSOR_PLUGINS", [])),
        "runtime": results,
    }


def _stop_study_sensor_runtime() -> dict:
    active_hardware_config = current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG")
    active_plugins = list(current_app.config.get("ACTIVE_STUDY_SENSOR_PLUGINS") or [])
    context = _integration_context(active_hardware_config) if active_hardware_config else _integration_context()
    results: dict[str, dict] = {}
    for sensor_key in active_plugins:
        try:
            results[sensor_key] = run_runtime_action(sensor_key, "stop", context)
        except Exception as error:
            results[sensor_key] = {"ok": False, "error": str(error)}

    current_app.config.pop("ACTIVE_STUDY_HARDWARE_CONFIG", None)
    current_app.config["ACTIVE_STUDY_SENSOR_PLUGINS"] = []
    _refresh_trial_runtime()
    return {"stopped_plugins": active_plugins, "runtime": results}


def _start_study_camera_monitor_runtime() -> dict:
    config_data = _current_config_data()
    runtime_state = _sensor_runtime_state(config_data.get("study_settings", {}))
    if not runtime_state["effective"].get("camera_emotion"):
        return {
            "ok": True,
            "skipped": True,
            "reason": "camera_emotion_not_effective",
            "sensor_runtime": runtime_state,
        }

    active_config = current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG")
    if isinstance(active_config, dict):
        context = _integration_context(active_config)
        initialize_plugin("camera_emotion", context)
        current_app.config["CAMERA_PREVIEW_ACTIVE"] = True
        return run_runtime_action("camera_emotion", "start", context)

    monitor_config = _effective_hardware_config_for_current_study(config_data.get("study_settings", {}))
    camera_config = monitor_config.setdefault("camera_emotion", {})
    if not isinstance(camera_config, dict):
        camera_config = {}
        monitor_config["camera_emotion"] = camera_config
    camera_config["enabled"] = True
    current_app.config["CAMERA_PREVIEW_HARDWARE_CONFIG"] = monitor_config
    current_app.config["CAMERA_PREVIEW_ACTIVE"] = True
    context = _integration_context(monitor_config)
    initialize_plugin("camera_emotion", context)
    return run_runtime_action("camera_emotion", "start", context)


def _study_sessions() -> dict:
    sessions = current_app.config.setdefault("STUDY_SESSIONS", {})
    return sessions if isinstance(sessions, dict) else {}


def _find_active_study_session(study_id: str, participant_id: str, client_id: str = "") -> dict | None:
    for session in _study_sessions().values():
        if session.get("status") != "active":
            continue
        if session.get("study_id") != study_id or session.get("participant_id") != participant_id:
            continue
        if client_id and session.get("client_id") != client_id:
            continue
        return session
    return None


def _start_or_reuse_study_session(payload: dict) -> dict:
    study_id = str(payload.get("study_id") or "").strip()
    participant_id = str(payload.get("participant_id") or "").strip()
    client_id = str(payload.get("client_id") or "").strip()
    existing = _find_active_study_session(study_id, participant_id, client_id)
    now = time.time()
    if existing:
        existing["last_seen"] = now
        existing["last_seen_at"] = _format_server_time(now)
        return {**existing, "reused": True}

    session_id = str(payload.get("session_id") or f"study-session-{uuid.uuid4()}").strip()
    session = {
        "session_id": session_id,
        "client_id": client_id,
        "study_id": study_id,
        "participant_id": participant_id,
        "current_index": payload.get("current_index"),
        "current_type": payload.get("current_type"),
        "status": "active",
        "started_at": _format_server_time(now),
        "last_seen": now,
        "last_seen_at": _format_server_time(now),
        "events": [],
    }
    _study_sessions()[session_id] = session
    return {**session, "reused": False}


def _resume_study_session(payload: dict) -> dict | None:
    session_id = str(payload.get("session_id") or "").strip()
    study_id = str(payload.get("study_id") or "").strip()
    participant_id = str(payload.get("participant_id") or "").strip()
    client_id = str(payload.get("client_id") or "").strip()
    session = _study_sessions().get(session_id) if session_id else None
    if not session:
        session = _find_active_study_session(study_id, participant_id, client_id)
    if not session:
        return None
    now = time.time()
    session["status"] = "active"
    session["last_seen"] = now
    session["last_seen_at"] = _format_server_time(now)
    _append_session_event(session, payload.get("event") or "study_resume_after_reload", payload)
    return session


def _record_study_client_event(payload: dict) -> dict:
    session_id = str(payload.get("session_id") or "").strip()
    study_id = str(payload.get("study_id") or "").strip()
    participant_id = str(payload.get("participant_id") or "").strip()
    client_id = str(payload.get("client_id") or "").strip()
    session = _study_sessions().get(session_id) if session_id else None
    if not session:
        session = _find_active_study_session(study_id, participant_id, client_id)
    if session is None:
        return {"recorded": False, "reason": "session_not_found"}
    _append_session_event(session, payload.get("event") or "client_event", payload)
    return {"recorded": True, "session": _public_study_session(session)}


def _append_session_event(session: dict, event: object, payload: dict) -> None:
    events = session.setdefault("events", [])
    event_name = str(event or "client_event").strip() or "client_event"
    session["current_index"] = payload.get("current_index", session.get("current_index"))
    session["current_type"] = payload.get("current_type", session.get("current_type"))
    item = {
        "event": event_name,
        "received_at": _format_server_time(time.time()),
        "current_index": payload.get("current_index"),
        "current_type": payload.get("current_type"),
        "is_stimulus_active": bool(payload.get("is_stimulus_active", False)),
    }
    if event_name in {"client_reload_or_leave", "pagehide", "beforeunload"} and item["is_stimulus_active"]:
        item["interrupted_by_reload"] = True
        session["last_interruption"] = item
    events.append(item)
    if len(events) > 50:
        del events[:-50]


def _public_study_session(session: dict | None) -> dict | None:
    if not session:
        return None
    return {
        "session_id": session.get("session_id"),
        "client_id": session.get("client_id"),
        "study_id": session.get("study_id"),
        "participant_id": session.get("participant_id"),
        "current_index": session.get("current_index"),
        "current_type": session.get("current_type"),
        "status": session.get("status"),
        "started_at": session.get("started_at"),
        "last_seen_at": session.get("last_seen_at"),
        "last_interruption": session.get("last_interruption"),
    }


def _format_server_time(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


def register_routes(app: Flask) -> None:
    configure_runtime(
        base_dir=app.config["BASE_DIR"],
        data_dir=app.config["DATA_DIR"],
        hardware_config=app.config.get("HARDWARE_CONFIG", {}),
        local_secrets=app.config.get("LOCAL_SECRETS", {}),
        local_secrets_file=app.config["LOCAL_SECRETS_FILE"],
    )

    @app.route("/")
    def study_page():
        return send_from_directory(current_app.static_folder, "pages/study.html")

    @app.route("/admin")
    def admin_page():
        return send_from_directory(current_app.static_folder, "pages/admin.html")

    @app.route("/audit")
    def audit_page():
        return send_from_directory(current_app.static_folder, "pages/audit.html")

    @app.route("/api/health")
    def health():
        return jsonify(
            {
                "ok": True,
                "status": "running",
                "app_mode": current_app.config.get("APP_MODE", "python"),
            }
        )

    @app.route("/api/runtime-info")
    def runtime_info():
        return jsonify(build_runtime_info(current_app.config))

    @app.route("/api/config")
    def get_config():
        config_data = validate_and_normalize_config(load_config(current_app.config["CONFIG_FILE"]))
        config_data["_capabilities"] = {
            "unsafe_stimulus_code": bool(current_app.config.get("ALLOW_UNSAFE_STIMULUS_CODE", False))
        }
        config_data["_runtime"] = {
            "sensor_runtime": _sensor_runtime_state(config_data.get("study_settings", {})),
            "session_overrides": _session_overrides(),
        }
        return jsonify(config_data)

    @app.route("/api/config", methods=["POST"])
    def update_config():
        config_data = request.get_json() or {}
        validated_config = validate_and_normalize_config(config_data)
        save_config(current_app.config["CONFIG_FILE"], validated_config)
        save_study(current_app.config["SAVED_STUDIES_DIR"], validated_config)
        print("[CONFIG] Saved.")
        return jsonify({"ok": True})

    @app.route("/api/study/session/start", methods=["POST"])
    def start_study_session():
        payload = request.get_json() or {}
        if not _valid_participant_id(payload.get("participant_id")):
            return jsonify({"ok": False, "error": "Participant ID is required before a study can start."}), 400
        config_data = _current_config_data()
        session = _start_or_reuse_study_session(payload)
        result = _start_study_sensor_runtime(config_data.get("study_settings", {}))
        return jsonify({"ok": True, "session": _public_study_session(session), **result})

    @app.route("/api/study/session/stop", methods=["POST"])
    def stop_study_session():
        payload = request.get_json() or {}
        session_id = str(payload.get("session_id") or "").strip()
        if session_id and session_id in _study_sessions():
            _study_sessions()[session_id]["status"] = "completed"
        result = _stop_study_sensor_runtime()
        return jsonify({"ok": True, **result})

    @app.route("/api/study/session/resume", methods=["POST"])
    def resume_study_session():
        payload = request.get_json() or {}
        if not _valid_participant_id(payload.get("participant_id")):
            return jsonify({"ok": False, "error": "Participant ID is required before a study can resume."}), 400
        session = _resume_study_session(payload)
        if session is None:
            return jsonify({"ok": False, "error": "No active study session was found for this tablet."}), 404
        return jsonify({"ok": True, "session": _public_study_session(session), "sensor_runtime": _sensor_runtime_state()})

    @app.route("/api/study/session/client-event", methods=["POST"])
    def study_session_client_event():
        payload = request.get_json() or {}
        return jsonify({"ok": True, **_record_study_client_event(payload)})

    @app.route("/api/study/runtime")
    def study_runtime():
        return jsonify(
            {
                "ok": True,
                "sensor_runtime": _sensor_runtime_state(),
                "session_overrides": _session_overrides(),
                "active_study_session": bool(current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG")),
                "camera_live_active": bool(current_app.config.get("CAMERA_PREVIEW_ACTIVE", False)),
            }
        )

    @app.route("/api/study/camera-monitor/start", methods=["POST"])
    def start_study_camera_monitor():
        try:
            result = _start_study_camera_monitor_runtime()
            return jsonify({"ok": True, "runtime": result})
        except Exception as error:
            return jsonify({"ok": False, "error": str(error)}), 400

    @app.route("/api/start", methods=["POST"])
    def start_trial():
        _refresh_trial_runtime()
        result = start_trial_session(validate_and_normalize_trial_options(request.get_json()))
        return jsonify({"ok": True, **result})

    @app.route("/api/stop", methods=["POST"])
    def stop_trial():
        _refresh_trial_runtime()
        result = stop_trial_session(validate_and_normalize_trial_options(request.get_json()))
        return jsonify({"ok": True, **result})

    @app.route("/api/marker", methods=["POST"])
    def trial_marker():
        _refresh_trial_runtime()
        payload = request.get_json() or {}
        options = validate_and_normalize_trial_options(payload)
        event = options.get("marker_event") or payload.get("event") or payload.get("phase") or "marker"
        result = send_trial_marker(str(event), options)
        return jsonify({"ok": True, **result})

    @app.route("/api/admin/restart", methods=["POST"])
    def admin_restart():
        app_mode = str(current_app.config.get("APP_MODE", "python")).strip().lower()
        if app_mode in {"desktop", "packaged"} or getattr(sys, "frozen", False):
            return jsonify(
                {
                    "ok": False,
                    "error": "Server restart is unavailable in packaged builds. Close and reopen Study Runner, or use the update restart action after staging an update.",
                }
            ), 503

        shutdown_func = request.environ.get("werkzeug.server.shutdown")
        if shutdown_func is None:
            return jsonify({"ok": False, "error": "Server restart is only available on the built-in Study Runner server."}), 503

        try:
            _spawn_server_restart(current_app.config["BASE_DIR"])
        except Exception as error:
            return jsonify({"ok": False, "error": str(error)}), 500

        threading.Thread(target=_delayed_shutdown, args=(shutdown_func,), daemon=True).start()
        return jsonify({"ok": True, "message": "Server restart requested."})

    @app.route("/api/study-client/heartbeat", methods=["POST"])
    def study_client_heartbeat():
        payload = request.get_json() or {}
        heartbeat_result = register_heartbeat(payload, request.remote_addr, request.headers.get("User-Agent", ""))
        return jsonify({"ok": True, **heartbeat_result, "sensor_runtime": _sensor_runtime_state()})

    @app.route("/api/admin/studies", methods=["GET"])
    def admin_list_studies():
        return jsonify(list_studies(current_app.config["SAVED_STUDIES_DIR"]))

    @app.route("/api/admin/studies/active", methods=["POST"])
    def admin_set_active_study():
        payload = request.get_json() or {}
        study_id = payload.get("id")
        if not study_id:
            return jsonify({"ok": False, "error": "No study ID provided"}), 400
        try:
            config_data = load_study(current_app.config["SAVED_STUDIES_DIR"], study_id)
            validated_config = validate_and_normalize_config(config_data)
            save_config(current_app.config["CONFIG_FILE"], validated_config)
            print(f"[CONFIG] Activated study: {study_id}")
            return jsonify(validated_config)
        except Exception as error:
            return jsonify({"ok": False, "error": str(error)}), 404

    @app.route("/api/admin/studies/<study_id>", methods=["GET"])
    def admin_get_study(study_id):
        try:
            return jsonify(load_study(current_app.config["SAVED_STUDIES_DIR"], study_id))
        except Exception as error:
            return jsonify({"ok": False, "error": str(error)}), 404

    @app.route("/api/admin/studies/<study_id>", methods=["DELETE"])
    def admin_delete_study(study_id):
        if delete_study(current_app.config["SAVED_STUDIES_DIR"], study_id):
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "Not found"}), 404

    @app.route("/api/admin/status")
    def admin_status():
        payload = build_admin_status(_integration_context())
        payload["active_study_session"] = bool(current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG"))
        payload["study_controlled_sensor_keys"] = list(STUDY_SENSOR_KEYS)
        payload["sensor_runtime"] = _sensor_runtime_state()
        payload["session_sensor_overrides"] = _session_overrides()
        return jsonify(payload)

    @app.route("/api/admin/session-overrides/reset", methods=["POST"])
    def reset_session_overrides():
        _clear_session_overrides()
        active_config = _rebuild_active_study_runtime_config()
        context = _integration_context(active_config) if isinstance(active_config, dict) else _integration_context()
        for sensor_key in STUDY_SENSOR_KEYS:
            effective = _sensor_runtime_state()["effective"].get(sensor_key, False)
            try:
                if effective:
                    initialize_plugin(sensor_key, context)
                    run_runtime_action(sensor_key, "start", context)
                else:
                    run_runtime_action(sensor_key, "stop", context)
                    if sensor_key == "camera_emotion":
                        current_app.config["CAMERA_PREVIEW_ACTIVE"] = False
            except Exception:
                pass
        return jsonify({"ok": True, "sensor_runtime": _sensor_runtime_state()})

    @app.route("/api/admin/update/status")
    def admin_update_status():
        return jsonify(build_update_status(current_app.config))

    @app.route("/api/admin/update/check", methods=["POST"])
    def admin_update_check():
        try:
            return jsonify(check_for_update(current_app.config))
        except UpdateError as error:
            return jsonify({"ok": False, "error": str(error)}), 400

    @app.route("/api/admin/update/download", methods=["POST"])
    def admin_update_download():
        try:
            return jsonify(download_and_stage_update(current_app.config))
        except UpdateError as error:
            return jsonify({"ok": False, "error": str(error)}), 400

    @app.route("/api/admin/update/install", methods=["POST"])
    def admin_update_install():
        shutdown_func = request.environ.get("werkzeug.server.shutdown")
        if shutdown_func is None:
            return jsonify({"ok": False, "error": "Update restart is only available on the built-in Study Runner server."}), 503

        try:
            result = request_update_install(current_app.config)
        except UpdateError as error:
            return jsonify({"ok": False, "error": str(error)}), 503

        threading.Thread(target=_delayed_shutdown, args=(shutdown_func,), daemon=True).start()
        return jsonify({"ok": True, **result})

    @app.route("/api/hardware-config")
    def get_hardware_config():
        return jsonify(redact_hardware_config(current_app.config.get("HARDWARE_CONFIG", {}), current_app.config.get("LOCAL_SECRETS", {})))

    @app.route("/api/hardware-config", methods=["POST"])
    def update_hardware_config():
        config_data = request.get_json()
        if not isinstance(config_data, dict):
            return jsonify({"ok": False, "error": "hardware_config payload must be a JSON object."}), 400

        sanitized_config, _secret_updated = _save_notion_secret_payload(config_data)
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

    @app.route("/api/admin/integrations/<integration_key>/enabled", methods=["POST"])
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

    @app.route("/api/admin/integrations/<integration_key>/<action>", methods=["POST"])
    def run_integration_runtime_action(integration_key: str, action: str):
        return _run_integration_action_json(integration_key, action)

    def _run_integration_action_json(integration_key: str, action: str):
        try:
            normalized_action = str(action or "").strip().lower()
            if integration_key in STUDY_SENSOR_KEYS and normalized_action in {"start", "stop", "restart"}:
                _set_session_override(integration_key, normalized_action != "stop")
                _rebuild_active_study_runtime_config()
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

    @app.route("/api/admin/brainbit/start", methods=["POST"])
    def start_brainbit():
        return _run_integration_action_json("brainbit", "start")

    @app.route("/api/admin/brainbit/stop", methods=["POST"])
    def stop_brainbit():
        return _run_integration_action_json("brainbit", "stop")

    @app.route("/api/admin/brainbit/restart", methods=["POST"])
    def restart_brainbit():
        return _run_integration_action_json("brainbit", "restart")

    @app.route("/api/admin/brainbit/select-device", methods=["POST"])
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

    @app.route("/api/admin/radar/start", methods=["POST"])
    def start_mini_radar():
        return _run_integration_action_json("mini_radar", "start")

    @app.route("/api/admin/radar/stop", methods=["POST"])
    def stop_mini_radar():
        return _run_integration_action_json("mini_radar", "stop")

    @app.route("/api/admin/radar/restart", methods=["POST"])
    def restart_mini_radar():
        return _run_integration_action_json("mini_radar", "restart")

    @app.route("/api/camera/frame", methods=["POST"])
    def process_camera_frame():
        from study_runner.integrations.tablet_camera_emotion import adapter as camera_affect_adapter

        frame_result = camera_affect_adapter.process_frame(request.get_json() or {})
        return jsonify({"ok": bool(frame_result.get("accepted", False)), **frame_result})

    @app.route("/api/admin/camera/start", methods=["POST"])
    def start_camera_affect():
        return _run_integration_action_json("camera_emotion", "start")

    @app.route("/api/admin/camera/stop", methods=["POST"])
    def stop_camera_affect():
        return _run_integration_action_json("camera_emotion", "stop")

    @app.route("/api/admin/emotion-worker/repair-runtime", methods=["POST"])
    def repair_emotion_worker_runtime():
        try:
            from study_runner.integrations.local_emotion_worker import plugin as emotion_worker_plugin

            result = emotion_worker_plugin.repair_runtime(_integration_context())
            return jsonify({"ok": True, **result})
        except Exception as error:
            return jsonify({"ok": False, "error": str(error)}), 500

    @app.route("/api/admin/emotion-worker/install-dependencies", methods=["POST"])
    def install_emotion_worker_dependencies():
        try:
            from study_runner.integrations.local_emotion_worker import plugin as emotion_worker_plugin

            result = emotion_worker_plugin.install_dependencies(_integration_context())
            return jsonify({"ok": True, **result})
        except Exception as error:
            return jsonify({"ok": False, "error": str(error)}), 500

    @app.route("/api/admin/camera/live/status")
    def camera_live_status():
        from study_runner.integrations.tablet_camera_emotion import adapter as camera_affect_adapter

        return jsonify(
            {
                "ok": True,
                "active": bool(current_app.config.get("CAMERA_PREVIEW_ACTIVE", False)),
                **camera_affect_adapter.get_preview_status(),
            }
        )

    @app.route("/api/results", methods=["POST"])
    def save_results():
        result_payload = request.get_json() or {}
        config_data = validate_and_normalize_config(load_config(current_app.config["CONFIG_FILE"]))
        hardware_config = _runtime_hardware_config()
        validated_results = validate_and_normalize_results(result_payload, config_data)
        validated_results["answer_details"] = build_answer_details(
            validated_results,
            config_data,
            hardware_config,
        )
        saved_output = save_results_payload(
            current_app.config["DATA_DIR"],
            config_data["study_id"],
            validated_results,
            hardware_config,
            context=_integration_context(),
        )
        print(f"[DATA] Saved: {saved_output['json_file']}")
        if saved_output.get("xdf_file"):
            print(f"[DATA] XDF: {saved_output['xdf_file']}")

        study_settings = config_data.get("study_settings", {})
        if study_settings.get("notion_enabled"):
            from study_runner.integrations.notion_upload import adapter as notion_adapter

            biosignal_summary = build_biosignal_summary(hardware_config, saved_output, context=_integration_context())
            notion_result = notion_adapter.upload_study_result(
                result_payload=validated_results,
                hardware_config=hardware_config,
                saved_output={**saved_output, "biosignal_summary": biosignal_summary},
                config_data=config_data,
            )
            if notion_result.get("ok"):
                print("[NOTION] Uploaded")
            elif notion_result.get("queued"):
                print("[NOTION] Queued (offline)")
            elif notion_result.get("skipped"):
                print(f"[NOTION] Skipped: {notion_result.get('error', 'not configured')}")

        return jsonify({"ok": True, **saved_output})

    @app.route("/api/notion/status")
    def notion_status():
        from study_runner.integrations.notion_upload import adapter as notion_adapter

        hardware_config = current_app.config.get("HARDWARE_CONFIG", {})
        local_secrets = current_app.config.get("LOCAL_SECRETS", {})
        config_data = validate_and_normalize_config(load_config(current_app.config["CONFIG_FILE"]))
        study_settings = config_data.get("study_settings", {})

        status = notion_adapter.get_status()
        status.update(
            {
                "enabled_globally": bool(hardware_config.get("notion", {}).get("enabled")),
                "auto_retry_failed": bool(hardware_config.get("notion", {}).get("auto_retry_failed", True)),
                "api_key_configured": bool(resolve_notion_api_key(hardware_config, local_secrets)),
                "api_key_source": describe_notion_api_key_source(hardware_config, local_secrets),
                "api_key_storage": describe_notion_api_key_storage(hardware_config, local_secrets, current_app.config["LOCAL_SECRETS_FILE"]),
                "local_secrets_file": current_app.config["LOCAL_SECRETS_FILE"].name,
                "current_study_id": config_data.get("study_id", ""),
                "current_study_notion_enabled": bool(study_settings.get("notion_enabled")),
                "current_study_parent_page_id": study_settings.get("notion_parent_page_id", ""),
                "current_study_database_id": study_settings.get("notion_database_id", ""),
                "current_study_target_ready": bool(study_settings.get("notion_parent_page_id") or study_settings.get("notion_database_id")),
            }
        )
        return jsonify(status)

    @app.route("/api/notion/flush-queue", methods=["POST"])
    def notion_flush_queue():
        from study_runner.integrations.notion_upload import adapter as notion_adapter

        return jsonify(notion_adapter.flush_queue())

    @app.route("/api/notion/test", methods=["POST"])
    def notion_test():
        from study_runner.integrations.notion_upload import adapter as notion_adapter

        payload = request.get_json() or {}
        result = notion_adapter.test_connection(
            api_key=(
                str(payload.get("api_key") or "").strip()
                or resolve_notion_api_key(current_app.config.get("HARDWARE_CONFIG", {}), current_app.config.get("LOCAL_SECRETS", {}))
            ),
            timeout_seconds=int(payload.get("timeout_seconds") or 10),
        )
        return jsonify(result)

    @app.route("/api/sync-clock", methods=["POST"])
    def sync_clock():
        """Clock-sync endpoint for tablet trigger precision against the Study Runner server."""
        data = request.get_json(force=True) or {}
        server_receive_ms = time.time() * 1000
        return jsonify(
            {
                "client_send_ms": data.get("client_send_ms"),
                "server_receive_ms": server_receive_ms,
                "server_send_ms": time.time() * 1000,
            }
        )

    @app.errorhandler(ValidationError)
    def handle_validation_error(error: ValidationError):
        return jsonify({"ok": False, "error": str(error)}), 400
