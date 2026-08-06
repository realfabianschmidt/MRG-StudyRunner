"""In-app updater endpoints (check, download, install, status)."""
import threading

from flask import Blueprint, current_app, jsonify, request

from ..services.settings.update_service import (
    UpdateError,
    build_update_status,
    check_for_update,
    download_and_stage_update,
    request_update_install,
)
from .helpers import _delayed_shutdown

bp = Blueprint("update", __name__)


@bp.route("/api/admin/update/status")
def admin_update_status():
    return jsonify(build_update_status(current_app.config))


@bp.route("/api/admin/update/check", methods=["POST"])
def admin_update_check():
    try:
        return jsonify(check_for_update(current_app.config))
    except UpdateError as error:
        return jsonify({"ok": False, "error": str(error)}), 400


@bp.route("/api/admin/update/download", methods=["POST"])
def admin_update_download():
    try:
        return jsonify(download_and_stage_update(current_app.config))
    except UpdateError as error:
        return jsonify({"ok": False, "error": str(error)}), 400


@bp.route("/api/admin/update/install", methods=["POST"])
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
