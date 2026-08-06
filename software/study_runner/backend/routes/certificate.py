"""Certificate page endpoints: status, and moving the root CA between computers."""
from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request

from ..services.delivery.certificate_transfer_service import (
    EXPORT_FILENAME,
    CertificateTransferError,
    apply_import,
    build_export,
    parse_bundle_bytes,
)


bp = Blueprint("certificate", __name__)
MAX_IMPORT_BYTES = 1024 * 1024


@bp.route("/api/admin/certificate/status", methods=["GET"])
def certificate_status():
    """Everything the certificate page shows, in one call."""
    info = current_app.config.get("HTTPS_CERTIFICATE", {}) or {}
    mode = info.get("mode") or "unknown"
    download_urls = info.get("download_urls") or []
    return jsonify(
        {
            "ok": True,
            "mode": mode,
            "https_active": mode in {"generated", "configured", "adhoc"},
            "trust_required": bool(info.get("trust_required")),
            "root_ca_file": info.get("root_ca_file"),
            "root_ca_fingerprint_sha256": info.get("root_ca_fingerprint_sha256"),
            "root_ca_expires_at": info.get("root_ca_expires_at"),
            "server_expires_at": info.get("server_expires_at"),
            "dns_names": info.get("dns_names") or [],
            "ip_addresses": info.get("ip_addresses") or [],
            "download_status": info.get("download_status") or "unknown",
            "download_urls": download_urls,
            "download_url": download_urls[0] if download_urls else "",
            "download_error": info.get("download_error"),
        }
    )


@bp.route("/api/admin/certificate/export", methods=["GET"])
def certificate_export():
    """Download this computer's root CA so another computer can reuse it."""
    try:
        payload = build_export(Path(current_app.config["SETTINGS_DIR"]))
    except CertificateTransferError as error:
        return jsonify({"ok": False, "error": str(error)}), 400

    body = json.dumps(payload, indent=2, ensure_ascii=False)
    return Response(
        body,
        mimetype="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{EXPORT_FILENAME}"',
            # The bundle contains a private key: never let a proxy or the
            # browser keep a copy around.
            "Cache-Control": "no-store",
        },
    )


@bp.route("/api/admin/certificate/import", methods=["POST"])
def certificate_import():
    """Install a root CA exported from another computer."""
    try:
        raw = _read_uploaded_bundle()
        if raw is None:
            raise CertificateTransferError("No certificate backup file was sent.")
        payload = parse_bundle_bytes(raw)
        result = apply_import(Path(current_app.config["SETTINGS_DIR"]), payload)
    except CertificateTransferError as error:
        return jsonify({"ok": False, "error": str(error)}), 400

    return jsonify(result)


def _read_uploaded_bundle() -> bytes | None:
    """Accept either a file upload or a raw JSON body."""
    if request.content_length is not None and request.content_length > MAX_IMPORT_BYTES:
        raise CertificateTransferError("The certificate backup is larger than the 1 MB limit.")
    uploaded = request.files.get("file")
    if uploaded is not None:
        raw = uploaded.read(MAX_IMPORT_BYTES + 1)
    elif request.data:
        raw = request.data
    else:
        return None
    if len(raw) > MAX_IMPORT_BYTES:
        raise CertificateTransferError("The certificate backup is larger than the 1 MB limit.")
    return raw
