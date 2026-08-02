"""Generic metadata and manifest-declared actions for built-in plugins."""
from flask import Blueprint, jsonify, send_file
from werkzeug.exceptions import BadRequest, Forbidden, UnsupportedMediaType

from study_runner.integrations.registry import (
    get_plugin_catalog_payload,
    ingest_participant_payload,
    resolve_plugin_ui_asset,
    run_admin_action,
    run_participant_action,
)

from .helpers import (
    _integration_context,
    _request_json_object,
    _require_secure_participant_ingest,
)


bp = Blueprint("plugins", __name__)


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


@bp.route(
    "/api/admin/plugins/<plugin_key>/actions/<action_key>",
    methods=["POST"],
)
def plugin_admin_action(plugin_key: str, action_key: str):
    """Execute only actions explicitly advertised by the plugin manifest."""

    try:
        payload = _request_json_object()
        return jsonify(
            run_admin_action(
                plugin_key,
                action_key,
                _integration_context(machine_admin=True),
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

    try:
        return jsonify(
            run_participant_action(
                plugin_key,
                action_key,
                _integration_context(),
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

    try:
        _require_secure_participant_ingest(plugin_key)
        return jsonify(
            ingest_participant_payload(
                plugin_key,
                ingest_key,
                _integration_context(),
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
