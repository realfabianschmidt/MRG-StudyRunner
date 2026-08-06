"""Branding endpoints: the group mark and funder logos on the waiting slide.

This layer only moves bytes. Which files are allowed, how large they may be, and
how a slot maps to a file all live in ``branding_service``.
"""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request

from ..services.branding_service import (
    MAX_ASSET_BYTES,
    BrandingError,
    public_manifest,
    remove_asset,
    resolve_asset,
    store_asset,
)


bp = Blueprint("branding", __name__)


def _branding_dir() -> Path:
    return Path(current_app.config["BRANDING_DIR"])


@bp.route("/api/branding", methods=["GET"])
def branding_manifest():
    """What the participant slide and the hub header need to render."""
    return jsonify({"ok": True, "branding": public_manifest(_branding_dir())})


@bp.route("/api/branding/asset/<slot>", methods=["GET"])
def branding_asset(slot: str):
    try:
        path, content_type = resolve_asset(_branding_dir(), slot)
    except BrandingError as error:
        return jsonify({"ok": False, "error": str(error)}), 404

    response = Response(path.read_bytes(), mimetype=content_type)
    # The bytes are operator-supplied. nosniff stops a mislabelled file being
    # re-interpreted, and the pages render these through <img> so script inside
    # an uploaded SVG never executes.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.route("/api/admin/branding/<slot>", methods=["POST"])
def branding_upload(slot: str):
    uploaded = request.files.get("file")
    if uploaded is None:
        return jsonify({"ok": False, "error": "No logo file was sent."}), 400

    payload = uploaded.read(MAX_ASSET_BYTES + 1)
    try:
        branding = store_asset(
            _branding_dir(),
            slot,
            uploaded.filename or "",
            payload,
            request.form.get("alt", ""),
        )
    except BrandingError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "branding": branding})


@bp.route("/api/admin/branding/<slot>", methods=["DELETE"])
def branding_delete(slot: str):
    try:
        branding = remove_asset(_branding_dir(), slot)
    except BrandingError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "branding": branding})
