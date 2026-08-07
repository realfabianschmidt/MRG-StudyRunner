"""Operator/admin endpoints: health, studies, status, restart, shortcut."""
import sys
import threading

from flask import Blueprint, current_app, jsonify, request

from study_runner.plugin_framework.registry import initialize_plugin, run_runtime_action
from ..services.settings.admin_status_service import build_admin_status
from ..services.settings.runtime_config import build_runtime_info
from ..services.settings.shortcut_service import ShortcutError, create_desktop_shortcut
from ..services.studies.study_client_service import get_client_status
from ..services.settings.secrets_service import load_local_secrets, save_local_secrets
from ..services.studies.study_config_service import delete_study, list_studies, load_config, load_study, save_config
from ..services.studies.study_readiness_service import check_study_readiness, describe_credentials
from ..services.studies.study_secrets_service import (
    forget_study_secrets,
    list_study_credential_state,
    secret_fields,
    set_study_secret,
)
from ..services.recording.study_sensor_runtime import STUDY_SENSOR_KEYS
from ..services.studies.validation import validate_and_normalize_config
from .helpers import (
    _clear_session_overrides,
    _delayed_shutdown,
    _plugin_context,
    _load_study_run,
    _rebuild_active_study_runtime_config,
    _sensor_runtime_state,
    _session_overrides,
    _spawn_server_restart,
    _start_study_run,
    _stop_study_run,
    _study_run_state,
    _stop_study_sensor_runtime,
)

bp = Blueprint("admin", __name__)


@bp.route("/api/health")
def health():
    return jsonify(
        {
            "ok": True,
            "status": "running",
            "app_mode": current_app.config.get("APP_MODE", "python"),
        }
    )


@bp.route("/api/runtime-info")
def runtime_info():
    return jsonify(build_runtime_info(current_app.config))


@bp.route("/api/admin/restart", methods=["POST"])
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


@bp.route("/api/admin/studies", methods=["GET"])
def admin_list_studies():
    return jsonify(list_studies(current_app.config["SAVED_STUDIES_DIR"]))


@bp.route("/api/admin/studies/active", methods=["POST"])
def admin_set_active_study():
    payload = request.get_json() or {}
    study_id = payload.get("id")
    if not study_id:
        return jsonify({"ok": False, "error": "No study ID provided"}), 400
    try:
        config_data = load_study(current_app.config["SAVED_STUDIES_DIR"], study_id)
        validated_config = validate_and_normalize_config(config_data)
        save_config(current_app.config["CONFIG_FILE"], validated_config)
        _load_study_run(validated_config["study_id"])
        print(f"[CONFIG] Activated study: {study_id}")
        return jsonify(validated_config)
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 404


@bp.route("/api/admin/studies/<study_id>", methods=["GET"])
def admin_get_study(study_id):
    try:
        return jsonify(load_study(current_app.config["SAVED_STUDIES_DIR"], study_id))
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 404


@bp.route("/api/admin/studies/<study_id>", methods=["DELETE"])
def admin_delete_study(study_id):
    if delete_study(current_app.config["SAVED_STUDIES_DIR"], study_id):
        # Do not leave a deleted study's credentials on disk forever.
        _forget_study_credentials(study_id)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Not found"}), 404


def _forget_study_credentials(study_id: str) -> None:
    secrets = load_local_secrets(current_app.config["LOCAL_SECRETS_FILE"])
    if forget_study_secrets(secrets, study_id):
        save_local_secrets(current_app.config["LOCAL_SECRETS_FILE"], secrets)
        current_app.config["LOCAL_SECRETS"] = load_local_secrets(current_app.config["LOCAL_SECRETS_FILE"])


@bp.route("/api/admin/studies/<study_id>/credentials", methods=["GET"])
def admin_get_study_credentials(study_id):
    """Report whether each credential is configured and where it comes from.

    Deliberately never returns a value - the operator only needs to know if a
    key is in place and whether it is this study's own or the shared one.
    """
    return jsonify({
        "ok": True,
        "study_id": study_id,
        "credentials": list_study_credential_state(
            current_app.config.get("HARDWARE_CONFIG", {}),
            current_app.config.get("LOCAL_SECRETS", {}),
            study_id,
        ),
    })


@bp.route("/api/admin/studies/<study_id>/credentials", methods=["POST"])
def admin_set_study_credentials(study_id):
    """Store or clear this study's own credentials.

    A targeted write, following the brainbit device-selection precedent: only
    the fields named in the body are touched, never the whole secrets file.
    """
    payload = request.get_json(silent=True) or {}
    secrets = load_local_secrets(current_app.config["LOCAL_SECRETS_FILE"])
    changed = []

    for kind in secret_fields():
        clear_requested = payload.get(f"clear_{kind}") is True
        raw_value = payload.get(kind)
        if not clear_requested and raw_value is None:
            continue
        value = "" if clear_requested else str(raw_value or "")
        try:
            set_study_secret(secrets, study_id, kind, value)
        except ValueError as error:
            return jsonify({"ok": False, "error": str(error)}), 400
        changed.append(kind)

    if not changed:
        return jsonify({"ok": False, "error": "No credential was provided."}), 400

    save_local_secrets(current_app.config["LOCAL_SECRETS_FILE"], secrets)
    current_app.config["LOCAL_SECRETS"] = load_local_secrets(current_app.config["LOCAL_SECRETS_FILE"])
    # A changed credential must not keep serving a client cached under the old
    # one - whichever plugin it belongs to, not just the one this used to
    # hardcode.
    for kind in changed:
        initialize_plugin(kind, _plugin_context())

    return jsonify({
        "ok": True,
        "changed": changed,
        "credentials": list_study_credential_state(
            current_app.config.get("HARDWARE_CONFIG", {}),
            current_app.config.get("LOCAL_SECRETS", {}),
            study_id,
        ),
    })


@bp.route("/api/admin/study-readiness", methods=["GET"])
def admin_study_readiness():
    """What would stop the loaded study from delivering a complete result."""
    config_data = validate_and_normalize_config(load_config(current_app.config["CONFIG_FILE"]))
    hardware_config = current_app.config.get("HARDWARE_CONFIG", {})
    local_secrets = current_app.config.get("LOCAL_SECRETS", {})
    recording_runtime = current_app.config.get("RECORDING_RUNTIME_SERVICE")
    recording_preflight = (
        recording_runtime.preflight(config_data, hardware_config) if recording_runtime else None
    )
    report = check_study_readiness(
        config_data,
        hardware_config,
        local_secrets,
        recording_preflight=recording_preflight,
    )
    report["credentials"] = describe_credentials(config_data, hardware_config, local_secrets)
    return jsonify({"ok": True, **report})


@bp.route("/api/admin/status")
def admin_status():
    payload = build_admin_status(
        _plugin_context(),
        sensor_coordinator=current_app.config.get("SENSOR_COORDINATOR"),
        clock_sync_service=current_app.config.get("CLOCK_SYNC_SERVICE"),
    )
    payload["active_study_session"] = bool(current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG"))
    payload["study_controlled_sensor_keys"] = list(STUDY_SENSOR_KEYS)
    payload["sensor_runtime"] = _sensor_runtime_state()
    payload["session_sensor_overrides"] = _session_overrides()
    run_state = _study_run_state()
    payload["study_run_state"] = run_state
    payload["study_clients"] = get_client_status(
        active_study_id=str(run_state.get("study_id") or ""),
        assigned_client_id=str(run_state.get("active_client_id") or ""),
    )
    recording_runtime = current_app.config.get("RECORDING_RUNTIME_SERVICE")
    payload["recording_infrastructure"] = (
        recording_runtime.availability()
        if recording_runtime is not None
        else {
            "available": False,
            "canonical_xdf": False,
            "supports_merge": False,
            "reason": "Recording runtime is not configured.",
        }
    )
    payload["recording_worker"] = (
        recording_runtime.current_status() if recording_runtime is not None else None
    )
    return jsonify(payload)


@bp.route("/api/admin/study-run", methods=["GET"])
def admin_study_run_status():
    run_state = _study_run_state()
    client_status = get_client_status(
        active_study_id=str(run_state.get("study_id") or ""),
        assigned_client_id=str(run_state.get("active_client_id") or ""),
    )
    return jsonify({"ok": True, "run_state": run_state, "tablet_gate": client_status.get("single_tablet", {})})


@bp.route("/api/admin/study-run/load", methods=["POST"])
def admin_load_study_run():
    payload = request.get_json() or {}
    study_id = payload.get("id")
    if not study_id:
        return jsonify({"ok": False, "error": "No study ID provided"}), 400
    try:
        config_data = load_study(current_app.config["SAVED_STUDIES_DIR"], study_id)
        validated_config = validate_and_normalize_config(config_data)
        save_config(current_app.config["CONFIG_FILE"], validated_config)
        run_state = _load_study_run(validated_config["study_id"])
        client_status = get_client_status(active_study_id=str(run_state.get("study_id") or ""))
        print(f"[STUDY-RUN] Loaded study: {study_id}")
        return jsonify(
            {
                "ok": True,
                "config": validated_config,
                "run_state": run_state,
                "tablet_gate": client_status.get("single_tablet", {}),
            }
        )
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 404


@bp.route("/api/admin/study-run/start", methods=["POST"])
def admin_start_study_run():
    try:
        config_data = validate_and_normalize_config(load_config(current_app.config["CONFIG_FILE"]))
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    hardware_config = current_app.config.get("HARDWARE_CONFIG", {})
    local_secrets = current_app.config.get("LOCAL_SECRETS", {})
    recording_runtime = current_app.config.get("RECORDING_RUNTIME_SERVICE")
    readiness = check_study_readiness(
        config_data,
        hardware_config,
        local_secrets,
        recording_preflight=(
            recording_runtime.preflight(config_data, hardware_config) if recording_runtime else None
        ),
    )
    if readiness.get("start_blocked"):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Required plugins or recording infrastructure are not ready.",
                    "readiness": readiness,
                }
            ),
            409,
        )
    client_status = get_client_status(active_study_id=str(config_data["study_id"]))
    tablet_gate = client_status.get("single_tablet", {})
    if not tablet_gate.get("can_start"):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Exactly one participant tablet must be connected to this study before it can start.",
                    "tablet_gate": tablet_gate,
                }
            ),
            409,
        )
    run_state = _start_study_run(config_data["study_id"], str(tablet_gate.get("selected_client_id") or ""))
    print(f"[STUDY-RUN] Started study: {config_data['study_id']}")
    return jsonify({"ok": True, "run_state": run_state, "tablet_gate": tablet_gate})


@bp.route("/api/admin/study-run/stop", methods=["POST"])
def admin_stop_study_run():
    run_state = _stop_study_run()
    sensor_result = _stop_study_sensor_runtime()
    print(f"[STUDY-RUN] Stopped study: {run_state.get('study_id')}")
    return jsonify({"ok": True, "run_state": run_state, **sensor_result})


@bp.route("/api/admin/session-overrides/reset", methods=["POST"])
def reset_session_overrides():
    _clear_session_overrides()
    active_config = _rebuild_active_study_runtime_config()
    context = _plugin_context(active_config) if isinstance(active_config, dict) else _plugin_context()
    for sensor_key in STUDY_SENSOR_KEYS:
        effective = _sensor_runtime_state()["effective"].get(sensor_key, False)
        try:
            if effective:
                initialize_plugin(sensor_key, context)
                run_runtime_action(sensor_key, "start", context)
            else:
                run_runtime_action(sensor_key, "stop", context)
        except Exception:
            pass
    return jsonify({"ok": True, "sensor_runtime": _sensor_runtime_state()})


@bp.route("/api/admin/system/create-shortcut", methods=["POST"])
def admin_create_shortcut():
    try:
        return jsonify(create_desktop_shortcut(current_app.config))
    except ShortcutError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
