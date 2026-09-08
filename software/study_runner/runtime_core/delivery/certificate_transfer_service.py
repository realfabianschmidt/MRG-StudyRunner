"""Move the local root CA to another server computer.

Every computer normally generates its own root CA, which is why setting up a
replacement machine used to mean installing a new certificate on every tablet
again. Exporting the CA here and importing it there lets the tablet keep
trusting what it already trusts.

Safety rules this module enforces:
  - An import is fully validated before any file is touched, so a wrong file
    can never leave the server unable to start HTTPS.
  - The previous CA is kept as a timestamped backup.
  - The server certificate is removed on import, because it was signed by the
    old CA and would otherwise present a broken chain to the tablet.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from study_runner.shared.atomic_io import atomic_write_bytes


EXPORT_SCHEMA = 1
EXPORT_FILENAME = "study-runner-root-ca-backup.json"

_ROOT_CERT_NAME = "study-runner-local-root-ca.crt"
_ROOT_KEY_NAME = "study-runner-local-root-ca.key"
_SERVER_FILE_NAMES = (
    "study-runner-local-server.crt",
    "study-runner-local-server.key",
    "study-runner-local-server-chain.crt",
)


class CertificateTransferError(RuntimeError):
    """Raised with an operator-readable message; never leaks file contents."""


def ssl_dir(settings_dir: Path) -> Path:
    return Path(settings_dir) / "ssl"


def build_export(settings_dir: Path) -> dict[str, Any]:
    """Return the transferable root-CA bundle as a JSON-serializable dict."""
    directory = ssl_dir(settings_dir)
    cert_file = directory / _ROOT_CERT_NAME
    key_file = directory / _ROOT_KEY_NAME
    if not cert_file.exists() or not key_file.exists():
        raise CertificateTransferError(
            "This computer has no local certificate yet. Start Study Runner with HTTPS once, then export."
        )

    certificate_pem = cert_file.read_text(encoding="utf-8")
    private_key_pem = key_file.read_text(encoding="utf-8")
    fingerprint, expires_at = _describe_certificate(certificate_pem)
    return {
        "study_runner_root_ca_export": EXPORT_SCHEMA,
        "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "fingerprint_sha256": fingerprint,
        "expires_at": expires_at,
        "root_ca_certificate": certificate_pem,
        "root_ca_private_key": private_key_pem,
    }


def inspect_import(payload: Any) -> dict[str, Any]:
    """Validate a bundle and describe it, without writing anything."""
    if not isinstance(payload, dict):
        raise CertificateTransferError("This file is not a Study Runner certificate backup.")
    if payload.get("study_runner_root_ca_export") != EXPORT_SCHEMA:
        raise CertificateTransferError(
            "This file is not a Study Runner certificate backup, or it was made by a newer version."
        )

    certificate_pem = _require_pem(payload.get("root_ca_certificate"), "certificate")
    private_key_pem = _require_pem(payload.get("root_ca_private_key"), "private key")
    certificate = _load_certificate(certificate_pem)
    private_key = _load_private_key(private_key_pem)

    if not _is_certificate_authority(certificate):
        raise CertificateTransferError("The certificate in this file is not a certificate authority.")
    if not _key_matches_certificate(private_key, certificate):
        raise CertificateTransferError("The certificate and the key in this file do not belong together.")

    expires_at = _expiry(certificate)
    if expires_at <= dt.datetime.now(dt.timezone.utc):
        raise CertificateTransferError("The certificate in this file has expired and cannot be used.")

    return {
        "fingerprint_sha256": _fingerprint(certificate),
        "expires_at": expires_at.isoformat(),
        "certificate_pem": certificate_pem,
        "private_key_pem": private_key_pem,
    }


def apply_import(settings_dir: Path, payload: Any) -> dict[str, Any]:
    """Install a validated bundle as this computer's root CA."""
    described = inspect_import(payload)

    directory = ssl_dir(settings_dir)
    directory.mkdir(parents=True, exist_ok=True)
    cert_file = directory / _ROOT_CERT_NAME
    key_file = directory / _ROOT_KEY_NAME

    replaced_fingerprint = None
    backup_suffix = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    if cert_file.exists():
        try:
            replaced_fingerprint, _ = _describe_certificate(cert_file.read_text(encoding="utf-8"))
        except CertificateTransferError:
            replaced_fingerprint = None
        if replaced_fingerprint == described["fingerprint_sha256"]:
            return {
                "ok": True,
                "unchanged": True,
                "fingerprint_sha256": described["fingerprint_sha256"],
                "expires_at": described["expires_at"],
            }

    backups: list[tuple[Path, Path]] = []
    retired_server_files: list[tuple[Path, Path]] = []
    try:
        _backup_existing(directory, backup_suffix, backups)
        # Each file is flushed and atomically replaced. The rollback below also
        # keeps the certificate/key pair coherent if the second write fails.
        atomic_write_bytes(cert_file, described["certificate_pem"].encode("utf-8"))
        atomic_write_bytes(key_file, described["private_key_pem"].encode("utf-8"))
        # Signed by the old CA: retire it so the next start issues a matching
        # leaf certificate. These moves belong to the same rollback unit.
        _retire_server_files(directory, backup_suffix, retired_server_files)
    except OSError as error:
        _rollback_import(cert_file, key_file, backups, retired_server_files)
        raise CertificateTransferError(f"Could not install the imported certificate: {error}") from error

    return {
        "ok": True,
        "unchanged": False,
        "fingerprint_sha256": described["fingerprint_sha256"],
        "expires_at": described["expires_at"],
        "replaced_fingerprint_sha256": replaced_fingerprint,
        "backup_suffix": backup_suffix,
        "reissued_server_files": [source.name for source, _ in retired_server_files],
        "restart_required": True,
    }


def parse_bundle_bytes(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CertificateTransferError("This file is not readable as a Study Runner certificate backup.") from error


def _backup_existing(directory: Path, suffix: str, moved: list[tuple[Path, Path]]) -> None:
    for name in (_ROOT_CERT_NAME, _ROOT_KEY_NAME):
        source = directory / name
        if not source.exists():
            continue
        target = directory / f"{name}.backup-{suffix}"
        source.replace(target)
        moved.append((source, target))


def _restore(moved: list[tuple[Path, Path]]) -> None:
    for source, target in reversed(moved):
        if target.exists():
            target.replace(source)


def _retire_server_files(directory: Path, suffix: str, moved: list[tuple[Path, Path]]) -> None:
    for name in _SERVER_FILE_NAMES:
        source = directory / name
        if not source.exists():
            continue
        target = directory / f"{name}.replaced-{suffix}"
        source.replace(target)
        moved.append((source, target))


def _rollback_import(
    cert_file: Path,
    key_file: Path,
    backups: list[tuple[Path, Path]],
    retired_server_files: list[tuple[Path, Path]],
) -> None:
    _restore(retired_server_files)
    for installed in (cert_file, key_file):
        try:
            installed.unlink(missing_ok=True)
        except OSError:
            pass
    _restore(backups)


def _describe_certificate(certificate_pem: str) -> tuple[str, str]:
    certificate = _load_certificate(certificate_pem)
    return _fingerprint(certificate), _expiry(certificate).isoformat()


def _require_pem(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text or "-----BEGIN" not in text:
        raise CertificateTransferError(f"The backup file is missing its {label}.")
    return text if text.endswith("\n") else f"{text}\n"


def _load_certificate(certificate_pem: str):
    try:
        from cryptography import x509

        return x509.load_pem_x509_certificate(certificate_pem.encode("utf-8"))
    except ImportError as error:  # pragma: no cover - dependency is pinned
        raise CertificateTransferError("The certificate library is unavailable on this computer.") from error
    except Exception as error:
        raise CertificateTransferError("The certificate in this file could not be read.") from error


def _load_private_key(private_key_pem: str):
    try:
        from cryptography.hazmat.primitives import serialization

        return serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    except ImportError as error:  # pragma: no cover - dependency is pinned
        raise CertificateTransferError("The certificate library is unavailable on this computer.") from error
    except Exception as error:
        raise CertificateTransferError(
            "The key in this file could not be read. Password-protected keys are not supported."
        ) from error


def _is_certificate_authority(certificate) -> bool:
    try:
        from cryptography import x509
        from cryptography.x509.oid import ExtensionOID

        basic = certificate.extensions.get_extension_for_oid(ExtensionOID.BASIC_CONSTRAINTS).value
        return bool(basic.ca)
    except Exception:
        return False


def _key_matches_certificate(private_key, certificate) -> bool:
    try:
        from cryptography.hazmat.primitives import serialization

        encoding = serialization.Encoding.DER
        fmt = serialization.PublicFormat.SubjectPublicKeyInfo
        return private_key.public_key().public_bytes(encoding, fmt) == certificate.public_key().public_bytes(
            encoding, fmt
        )
    except Exception:
        return False


def _fingerprint(certificate) -> str:
    from cryptography.hazmat.primitives import hashes

    raw = certificate.fingerprint(hashes.SHA256()).hex().upper()
    return ":".join(raw[index:index + 2] for index in range(0, len(raw), 2))


def _expiry(certificate) -> dt.datetime:
    return (
        certificate.not_valid_after_utc
        if hasattr(certificate, "not_valid_after_utc")
        else certificate.not_valid_after.replace(tzinfo=dt.timezone.utc)
    )
