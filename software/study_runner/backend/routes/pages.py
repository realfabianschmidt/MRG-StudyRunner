"""The HTML pages Study Runner serves.

The audit text moved into the settings shell, so there is no separate page
for it any more - the settings app is the container for that content.
"""
from flask import Blueprint, current_app, send_from_directory

bp = Blueprint("pages", __name__)


@bp.route("/")
def study_page():
    return send_from_directory(current_app.static_folder, "pages/study.html")


@bp.route("/admin")
def admin_page():
    return send_from_directory(current_app.static_folder, "pages/admin.html")
