"""Endpoints the participant tablet talks to during a study.

Study configuration, study sessions (start/stop/resume, lifecycle
events), sensor trial triggers (start/stop/marker), heartbeat, and the
clock-sync endpoint used to align tablet timestamps with the server.
"""
import ipaddress
import time

from flask import Blueprint, current_app, jsonify, request

from ..services.settings.secrets_service import update_local_secrets
from ..services.studies.study_client_service import register_heartbeat
from ..services.studies.study_secrets_service import copy_study_secrets
from ..services.studies.study_config_service import (
    StudyRevisionConflict,
    load_config,
    save_active_study,
    study_config_revision,
)
from ..services.studies.study_readiness_service import check_study_readiness
from ..services.recording.recording_runtime import required_recording_plugins
from ..services.studies.trial_service import send_trial_marker, start_trial_session, stop_trial_session
from ..services.studies.trial_service import TrialDispatchError
from ..services.studies.trial_event_service import (
    TrialEventConflictError,
    TrialEventInProgressError,
    TrialPreparationRequiredError,
)
from ..services.studies.validation import validate_and_normalize_config, validate_and_normalize_trial_options
from .helpers import (
    _current_config_data,
    _public_study_session,
    _record_study_client_event,
    _refresh_trial_runtime,
    _resume_study_session,
    _sensor_runtime_state,
    _session_overrides,
    _load_study_run,
    _participant_study_run_state,
    _start_or_reuse_study_session,
    _start_study_sensor_runtime,
    _study_run_state,
    _stop_study_session_tracking,
    _stop_study_sensor_runtime,
    _valid_participant_id,
)

bp = Blueprint("study", __name__)


class TrialRuntimeGateError(RuntimeError):
    def __init__(self, message: str, *, status_code: int, details: dict | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


@bp.route("/api/config")
def get_config():
    config_data = validate_and_normalize_config(load_config(current_app.config["CONFIG_FILE"]))
    config_data["_revision"] = study_config_revision(config_data)
    client_id = request.args.get("client_id", "")
    config_data["_capabilities"] = {
        "unsafe_stimulus_code": bool(current_app.config.get("ALLOW_UNSAFE_STIMULUS_CODE", False))
    }
    config_data["_runtime"] = {
        "sensor_runtime": _sensor_runtime_state(config_data.get("study_settings", {})),
        "session_overrides": _session_overrides(),
        "study_run_state": _participant_study_run_state(client_id, config_data["study_id"])
        if client_id
        else _study_run_state(config_data["study_id"]),
    }
    return jsonify(config_data)


@bp.route("/api/config", methods=["POST"])
def update_config():
    config_data = request.get_json() or {}
    if not isinstance(config_data, dict):
        return jsonify({"ok": False, "error": "Study config must be a JSON object."}), 400
    config_data = dict(config_data)
    expected_revision = str(config_data.pop("_revision", "") or "").strip() or None
    config_data.pop("_runtime", None)
    config_data.pop("_capabilities", None)
    previous_study_id = _current_study_id()
    validated_config = validate_and_normalize_config(config_data)
    try:
        revision = save_active_study(
            current_app.config["CONFIG_FILE"],
            current_app.config["SAVED_STUDIES_DIR"],
            validated_config,
            expected_revision=expected_revision,
        )
    except StudyRevisionConflict as error:
        current = validate_and_normalize_config(load_config(current_app.config["CONFIG_FILE"]))
        return (
            jsonify(
                {
                    "ok": False,
                    "code": "study_revision_conflict",
                    "error": str(error),
                    "current_revision": study_config_revision(current),
                }
            ),
            409,
        )
    _carry_credentials_on_rename(previous_study_id, str(validated_config.get("study_id") or ""))
    current_run_state = _study_run_state(validated_config["study_id"])
    if current_run_state.get("status") != "running":
        _load_study_run(validated_config["study_id"])
    print("[CONFIG] Saved.")
    return jsonify({"ok": True, "config": {**validated_config, "_revision": revision}})


def _current_study_id() -> str:
    try:
        return str(_current_config_data().get("study_id") or "")
    except Exception:
        return ""


def _carry_credentials_on_rename(previous_study_id: str, new_study_id: str) -> None:
    """Keep a renamed study's credentials working.

    Copied, not moved: save_study never deletes the old file, so the previous
    study still exists on disk and would otherwise lose its key.
    """
    if not previous_study_id or not new_study_id or previous_study_id == new_study_id:
        return
    try:
        secrets_file = current_app.config["LOCAL_SECRETS_FILE"]
        secrets, changed = update_local_secrets(
            secrets_file,
            lambda stored: copy_study_secrets(stored, previous_study_id, new_study_id),
        )
        if changed:
            current_app.config["LOCAL_SECRETS"] = secrets
            print(f"[SECRETS] Credentials carried from '{previous_study_id}' to '{new_study_id}'.")
    except Exception as error:
        # Never turn a successful study save into an error over bookkeeping.
        print(f"[SECRETS] Could not carry credentials on rename: {error}")


@bp.route("/api/study/session/start", methods=["POST"])
def start_study_session():
    payload = request.get_json() or {}
    if not _valid_participant_id(payload.get("participant_id")):
        return jsonify({"ok": False, "error": "Participant ID is required before a study can start."}), 400
    config_data = _current_config_data()
    recording_runtime = current_app.config.get("RECORDING_RUNTIME_SERVICE")
    readiness = check_study_readiness(
        config_data,
        current_app.config.get("HARDWARE_CONFIG", {}),
        current_app.config.get("LOCAL_SECRETS", {}),
        recording_preflight=(
            recording_runtime.preflight(config_data, current_app.config.get("HARDWARE_CONFIG", {}))
            if recording_runtime
            else None
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
    run_state = _study_run_state(config_data["study_id"])
    if payload.get("require_admin_start") and run_state.get("status") != "running":
        return jsonify({"ok": False, "error": "The study has not been started by the admin yet."}), 409
    client_id = str(payload.get("client_id") or "").strip()
    active_client_id = str(run_state.get("active_client_id") or "").strip()
    if payload.get("require_admin_start") and active_client_id and client_id != active_client_id:
        return jsonify({"ok": False, "error": "Another tablet is already assigned to this study run."}), 409
    payload_run_id = str(payload.get("study_run_id") or "").strip()
    if payload.get("require_admin_start") and payload_run_id and payload_run_id != str(run_state.get("run_id") or ""):
        return jsonify({"ok": False, "error": "The tablet is using an older study run. Please wait for the latest start signal."}), 409
    session = _start_or_reuse_study_session(payload)
    result = _start_study_sensor_runtime(config_data.get("study_settings", {}))
    required_failures = _required_sensor_runtime_failures(config_data, result.get("runtime") or {})
    if required_failures:
        _stop_study_sensor_runtime()
        _stop_study_session_tracking(str(session.get("session_id") or ""))
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "A required sensor plugin could not start.",
                    "plugin_failures": required_failures,
                }
            ),
            503,
        )

    recording_result: dict = {"recording_expected": False, "status": "skipped", "plugins": []}
    if recording_runtime is not None:
        try:
            recording_result = recording_runtime.start_session(
                session,
                config_data,
                current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG")
                or current_app.config.get("HARDWARE_CONFIG", {}),
                result.get("runtime") or {},
            )
        except Exception as error:
            required_recording = list(required_recording_plugins(config_data))
            if required_recording:
                _stop_study_sensor_runtime()
                _stop_study_session_tracking(str(session.get("session_id") or ""))
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": f"Canonical XDF recording could not start: {error}",
                            "recording": {"status": "attention_required", "error": str(error)},
                        }
                    ),
                    503,
                )
            recording_result = {
                "recording_expected": True,
                "status": "attention_required",
                "error": str(error),
            }
    return jsonify(
        {
            "ok": True,
            "session": _public_study_session(session),
            "study_run_state": _participant_study_run_state(client_id, config_data["study_id"]),
            "recording": recording_result,
            **result,
        }
    )


def _required_sensor_runtime_failures(config_data: dict, runtime: dict) -> list[dict]:
    from study_runner.plugin_framework.registry import get_plugin_manifests

    manifests = get_plugin_manifests()
    settings = config_data.get("study_settings") or {}
    selections = settings.get("plugins") if isinstance(settings, dict) else {}
    failures: list[dict] = []
    if not isinstance(selections, dict):
        return failures
    for plugin_key, selection in selections.items():
        if not isinstance(selection, dict) or not selection.get("enabled") or not selection.get("required", True):
            continue
        if "study_sensor" not in set((manifests.get(plugin_key) or {}).get("capabilities") or []):
            continue
        outcome = runtime.get(plugin_key)
        if isinstance(outcome, dict) and outcome.get("ok", True):
            continue
        failures.append(
            {
                "plugin": plugin_key,
                "error": str((outcome or {}).get("error") or "plugin did not report a successful start"),
            }
        )
    return failures


@bp.route("/api/study/session/stop", methods=["POST"])
def stop_study_session():
    payload = request.get_json() or {}
    session_id = str(payload.get("session_id") or "").strip()
    _stop_study_session_tracking(session_id)
    result = _stop_study_sensor_runtime()
    return jsonify({"ok": True, **result})


@bp.route("/api/study/session/resume", methods=["POST"])
def resume_study_session():
    payload = request.get_json() or {}
    if not _valid_participant_id(payload.get("participant_id")):
        return jsonify({"ok": False, "error": "Participant ID is required before a study can resume."}), 400
    session = _resume_study_session(payload)
    if session is None:
        return jsonify({"ok": False, "error": "No active study session was found for this tablet."}), 404
    config_data = _current_config_data()
    sensor_result = _start_study_sensor_runtime(config_data.get("study_settings", {}))
    required_failures = _required_sensor_runtime_failures(
        config_data,
        sensor_result.get("runtime") or {},
    )
    if required_failures:
        _stop_study_sensor_runtime()
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "A required sensor plugin could not resume.",
                    "plugin_failures": required_failures,
                }
            ),
            503,
        )

    recording_result: dict = {"recording_expected": False, "status": "skipped"}
    recording_runtime = current_app.config.get("RECORDING_RUNTIME_SERVICE")
    if recording_runtime is not None:
        try:
            recording_result = recording_runtime.start_session(
                {**session, "reused": True},
                config_data,
                current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG")
                or current_app.config.get("HARDWARE_CONFIG", {}),
                sensor_result.get("runtime") or {},
            )
        except Exception as error:
            # A transient sensor/worker outage must not trap the participant.
            # The durable recording/finalization state remains fail-closed and
            # will surface attention_required to the admin.
            recording_result = {
                "recording_expected": bool(required_recording_plugins(config_data)),
                "status": "attention_required",
                "error": str(error),
            }
    return jsonify(
        {
            "ok": True,
            "session": _public_study_session(session),
            **sensor_result,
            "recording": recording_result,
        }
    )


@bp.route("/api/study/session/client-event", methods=["POST"])
def study_session_client_event():
    payload = request.get_json() or {}
    return jsonify({"ok": True, **_record_study_client_event(payload)})


@bp.route("/api/study/runtime")
def study_runtime():
    return jsonify(
        {
            "ok": True,
            "sensor_runtime": _sensor_runtime_state(),
            "session_overrides": _session_overrides(),
            "study_run_state": _participant_study_run_state(request.args.get("client_id", "")),
            "active_study_session": bool(current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG")),
        }
    )


@bp.route("/api/start", methods=["POST"])
def start_trial():
    ingress_epoch_ms = time.time() * 1000.0
    _refresh_trial_runtime()
    options = validate_and_normalize_trial_options(request.get_json())
    _apply_trial_ingress_time(options, ingress_epoch_ms)
    service = current_app.config["TRIAL_EVENT_SERVICE"]
    try:
        prepare_gate = service.authorize_start(options)
        runtime_gate = None
        if prepare_gate.get("source") != "completed_event":
            runtime_gate = _require_trial_start_runtime(options)
        deadline = None
        if prepare_gate.get("source") != "completed_event":
            deadline = service.arm_deadline(
                options.get("stimulus_id") or "",
                options.get("planned_deadline_epoch_ms"),
                _deadline_stop_payload(options),
                stop_trial_session,
            )
            if not isinstance(deadline, dict) or deadline.get("status") != "armed":
                raise TrialPreparationRequiredError(
                    "The server stop deadline is no longer active for this stimulus."
                )
        result = service.execute(
            options.get("event_id"),
            "trial_start",
            options,
            start_trial_session,
        )
    except (TrialEventConflictError, TrialEventInProgressError) as error:
        return jsonify({"ok": False, "error": str(error)}), 409
    except TrialPreparationRequiredError as error:
        return (
            jsonify(
                {
                    "ok": False,
                    "code": "trial_prepare_required",
                    "error": str(error),
                    "event_id": options.get("event_id"),
                    "stimulus_id": options.get("stimulus_id"),
                }
            ),
            428,
        )
    except TrialRuntimeGateError as error:
        return (
            jsonify(
                {
                    "ok": False,
                    "code": "trial_runtime_not_ready",
                    "error": str(error),
                    "event_id": options.get("event_id"),
                    "stimulus_id": options.get("stimulus_id"),
                    "runtime_gate": error.details,
                }
            ),
            error.status_code,
        )
    except ValueError as error:
        return jsonify({"ok": False, "code": "invalid_trial_timing", "error": str(error)}), 400
    except TrialDispatchError as error:
        return _trial_dispatch_error_response(error, options)
    return jsonify(
        {
            "ok": True,
            **result,
            "prepare_gate": prepare_gate,
            "runtime_gate": runtime_gate,
            "deadline": deadline,
        }
    )


@bp.route("/api/trial/prepare", methods=["POST"])
def prepare_trial():
    """Durably register planned timing before the visual onset is shown."""
    ingress_epoch_ms = time.time() * 1000.0
    _refresh_trial_runtime()
    options = validate_and_normalize_trial_options(request.get_json())
    _apply_trial_ingress_time(options, ingress_epoch_ms)
    service = current_app.config["TRIAL_EVENT_SERVICE"]
    try:
        override = service.get_prepare_override(options.get("event_id"))
        if override is not None and override.get("stimulus_id") != options.get("stimulus_id"):
            raise TrialEventConflictError(
                f"Trial prepare override {options.get('event_id')!r} belongs to a different stimulus."
            )
        prepared = service.prepare(options)
        deadline = service.arm_deadline(
            options.get("stimulus_id") or "",
            options.get("planned_deadline_epoch_ms"),
            _deadline_stop_payload(options),
            stop_trial_session,
        )
        if not isinstance(deadline, dict) or deadline.get("status") != "armed":
            raise TrialPreparationRequiredError(
                "The server stop deadline could not be armed for this stimulus."
            )
    except TrialEventConflictError as error:
        return jsonify({"ok": False, "error": str(error)}), 409
    except (ValueError, TrialPreparationRequiredError) as error:
        return jsonify({"ok": False, "code": "trial_prepare_failed", "error": str(error)}), 400
    return jsonify(
        {
            "ok": True,
            "prepared": prepared,
            "deadline": deadline,
            "overridden": override is not None,
            "override": override,
            "server_epoch_ms": ingress_epoch_ms,
        }
    )


@bp.route("/api/trial/prepare/cancel", methods=["POST"])
def cancel_trial_prepare():
    """Persist a tablet skip/abort and disarm without emitting trial events."""

    payload = request.get_json(silent=True) or {}
    service = current_app.config["TRIAL_EVENT_SERVICE"]
    try:
        result = service.cancel_preparation(
            payload.get("event_id"),
            payload.get("stimulus_id"),
            payload.get("reason"),
        )
    except TrialEventConflictError as error:
        return jsonify({"ok": False, "error": str(error)}), 409
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, **result})


@bp.route("/api/stop", methods=["POST"])
def stop_trial():
    ingress_epoch_ms = time.time() * 1000.0
    _refresh_trial_runtime()
    options = validate_and_normalize_trial_options(request.get_json())
    _apply_trial_ingress_time(options, ingress_epoch_ms)
    service = current_app.config["TRIAL_EVENT_SERVICE"]
    try:
        result = service.execute(
            options.get("event_id"),
            "trial_stop",
            options,
            stop_trial_session,
        )
        # Keep the backup stop armed until the stop side effect and its journal
        # record both succeeded. A failing browser stop must not disarm safety.
        deadline_cancelled = service.cancel_deadline(options.get("stimulus_id") or "")
    except (TrialEventConflictError, TrialEventInProgressError) as error:
        return jsonify({"ok": False, "error": str(error)}), 409
    except TrialDispatchError as error:
        return _trial_dispatch_error_response(error, options)
    return jsonify({"ok": True, **result, "deadline_cancelled": deadline_cancelled})


@bp.route("/api/marker", methods=["POST"])
def trial_marker():
    ingress_epoch_ms = time.time() * 1000.0
    _refresh_trial_runtime()
    payload = request.get_json() or {}
    options = validate_and_normalize_trial_options(payload)
    _apply_trial_ingress_time(options, ingress_epoch_ms)
    event = options.get("marker_event") or payload.get("event") or payload.get("phase") or "marker"
    service = current_app.config["TRIAL_EVENT_SERVICE"]
    try:
        result = service.execute(
            options.get("event_id"),
            "trial_marker",
            options,
            lambda event_options: send_trial_marker(str(event), event_options),
        )
    except (TrialEventConflictError, TrialEventInProgressError) as error:
        return jsonify({"ok": False, "error": str(error)}), 409
    except TrialDispatchError as error:
        return _trial_dispatch_error_response(error, options)
    return jsonify({"ok": True, **result})


@bp.route("/api/admin/trials/prepare-overrides", methods=["POST"])
def create_trial_prepare_override():
    """Create a durable, loopback-only exception to the trial prepare gate."""

    if not _request_is_loopback():
        return jsonify({"ok": False, "error": "Trial prepare overrides are local-admin only."}), 403
    payload = request.get_json(silent=True) or {}
    service = current_app.config["TRIAL_EVENT_SERVICE"]
    try:
        override = service.create_prepare_override(
            payload.get("event_id"),
            payload.get("stimulus_id"),
            payload.get("reason"),
        )
    except TrialEventConflictError as error:
        return jsonify({"ok": False, "error": str(error)}), 409
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify(
        {
            "ok": True,
            "override": override,
            "scope": "trial_prepare_only",
            "bypasses_recording_readiness": False,
        }
    )


@bp.route("/api/admin/trials/prepare-overrides/<event_id>", methods=["GET"])
def get_trial_prepare_override(event_id: str):
    if not _request_is_loopback():
        return jsonify({"ok": False, "error": "Trial prepare overrides are local-admin only."}), 403
    try:
        override = current_app.config["TRIAL_EVENT_SERVICE"].get_prepare_override(event_id)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    if override is None:
        return jsonify({"ok": False, "error": "No trial prepare override exists for this event."}), 404
    return jsonify(
        {
            "ok": True,
            "override": override,
            "scope": "trial_prepare_only",
            "bypasses_recording_readiness": False,
        }
    )


def _apply_trial_ingress_time(options: dict, ingress_epoch_ms: float) -> None:
    options["server_received_epoch_ms"] = round(float(ingress_epoch_ms), 3)
    if options.get("visual_onset_epoch_ms") is not None:
        options["source_epoch_ms"] = options["visual_onset_epoch_ms"]
    elif options.get("client_trigger_epoch_ms") is not None:
        options["source_epoch_ms"] = options["client_trigger_epoch_ms"]


def _deadline_stop_payload(options: dict) -> dict:
    return {
        **options,
        "event_id": options.get("stop_event_id") or "",
        "marker_event": "stimulus_active_stop",
        "phase": "stimulus_active_stop",
    }


def _trial_dispatch_error_response(error: TrialDispatchError, options: dict):
    return (
        jsonify(
            {
                "ok": False,
                "code": "trial_dispatch_failed",
                "error": str(error),
                "event_id": options.get("event_id"),
                "stimulus_id": options.get("stimulus_id"),
                "dispatch": error.outcomes,
                "event": error.response,
            }
        ),
        503,
    )


def _request_is_loopback() -> bool:
    try:
        address = ipaddress.ip_address(str(request.remote_addr or "").strip())
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def _require_trial_start_runtime(options: dict) -> dict:
    """Bind a new start side effect to its active session and recording plan."""

    study_id = str(options.get("study_id") or "").strip()
    participant_id = str(options.get("participant_id") or "").strip()
    session_id = str(options.get("session_id") or "").strip()
    client_id = str(options.get("client_id") or "").strip()
    if not study_id or not participant_id or not session_id or not client_id:
        raise TrialRuntimeGateError(
            "Trial start requires study_id, participant_id, session_id, and client_id from an active session.",
            status_code=409,
            details={"active_session": False},
        )

    config_data = _current_config_data()
    if str(config_data.get("study_id") or "") != study_id:
        raise TrialRuntimeGateError(
            "Trial start does not match the active study configuration.",
            status_code=409,
            details={"active_session": False, "active_study_id": config_data.get("study_id")},
        )
    session = current_app.config["SESSION_STORE"].get(session_id)
    session_matches = (
        isinstance(session, dict)
        and session.get("status") == "active"
        and session.get("study_id") == study_id
        and session.get("participant_id") == participant_id
        and session.get("client_id") == client_id
    )
    if not session_matches:
        raise TrialRuntimeGateError(
            "No matching active study session exists for this trial start.",
            status_code=409,
            details={"active_session": False, "session_id": session_id, "client_id": client_id},
        )
    if not isinstance(current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG"), dict):
        raise TrialRuntimeGateError(
            "The matching session exists, but its sensor runtime is not active.",
            status_code=503,
            details={"active_session": True, "session_id": session.get("session_id")},
        )

    required_recording = list(required_recording_plugins(config_data))
    recording_status = None
    if required_recording:
        recording_runtime = current_app.config.get("RECORDING_RUNTIME_SERVICE")
        try:
            recording_status = (
                recording_runtime.current_status() if recording_runtime is not None else None
            )
        except Exception as error:
            raise TrialRuntimeGateError(
                "Required canonical recording health could not be verified.",
                status_code=503,
                details={
                    "active_session": True,
                    "session_id": session.get("session_id"),
                    "required_recording_plugins": required_recording,
                    "recording_error": str(error),
                },
            ) from error
        healthy = (
            isinstance(recording_status, dict)
            and recording_status.get("session_id") == session.get("session_id")
            and recording_status.get("status") == "recording"
            and int(recording_status.get("worker_health_failures") or 0) == 0
            and not recording_status.get("last_error")
        )
        if not healthy:
            raise TrialRuntimeGateError(
                "Required canonical recording is not active and healthy for this session.",
                status_code=503,
                details={
                    "active_session": True,
                    "session_id": session.get("session_id"),
                    "required_recording_plugins": required_recording,
                    "recording": recording_status,
                },
            )

    return {
        "active_session": True,
        "session_id": session.get("session_id"),
        "required_recording_plugins": required_recording,
        "recording": recording_status,
    }


@bp.route("/api/study-client/heartbeat", methods=["POST"])
def study_client_heartbeat():
    payload = request.get_json() or {}
    heartbeat_result = register_heartbeat(payload, request.remote_addr, request.headers.get("User-Agent", ""))
    clock_sync = current_app.config.get("CLOCK_SYNC_SERVICE")
    if clock_sync:
        clock_sync.record_offset_sample(
            source_id=heartbeat_result.get("client_id", ""),
            source_type="tablet",
            offset_ms=payload.get("clock_offset_ms", payload.get("client_clock_offset_ms")),
            rtt_ms=payload.get("clock_sync_rtt_ms"),
            sequence_number=payload.get("sequence_number"),
            metadata={"study_id": payload.get("study_id") or ""},
        )
        source_clock = clock_sync.source_summary(heartbeat_result.get("client_id", ""))
    else:
        source_clock = None
    recording_lease = None
    recording_runtime = current_app.config.get("RECORDING_RUNTIME_SERVICE")
    session_id = str(payload.get("session_id") or "").strip()
    if recording_runtime is not None and session_id:
        try:
            recording_lease = recording_runtime.refresh_lease(session_id)
        except Exception as error:
            recording_lease = {"state": "attention_required", "error": str(error)}
    return jsonify(
        {
            "ok": True,
            **heartbeat_result,
            "clock_sync": source_clock,
            "recording_lease": recording_lease,
            "sensor_runtime": _sensor_runtime_state(),
            "study_run_state": _participant_study_run_state(heartbeat_result.get("client_id")),
        }
    )


@bp.route("/api/sync-clock", methods=["POST"])
def sync_clock():
    """Clock-sync endpoint for tablet trigger precision against the Study Runner server."""
    data = request.get_json(force=True) or {}
    server_receive_ms = time.time() * 1000
    server_send_ms = time.time() * 1000
    clock_sync = current_app.config.get("CLOCK_SYNC_SERVICE")
    if clock_sync:
        clock_sync.record_server_exchange(
            source_id=data.get("client_id") or "",
            source_type="tablet",
            client_send_ms=data.get("client_send_ms"),
            server_receive_ms=server_receive_ms,
            server_send_ms=server_send_ms,
            metadata={"endpoint": "sync-clock"},
        )
    return jsonify(
        {
            "client_send_ms": data.get("client_send_ms"),
            "server_receive_ms": server_receive_ms,
            "server_send_ms": server_send_ms,
        }
    )
