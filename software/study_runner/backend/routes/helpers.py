"""Shared request-context helpers used by several route modules.

Everything here operates on ``current_app.config`` state: the active
study configuration, temporary session sensor overrides, the running
study sessions, and the integration runtime context.
"""
import json
import os
import subprocess
import sys
import time

from flask import current_app

from study_runner.integrations.registry import (
    apply_enabled_runtime,
    build_context,
    initialize_plugin,
    run_runtime_action,
)
from ..services.hardware_settings_service import set_integration_enabled
from ..services.secrets_service import load_local_secrets, save_local_secrets
from ..services.session_store import public_session
from ..services.study_config_service import load_config
from ..services.study_sensor_runtime import (
    SESSION_OVERRIDE_KEYS,
    STUDY_SENSOR_KEYS,
    build_effective_hardware_config,
    build_sensor_runtime_state,
    normalize_session_overrides,
)
from ..services.trial_service import configure_runtime
from ..services.validation import validate_and_normalize_config

ACTIVE_RUNTIME_TOGGLE_KEYS = {"lsl", "labrecorder"}


def _hardware_disabled() -> bool:
    configured = current_app.config.get("HARDWARE_DISABLED")
    if configured is not None:
        return bool(configured)
    return os.getenv("STUDY_RUNNER_DISABLE_HARDWARE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _hardware_disabled_result(sensor_key: str) -> dict:
    return {"ok": True, "integration": sensor_key, "skipped": True, "reason": "hardware_disabled"}


def _disable_runtime_hardware(config_data: dict) -> dict:
    disabled = _copy_config(config_data)
    for key in SESSION_OVERRIDE_KEYS:
        section = disabled.get(key)
        if not isinstance(section, dict):
            section = {}
            disabled[key] = section
        section["enabled"] = False
    return disabled


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


def _study_run_state_store():
    return current_app.config["STUDY_RUN_STATE"]


def _study_run_state(study_id: str | None = None) -> dict:
    config_study_id = study_id if study_id is not None else _current_config_data().get("study_id", "")
    return _study_run_state_store().ensure_loaded(str(config_study_id or ""))


def _load_study_run(study_id: str) -> dict:
    return _study_run_state_store().set_loaded(study_id)


def _start_study_run(study_id: str) -> dict:
    return _study_run_state_store().start(study_id)


def _complete_study_run(study_id: str, session_id: str) -> dict:
    return _study_run_state_store().complete(study_id, session_id)


def _stop_study_run(study_id: str = "") -> dict:
    return _study_run_state_store().stop(study_id)


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

    if _hardware_disabled():
        if isinstance(active_config, dict):
            current_app.config["ACTIVE_STUDY_HARDWARE_CONFIG"] = _disable_runtime_hardware(active_config)
            _refresh_trial_runtime()
        return _hardware_disabled_result(integration_key)

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


def _save_hardware_secret_payload(config_data: dict) -> tuple[dict, bool]:
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

    nextcloud_config = sanitized_config.get("nextcloud")
    if isinstance(nextcloud_config, dict):
        if "password" in nextcloud_config:
            provided_password = str(nextcloud_config.get("password") or "")
            if provided_password:
                local_secrets.setdefault("nextcloud", {})["password"] = provided_password
                secret_updated = True

        if nextcloud_config.get("clear_password"):
            local_secrets.setdefault("nextcloud", {}).pop("password", None)
            if not local_secrets.get("nextcloud"):
                local_secrets.pop("nextcloud", None)
            secret_updated = True

        nextcloud_config.pop("password", None)
        nextcloud_config.pop("password_configured", None)
        nextcloud_config.pop("clear_password", None)

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

    if _hardware_disabled():
        disabled_config = _disable_runtime_hardware(effective_hardware_config)
        current_app.config["ACTIVE_STUDY_HARDWARE_CONFIG"] = disabled_config
        _refresh_trial_runtime()
        return {
            "sensors": selected_sensors,
            "sensor_runtime": runtime_state,
            "active_plugins": [],
            "runtime": {key: _hardware_disabled_result(key) for key in STUDY_SENSOR_KEYS},
        }

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


def _session_store():
    return current_app.config["SESSION_STORE"]


def _find_active_study_session(study_id: str, participant_id: str, client_id: str = "") -> dict | None:
    return _session_store().find_active(study_id, participant_id, client_id)


def _start_or_reuse_study_session(payload: dict) -> dict:
    return _session_store().start_or_reuse(payload)


def _resume_study_session(payload: dict) -> dict | None:
    session = _session_store().resume(payload)
    if session is not None:
        _restart_sensor_runtime_if_needed(session)
    return session


def _restart_sensor_runtime_if_needed(session: dict) -> None:
    """Bring sensors back up for a session resumed after a server restart.

    ``ACTIVE_STUDY_HARDWARE_CONFIG`` lives only in ``current_app.config``, so
    it is gone after a crash/restart even though the session itself is now
    rehydrated. Without this, a resumed tablet would look fine while no
    sensor is actually recording again.
    """
    if current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG"):
        return
    try:
        study_settings = _current_study_settings()
        _start_study_sensor_runtime(study_settings)
    except Exception as error:
        print(f"[SESSIONS] Could not restart sensors for resumed session: {error}")


def _stop_study_session_tracking(session_id: str) -> bool:
    if not session_id:
        return False
    return _session_store().mark_completed(session_id)


def _record_study_client_event(payload: dict) -> dict:
    session = _session_store().record_client_event(payload)
    if session is None:
        return {"recorded": False, "reason": "session_not_found"}
    return {"recorded": True, "session": _public_study_session(session)}


def _public_study_session(session: dict | None) -> dict | None:
    return public_session(session)
