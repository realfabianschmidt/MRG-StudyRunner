"""Generic metadata, actions, and local plugin-driver consoles."""
import ipaddress
import json
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request, send_file, stream_with_context
from werkzeug.exceptions import BadRequest, Forbidden, NotFound, UnsupportedMediaType

from study_runner.plugin_framework.registry import (
    get_plugin,
    get_plugin_catalog_payload,
    ingest_participant_payload,
    resolve_plugin_ui_asset,
    run_admin_action,
    run_participant_action,
)
from study_runner.plugin_framework.process_host import (
    ConsoleLockedError,
    get_process_runtime,
)

from .helpers import (
    _plugin_context,
    _request_json_object,
    _require_secure_participant_ingest,
    _study_run_state,
)


bp = Blueprint("plugins", __name__)


@bp.errorhandler(Forbidden)
def _plugin_forbidden(error):
    return jsonify({"ok": False, "error": error.description}), 403


@bp.errorhandler(BadRequest)
def _plugin_bad_request(error):
    return jsonify({"ok": False, "error": error.description}), 400


@bp.errorhandler(NotFound)
def _plugin_not_found(error):
    return jsonify({"ok": False, "error": error.description}), 404


def _require_installed_plugin(plugin_key: str) -> None:
    if get_plugin(plugin_key) is None:
        raise NotFound(f"Plugin '{plugin_key}' is not installed.")


def _require_loopback_admin() -> None:
    try:
        address = ipaddress.ip_address(str(request.remote_addr or ""))
    except ValueError as error:
        raise Forbidden("Plugin console access is local-only.") from error
    if not address.is_loopback:
        raise Forbidden("Plugin console access is local-only.")


def _require_console_runtime(plugin_key: str):
    _require_installed_plugin(plugin_key)
    runtime = get_process_runtime(plugin_key)
    if runtime is None or not runtime.runtime_config.get("interactive_stdin", False):
        raise BadRequest(f"Plugin '{plugin_key}' does not expose an interactive console.")
    return runtime


def _study_running() -> bool:
    return _study_run_state().get("status") == "running"


def _safe_artifact_name(value: str) -> str:
    normalized = "".join(character for character in str(value) if character.isalnum() or character in "-_")
    return normalized or "unknown"


@bp.route("/api/plugins/catalog")
def plugin_catalog():
    return jsonify(get_plugin_catalog_payload())


@bp.route("/api/plugins/<plugin_key>/assets/<path:asset_path>")
def plugin_ui_asset(plugin_key: str, asset_path: str):
    """Serve one trusted asset only when the normalized manifest declares it."""

    try:
        path = resolve_plugin_ui_asset(plugin_key, asset_path)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 404
    response = send_file(path, mimetype="text/javascript", conditional=True)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.route("/api/admin/plugins/<plugin_key>/console", methods=["GET"])
def plugin_console_snapshot(plugin_key: str):
    _require_loopback_admin()
    runtime = _require_console_runtime(plugin_key)
    state = _study_run_state()
    run_id = str(state.get("run_id") or "") if state.get("status") == "running" else ""
    snapshot = runtime.snapshot()
    snapshot["console_unlocked"] = runtime.console_unlocked_for(run_id) if run_id else False
    return jsonify({**snapshot, "study_running": state.get("status") == "running"})


@bp.route("/api/admin/plugins/<plugin_key>/console/events", methods=["GET"])
def plugin_console_events(plugin_key: str):
    _require_loopback_admin()
    runtime = _require_console_runtime(plugin_key)
    try:
        after = int(request.headers.get("Last-Event-ID") or request.args.get("after") or 0)
    except ValueError:
        after = 0

    @stream_with_context
    def generate():
        sequence = max(0, after)
        yield "retry: 1000\n\n"
        while True:
            entries = runtime.wait_for_output(sequence, timeout=15.0)
            if not entries:
                yield ": keepalive\n\n"
                continue
            for entry in entries:
                sequence = max(sequence, int(entry.get("sequence") or 0))
                encoded = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
                yield f"id: {sequence}\ndata: {encoded}\n\n"

    return Response(generate(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache"})


@bp.route("/api/admin/plugins/<plugin_key>/console/input", methods=["POST"])
def plugin_console_input(plugin_key: str):
    _require_loopback_admin()
    runtime = _require_console_runtime(plugin_key)
    try:
        payload = _request_json_object()
        state = _study_run_state()
        running = state.get("status") == "running"
        result = runtime.write_console_line(
            payload.get("line"),
            study_running=running,
            run_id=str(state.get("run_id") or "") if running else None,
        )
        return jsonify(result)
    except ConsoleLockedError as error:
        return jsonify({"ok": False, "error": str(error), "locked": True}), 423
    except (TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": str(error)}), 400


@bp.route("/api/admin/plugins/<plugin_key>/console/unlock", methods=["POST"])
def plugin_console_unlock(plugin_key: str):
    _require_loopback_admin()
    runtime = _require_console_runtime(plugin_key)
    payload = _request_json_object()
    reason = str(payload.get("reason") or "").strip()
    if payload.get("confirm") is not True:
        return jsonify({"ok": False, "error": "Explicit confirmation is required."}), 400
    if not reason:
        return jsonify({"ok": False, "error": "An intervention reason is required."}), 400
    if len(reason) > 500:
        return jsonify({"ok": False, "error": "Intervention reason exceeds 500 characters."}), 400
    state = _study_run_state()
    is_running = state.get("status") == "running"
    raw_run_id = str(state.get("run_id") or "") if is_running else ""
    if is_running:
        run_id = _safe_artifact_name(raw_run_id or "active-study")
        transcript = (
            Path(current_app.config["DATA_DIR"])
            / "runtime"
            / "operator_interventions"
            / run_id
            / f"{_safe_artifact_name(plugin_key)}.jsonl"
        )
        try:
            runtime.begin_intervention_transcript(
                transcript,
                run_id=raw_run_id,
                reason=reason,
            )
        except OSError:
            return jsonify(
                {
                    "ok": False,
                    "error": "The private intervention transcript is not writable.",
                }
            ), 507
    unlocked_until = runtime.unlock_console(600, run_id=raw_run_id or None)
    return jsonify(
        {
            "ok": True,
            "plugin_key": plugin_key,
            "unlocked_until_epoch": unlocked_until,
            "study_running": is_running,
            "operator_intervention": is_running,
        }
    )


@bp.route(
    "/api/admin/plugins/<plugin_key>/actions/<action_key>",
    methods=["POST"],
)
def plugin_admin_action(plugin_key: str, action_key: str):
    """Execute only actions explicitly advertised by the plugin manifest."""

    _require_installed_plugin(plugin_key)
    try:
        payload = _request_json_object()
        return jsonify(
            run_admin_action(
                plugin_key,
                action_key,
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


@bp.route(
    "/api/plugins/<plugin_key>/participant/actions/<action_key>",
    methods=["POST"],
)
def plugin_participant_action(plugin_key: str, action_key: str):
    """Execute a participant action only when its manifest declares the key."""

    _require_installed_plugin(plugin_key)
    try:
        return jsonify(
            run_participant_action(
                plugin_key,
                action_key,
                _plugin_context(),
                _request_json_object(),
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


@bp.route(
    "/api/plugins/<plugin_key>/participant/ingest/<ingest_key>",
    methods=["POST"],
)
def plugin_participant_ingest(plugin_key: str, ingest_key: str):
    """Deliver participant data through a manifest-declared plugin input."""

    _require_installed_plugin(plugin_key)
    try:
        _require_secure_participant_ingest(plugin_key)
        return jsonify(
            ingest_participant_payload(
                plugin_key,
                ingest_key,
                _plugin_context(),
                _request_json_object(),
            )
        )
    except Forbidden as error:
        return jsonify({"ok": False, "error": error.description}), 403
    except UnsupportedMediaType as error:
        return jsonify({"ok": False, "error": error.description}), 415
    except BadRequest as error:
        return jsonify({"ok": False, "error": error.description}), 400
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500
