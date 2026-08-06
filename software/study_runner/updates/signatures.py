"""Signed-update verification shared by the app, the manager, and release tooling.

Single source of truth for:
- the exact bytes a release signature covers (canonical_asset_payload),
- the platform key naming (detect_platform_key),
- Ed25519 public-key loading and signature verification.

This logic used to exist as three copies (backend update service,
tools/study_runner_manager.py, release_tools/build_python_update_manifest.py);
a fix applied to one copy silently broke compatibility with the others.

The wire format is FROZEN: installed 0.3.x clients verify release
manifests over exactly these bytes (see the byte-exact lock in
software/tests/test_route_inventory.py). Never change the payload shape.
"""
from __future__ import annotations

import base64
import json
import os
import platform
from typing import Any

UPDATER_SCHEMA_VERSION = 1
PUBLIC_KEY_ENV_VAR = "STUDY_RUNNER_UPDATE_PUBLIC_KEY"


class SignatureVerificationError(Exception):
    """Raised when keys are malformed or a signature does not verify."""


def canonical_asset_payload(version: str, platform_key: str, asset: dict[str, Any]) -> bytes:
    payload = {
        "schema": UPDATER_SCHEMA_VERSION,
        "version": str(version),
        "platform": str(platform_key),
        "url": str(asset.get("url") or ""),
        "sha256": str(asset.get("sha256") or "").lower(),
        "size": int(asset.get("size") or 0),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def detect_platform_key() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        arch = "x86_64"
    elif machine in {"aarch64", "arm64"}:
        arch = "arm64"
    else:
        arch = machine or "unknown"

    if system == "windows":
        os_key = "windows"
    elif system == "darwin":
        os_key = "macos"
    elif system == "linux":
        os_key = "linux"
    else:
        os_key = system or "unknown"
    return f"{os_key}-{arch}"


def load_trusted_public_keys() -> list:
    """Return the Ed25519 keys trusted for release signatures.

    Combines the optional env override with the keys baked into
    study_runner/updates/trusted_keys.py at release-build time. May be empty
    (source checkouts); malformed keys raise.
    """
    try:
        from study_runner.updates.trusted_keys import TRUSTED_UPDATE_PUBLIC_KEYS
    except Exception:
        TRUSTED_UPDATE_PUBLIC_KEYS = []

    raw_values: list[str] = []
    env_key = os.getenv(PUBLIC_KEY_ENV_VAR, "").strip()
    if env_key:
        raw_values.append(env_key)
    raw_values.extend(str(value).strip() for value in TRUSTED_UPDATE_PUBLIC_KEYS if str(value).strip())
    return [load_public_key(raw_value) for raw_value in raw_values]


def load_public_key(raw_value: str):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    raw_value = raw_value.strip()
    try:
        if "BEGIN PUBLIC KEY" in raw_value:
            key = serialization.load_pem_public_key(raw_value.encode("utf-8"))
            if isinstance(key, Ed25519PublicKey):
                return key
            raise SignatureVerificationError("Configured update public key is not an Ed25519 key.")

        key_bytes = decode_base64(raw_value)
        if len(key_bytes) != 32:
            raise SignatureVerificationError("Configured update public key must be a 32-byte base64 Ed25519 key.")
        return Ed25519PublicKey.from_public_bytes(key_bytes)
    except ValueError as error:
        raise SignatureVerificationError("Configured update public key is not valid base64 or PEM.") from error


def verify_asset_signature(
    version: str,
    platform_key: str,
    asset: dict[str, Any],
    public_keys: list | None = None,
) -> None:
    """Verify the asset signature against the trusted keys or raise."""
    from cryptography.exceptions import InvalidSignature

    signature = decode_base64(str(asset.get("signature") or ""))
    payload = canonical_asset_payload(version, platform_key, asset)
    keys = public_keys if public_keys is not None else load_trusted_public_keys()
    for public_key in keys:
        try:
            public_key.verify(signature, payload)
            return
        except InvalidSignature:
            continue
    raise SignatureVerificationError("Update asset signature could not be verified.")


def decode_base64(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)
