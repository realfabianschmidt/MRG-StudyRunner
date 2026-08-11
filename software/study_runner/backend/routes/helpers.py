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

from flask import current_app, request
from werkzeug.exceptions import BadRequest, Forbidden, UnsupportedMediaType

from study_runner.plugin_framework.registry import (
    apply_enabled_runtime,
    build_context,
    get_plugin_manifest,
    initialize_plugin,
    run_runtime_action,
)
from ..services.settings.hardware_settings_service import (
    HardwareRevisionConflict,
    set_plugin_enabled,
    update_hardware_config,
)
from ..services.settings.secrets_service import update_local_secrets
from ..services.studies.session_store import public_session
from ..services.studies.study_config_service import load_config
from ..services.studies.study_secrets_service import secret_fields
from ..services.recording.study_sensor_runtime import (
    SESSION_OVERRIDE_KEYS,
    STUDY_SENSOR_KEYS,
    build_effective_hardware_config,
    build_sensor_runtime_state,
    normalize_session_overrides,
)
from ..services.studies.trial_service import configure_runtime
from ..services.studies.validation import validate_and_normalize_config

# Internal marker/clock streams are mandatory recording providers, not
# operator-toggleable integrations.
ACTIVE_RUNTIME_TOGGLE_KEYS: set[str] = set()


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


def _request_json_object() -> dict:
    """Read an optional JSON object without treating malformed input as empty."""

    body = request.get_data(cache=True)
    if not body:
        return {}
    if not request.is_json:
        raise UnsupportedMediaType("plugin action payload must use application/json")
    payload = request.get_json(silent=False)
    if not isinstance(payload, dict):
        raise BadRequest("plugin action payload must be a JSON object")
    return payload


def _require_secure_participant_ingest(plugin_key: str) -> None:
    """Fail closed for plugin inputs whose manifest requires browser HTTPS.

    Study Runner terminates TLS directly and intentionally does not trust
    forwarded-proto headers. Consequently ``request.is_secure`` reflects the
    WSGI transport rather than client-controlled proxy headers. A future
    reverse-proxy deployment must configure a trusted ``ProxyFix`` boundary
    centrally before those headers can influence this decision.
    """

    transport = (
        (get_plugin_manifest(plugin_key).get("capability_config") or {})
        .get("acquisition_transport", {})
    )
    if transport.get("transport") == "browser_https" and not request.is_secure:
        raise Forbidden(
            "This participant plugin accepts browser data only over a trusted HTTPS connection."
        )


def _hardware_disabled_result(sensor_key: str) -> dict:
    return {"ok": True, "plugin": sensor_key, "skipped": True, "reason": "hardware_disabled"}


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


def _plugin_context(
    hardware_config: dict | None = None,
    *,
    machine_admin: bool = False,
):
    # ``persist_hardware_config`` may be called from a plugin's stdout-reader
    # thread after the originating request/app context has ended. Resolve the
    # LocalProxy now so the callback never depends on thread-local Flask state.
    app = current_app._get_current_object()
    selected_config = hardware_config
    persist_hardware_config = None
    runtime_locked = False
    if machine_admin:
        selected_config = json.loads(
            json.dumps(app.config.get("HARDWARE_CONFIG", {}))
        )
        baseline_config = json.loads(json.dumps(selected_config))
        runtime_locked = isinstance(
            app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG"),
            dict,
        )

        def persist_hardware_config(updated_config: dict) -> None:
            safe_config = json.loads(json.dumps(updated_config))
            config_path = app.config["HARDWARE_CONFIG_FILE"]
            # Apply only leaves changed by this plugin to the latest on-disk
            # document. Replacing the child's complete, potentially stale
            # context would silently erase a concurrent admin save.
            def apply_plugin_delta(current_config: dict) -> None:
                _apply_config_delta_checked(
                    current_config,
                    baseline_config,
                    safe_config,
                )

            with app.app_context():
                persisted, _, _ = update_hardware_config(
                    config_path,
                    apply_plugin_delta,
                )
                app.config["HARDWARE_CONFIG"] = persisted
                _refresh_trial_runtime()

    return build_context(
        base_dir=app.config["BASE_DIR"],
        data_dir=app.config["DATA_DIR"],
        hardware_config=selected_config if selected_config is not None else _runtime_hardware_config(),
        local_secrets=app.config.get("LOCAL_SECRETS", {}),
        local_secrets_file=app.config["LOCAL_SECRETS_FILE"],
        runtime_locked=runtime_locked,
        persist_hardware_config=persist_hardware_config,
    )


_MISSING_CONFIG_VALUE = object()


def _apply_config_delta_checked(target: dict, before: dict, after: dict, *, path: str = "") -> None:
    """Merge a plugin's changes and reject same-leaf concurrent edits."""

    for key in set(before) | set(after):
        old_value = before.get(key, _MISSING_CONFIG_VALUE)
        new_value = after.get(key, _MISSING_CONFIG_VALUE)
        if old_value == new_value:
            continue
        label = f"{path}.{key}" if path else str(key)
        current_value = target.get(key, _MISSING_CONFIG_VALUE)
        if isinstance(old_value, dict) and isinstance(new_value, dict):
            if current_value is _MISSING_CONFIG_VALUE:
                # The on-disk document simply never had this section yet (for
                # example the very first save of a plugin's settings). That is
                # not a concurrent edit, so create it and let the per-leaf
                # check below catch genuine conflicting values inside it.
                current_value = target.setdefault(key, {})
            if not isinstance(current_value, dict):
                raise HardwareRevisionConflict(
                    f"Hardware setting {label!r} changed concurrently; reload and retry."
                )
            _apply_config_delta_checked(
                current_value,
                old_value,
                new_value,
                path=label,
            )
            continue
        # A leaf the baseline snapshot never had an opinion on (``old_value``
        # missing) has nothing to conflict with: the plugin is introducing or
        # claiming that field, not overwriting a value someone else changed.
        if (
            old_value is not _MISSING_CONFIG_VALUE
            and current_value != old_value
            and current_value != new_value
        ):
            raise HardwareRevisionConflict(
                f"Hardware setting {label!r} changed concurrently; reload and retry."
            )
        if new_value is _MISSING_CONFIG_VALUE:
            target.pop(key, None)
        else:
            target[key] = json.loads(json.dumps(new_value))


def _refresh_trial_runtime() -> None:
    hardware_config = _runtime_hardware_config()
    if _hardware_disabled():
        # The process-host architecture can otherwise launch a driver lazily
        # on the first trial callback even though startup hardware was disabled.
        hardware_config = _disable_runtime_hardware(hardware_config)
    configure_runtime(
        base_dir=current_app.config["BASE_DIR"],
        data_dir=current_app.config["DATA_DIR"],
        hardware_config=hardware_config,
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


def _start_study_run(study_id: str, active_client_id: str = "") -> dict:
    return _study_run_state_store().start(study_id, active_client_id)


def _complete_study_run(study_id: str, session_id: str) -> dict:
    return _study_run_state_store().complete(study_id, session_id)


def _stop_study_run(study_id: str = "") -> dict:
    return _study_run_state_store().stop(study_id)


def _participant_study_run_state(client_id: str | None = None, study_id: str | None = None) -> dict:
    run_state = _study_run_state(study_id)
    public_state = dict(run_state)
    normalized_client_id = str(client_id or "").strip()
    active_client_id = str(run_state.get("active_client_id") or "").strip()
    if (
        run_state.get("status") == "running"
        and active_client_id
        and normalized_client_id
        and normalized_client_id != active_client_id
    ):
        public_state["status"] = "blocked"
        public_state["participant_allowed"] = False
        public_state["conflict"] = True
        public_state["message"] = "Another tablet is already assigned to this study run."
        return public_state
    public_state["participant_allowed"] = run_state.get("status") == "running"
    public_state["conflict"] = False
    return public_state


def _current_study_settings() -> dict:
    return _current_config_data().get("study_settings", {})


def _session_overrides() -> dict[str, bool]:
    return normalize_session_overrides(current_app.config.get("SESSION_SENSOR_OVERRIDES", {}))


def _set_session_override(plugin_key: str, enabled: bool) -> dict[str, bool]:
    if plugin_key not in SESSION_OVERRIDE_KEYS:
        raise ValueError(f"Integration '{plugin_key}' cannot be used as a session override.")
    overrides = _session_overrides()
    overrides[plugin_key] = bool(enabled)
    current_app.config["SESSION_SENSOR_OVERRIDES"] = overrides
    return overrides


def _sensor_coordinator():
    return current_app.config.get("SENSOR_COORDINATOR")


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


def _set_runtime_enabled(config_data: dict, plugin_key: str, enabled: bool) -> None:
    set_plugin_enabled(config_data, plugin_key, enabled)


def _apply_plugin_toggle_to_active_runtime(plugin_key: str, enabled: bool) -> bool:
    active_config = current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG")
    if not isinstance(active_config, dict) or plugin_key not in ACTIVE_RUNTIME_TOGGLE_KEYS:
        return False

    active_copy = _copy_config(active_config)
    _set_runtime_enabled(active_copy, plugin_key, enabled)
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


def _apply_session_override_runtime(plugin_key: str, enabled: bool) -> dict:
    active_config = _rebuild_active_study_runtime_config()
    context = _plugin_context(active_config) if isinstance(active_config, dict) else _plugin_context()
    result: dict = {}

    if _hardware_disabled():
        if isinstance(active_config, dict):
            current_app.config["ACTIVE_STUDY_HARDWARE_CONFIG"] = _disable_runtime_hardware(active_config)
            _refresh_trial_runtime()
        return _hardware_disabled_result(plugin_key)

    if plugin_key in STUDY_SENSOR_KEYS:
        try:
            if enabled:
                initialize_plugin(plugin_key, context)
                result = run_runtime_action(plugin_key, "start", context)
                active_plugins = list(current_app.config.get("ACTIVE_STUDY_SENSOR_PLUGINS") or [])
                if isinstance(active_config, dict) and plugin_key not in active_plugins:
                    active_plugins.append(plugin_key)
                    current_app.config["ACTIVE_STUDY_SENSOR_PLUGINS"] = active_plugins
            else:
                result = run_runtime_action(plugin_key, "stop", context)
                active_plugins = [
                    key for key in list(current_app.config.get("ACTIVE_STUDY_SENSOR_PLUGINS") or [])
                    if key != plugin_key
                ]
                current_app.config["ACTIVE_STUDY_SENSOR_PLUGINS"] = active_plugins
        except Exception as error:
            result = {"ok": False, "error": str(error)}
        return result

    try:
        apply_enabled_runtime(plugin_key, enabled, context)
        result = {"ok": True, "plugin": plugin_key, "enabled": enabled}
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
    secret_updates: list[tuple[str, str, str]] = []
    fields = secret_fields()
    for plugin_key, field in fields.items():
        section = sanitized_config.get(plugin_key)
        if not isinstance(section, dict):
            continue
        clear_field = f"clear_{field}"
        if field in section:
            provided = str(section.get(field) or "").strip()
            if provided:
                secret_updates.append(("set", plugin_key, provided))
        if section.get(clear_field) is True:
            secret_updates.append(("clear", plugin_key, ""))
        for private_field in (
            field,
            clear_field,
            f"{field}_configured",
            f"{field}_source",
            f"{field}_scope",
        ):
            section.pop(private_field, None)

    secret_updated = bool(secret_updates)
    if secret_updated:

        def apply_secret_updates(local_secrets):
            for action, kind, value in secret_updates:
                field = fields[kind]
                if action == "set":
                    local_secrets.setdefault(kind, {})[field] = value
                    continue
                section = local_secrets.get(kind)
                if isinstance(section, dict):
                    section.pop(field, None)
                    if not section:
                        local_secrets.pop(kind, None)

        local_secrets, _ = update_local_secrets(
            current_app.config["LOCAL_SECRETS_FILE"],
            apply_secret_updates,
        )
        current_app.config["LOCAL_SECRETS"] = local_secrets

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
            "coordinator": {},
        }

    _refresh_trial_runtime()

    context = _plugin_context(effective_hardware_config)
    coordinator = _sensor_coordinator()
    if coordinator:
        coordinator_result = coordinator.start_selected(selected_sensors, STUDY_SENSOR_KEYS, context)
        current_app.config["ACTIVE_STUDY_SENSOR_PLUGINS"] = list(coordinator_result.get("active_plugins") or [])
        results = coordinator_result.get("runtime") or {}
        coordinator_payload = coordinator_result.get("coordinator") or {}
    else:
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
        coordinator_payload = {}

    return {
        "sensors": selected_sensors,
        "sensor_runtime": runtime_state,
        "active_plugins": list(current_app.config.get("ACTIVE_STUDY_SENSOR_PLUGINS", [])),
        "runtime": results,
        "coordinator": coordinator_payload,
    }


def _stop_study_sensor_runtime() -> dict:
    active_hardware_config = current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG")
    active_plugins = list(current_app.config.get("ACTIVE_STUDY_SENSOR_PLUGINS") or [])
    context = _plugin_context(active_hardware_config) if active_hardware_config else _plugin_context()
    coordinator = _sensor_coordinator()
    if coordinator:
        coordinator_result = coordinator.stop_plugins(active_plugins, context)
        results = coordinator_result.get("runtime") or {}
        coordinator_payload = coordinator_result.get("coordinator") or {}
    else:
        results: dict[str, dict] = {}
        for sensor_key in active_plugins:
            try:
                results[sensor_key] = run_runtime_action(sensor_key, "stop", context)
            except Exception as error:
                results[sensor_key] = {"ok": False, "error": str(error)}
        coordinator_payload = {}

    current_app.config.pop("ACTIVE_STUDY_HARDWARE_CONFIG", None)
    current_app.config["ACTIVE_STUDY_SENSOR_PLUGINS"] = []
    _refresh_trial_runtime()
    return {"stopped_plugins": active_plugins, "runtime": results, "coordinator": coordinator_payload}


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
