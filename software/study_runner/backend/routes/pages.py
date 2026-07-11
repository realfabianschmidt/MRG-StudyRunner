"""The three HTML pages Study Runner serves."""
from flask import Blueprint, current_app, send_from_directory

bp = Blueprint("pages", __name__)


@bp.route("/")
def study_page():
    return send_from_directory(current_app.static_folder, "pages/study.html")


@bp.route("/admin")
def admin_page():
    return send_from_directory(current_app.static_folder, "pages/admin.html")


@bp.route("/audit")
def audit_page():
    return send_from_directory(current_app.static_folder, "pages/audit.html")
