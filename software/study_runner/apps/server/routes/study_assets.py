"""Study images and portable study packages.

This layer only moves bytes. What an image may be, how it is named, and what a
package must contain live in ``study_assets_service`` and
``study_package_service``.
"""
from __future__ import annotations

from pathlib import Path
import re

from flask import Blueprint, Response, current_app, jsonify, request

from study_runner.runtime_core.studies.study_assets_service import (
    MAX_ASSET_BYTES,
    StudyAssetError,
    resolve_asset,
    store_asset,
)
from study_runner.runtime_core.studies.study_config_service import load_study
from study_runner.runtime_core.studies.study_package_service import (
    MAX_PACKAGE_BYTES,
    StudyPackageError,
    build_package,
    read_package,
    store_package_assets,
)
from study_runner.runtime_core.studies.validation import validate_and_normalize_config


bp = Blueprint("study_assets", __name__)


def _studies_dir() -> Path:
    return Path(current_app.config["SAVED_STUDIES_DIR"])


@bp.route("/api/study-assets/<asset_id>", methods=["GET"])
def study_asset(asset_id: str):
    try:
        path, content_type = resolve_asset(_studies_dir(), asset_id)
    except StudyAssetError as error:
        return jsonify({"ok": False, "error": str(error)}), 404
    response = Response(path.read_bytes(), mimetype=content_type)
    # Operator-supplied bytes: never sniffed, never scripted (pages use <img>).
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    # The id is the content hash, so the bytes behind a URL never change.
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


@bp.route("/api/admin/study-assets", methods=["POST"])
def study_asset_upload():
    uploaded = request.files.get("file")
    if uploaded is None:
        return jsonify({"ok": False, "error": "No image file was sent."}), 400
    try:
        asset_id = store_asset(_studies_dir(), uploaded.read(MAX_ASSET_BYTES + 1))
    except StudyAssetError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "asset_id": asset_id, "url": f"/api/study-assets/{asset_id}"})


@bp.route("/api/admin/studies/<study_id>/package", methods=["GET"])
def study_package_export(study_id: str):
    try:
        config = validate_and_normalize_config(load_study(_studies_dir(), study_id))
        package = build_package(_studies_dir(), config)
    except StudyPackageError as error:
        return jsonify({"ok": False, "error": str(error)}), 409
    except Exception as error:  # load_study reports a missing study this way
        return jsonify({"ok": False, "error": str(error)}), 404
    filename = re.sub(r"[^A-Za-z0-9 ._-]+", "_", str(config.get("study_id") or study_id)).strip() or "study"
    response = Response(package, mimetype="application/zip")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}.study-runner"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.route("/api/admin/studies/import", methods=["POST"])
def study_package_import():
    """Unpack a study file and store its images; the editor then saves the study."""
    uploaded = request.files.get("file")
    if uploaded is None:
        return jsonify({"ok": False, "error": "No study file was sent."}), 400
    try:
        config, assets = read_package(uploaded.read(MAX_PACKAGE_BYTES + 1))
        store_package_assets(_studies_dir(), assets)
    except (StudyPackageError, StudyAssetError) as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "config": config, "asset_count": len(assets)})
