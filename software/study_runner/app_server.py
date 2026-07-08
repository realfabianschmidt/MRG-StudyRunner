"""Internal Flask app/server module for Study Runner.

Use ``software/server.py`` as the local development entrypoint.
"""

import datetime as dt
import ipaddress
import os
from pathlib import Path
import socket
import sys
import threading
import time
import webbrowser

from study_runner.backend import create_app
from study_runner.backend.services.runtime_config import (
    get_app_mode,
    get_local_private_ips,
    is_https_enabled,
    read_server_host,
    read_server_port,
)


app = create_app()
_SSL_CERTIFICATE_INFO: dict = {}


def get_local_ip() -> str:
    return get_local_private_ips()[0]


def is_debug_enabled() -> bool:
    return os.getenv("STUDY_RUNNER_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


def get_ssl_context():
    """Return an SSL context unless HTTPS was explicitly disabled."""
    if not is_https_enabled():
        return None

    cert_file = os.getenv("STUDY_RUNNER_SSL_CERT", "").strip()
    key_file = os.getenv("STUDY_RUNNER_SSL_KEY", "").strip()
    if cert_file and key_file:
        _set_ssl_certificate_info(
            {
                "mode": "configured",
                "cert_file": cert_file,
                "key_file": key_file,
                "trust_required": True,
            }
        )
        return (cert_file, key_file)

    try:
        cert_file, key_file, info = ensure_local_ssl_certificate()
        _set_ssl_certificate_info(info)
        return (str(cert_file), str(key_file))
    except Exception as error:
        _set_ssl_certificate_info(
            {
                "mode": "adhoc",
                "error": str(error),
                "trust_required": True,
            }
        )
        print(f"  Could not prepare persistent local HTTPS certificate: {error}")
        print("  Falling back to Flask adhoc HTTPS certificate.")
        return "adhoc"


def ensure_local_ssl_certificate() -> tuple[Path, Path, dict]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    ssl_dir = Path(app.config["SETTINGS_DIR"]) / "ssl"
    ssl_dir.mkdir(parents=True, exist_ok=True)
    root_key_file = ssl_dir / "study-runner-local-root-ca.key"
    root_cert_file = ssl_dir / "study-runner-local-root-ca.crt"
    server_key_file = ssl_dir / "study-runner-local-server.key"
    server_cert_file = ssl_dir / "study-runner-local-server.crt"
    server_chain_file = ssl_dir / "study-runner-local-server-chain.crt"

    root_key, root_cert = _load_or_create_root_ca(root_key_file, root_cert_file)
    dns_names, ip_addresses = _local_certificate_names()
    if not _server_certificate_matches(server_cert_file, server_key_file, dns_names, ip_addresses):
        server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, "Study Runner Local Server"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Study Runner Local"),
            ]
        )
        now = dt.datetime.now(dt.timezone.utc)
        alt_names = [x509.DNSName(name) for name in sorted(dns_names)]
        alt_names.extend(x509.IPAddress(ipaddress.ip_address(ip)) for ip in sorted(ip_addresses))
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(root_cert.subject)
            .public_key(server_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=397))
            .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=True,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(root_key, hashes.SHA256())
        )
        _write_private_key(server_key_file, server_key)
        server_cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    server_pem = server_cert_file.read_bytes()
    root_pem = root_cert_file.read_bytes()
    server_chain_file.write_bytes(server_pem + root_pem)
    fingerprint = _certificate_fingerprint_sha256(root_cert_file)
    return (
        server_chain_file,
        server_key_file,
        {
            "mode": "generated",
            "cert_file": str(server_chain_file),
            "key_file": str(server_key_file),
            "root_ca_file": str(root_cert_file),
            "root_ca_fingerprint_sha256": fingerprint,
            "dns_names": sorted(dns_names),
            "ip_addresses": sorted(ip_addresses),
            "trust_required": True,
        },
    )


def _load_or_create_root_ca(root_key_file: Path, root_cert_file: Path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    if root_key_file.exists() and root_cert_file.exists():
        key = serialization.load_pem_private_key(root_key_file.read_bytes(), password=None)
        cert = x509.load_pem_x509_certificate(root_cert_file.read_bytes())
        return key, cert

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "Study Runner Local Root CA"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Study Runner Local"),
        ]
    )
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    _write_private_key(root_key_file, key)
    root_cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key, cert


def _server_certificate_matches(cert_file: Path, key_file: Path, dns_names: set[str], ip_addresses: set[str]) -> bool:
    if not cert_file.exists() or not key_file.exists():
        return False
    try:
        from cryptography import x509
        from cryptography.x509.oid import ExtensionOID

        cert = x509.load_pem_x509_certificate(cert_file.read_bytes())
        expires = getattr(cert, "not_valid_after_utc", cert.not_valid_after.replace(tzinfo=dt.timezone.utc))
        if expires <= dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30):
            return False
        san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
        current_dns = set(san.get_values_for_type(x509.DNSName))
        current_ips = {str(value) for value in san.get_values_for_type(x509.IPAddress)}
        return dns_names.issubset(current_dns) and ip_addresses.issubset(current_ips)
    except Exception:
        return False


def _write_private_key(path: Path, key) -> None:
    from cryptography.hazmat.primitives import serialization

    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def _local_certificate_names() -> tuple[set[str], set[str]]:
    dns_names = {"localhost"}
    for hostname in {socket.gethostname(), socket.getfqdn()}:
        normalized = str(hostname or "").strip()
        if normalized:
            dns_names.add(normalized)

    ip_addresses = {"127.0.0.1", *get_local_private_ips()}
    return dns_names, ip_addresses


def _certificate_fingerprint_sha256(cert_file: Path) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    cert = x509.load_pem_x509_certificate(cert_file.read_bytes())
    raw = cert.fingerprint(hashes.SHA256()).hex().upper()
    return ":".join(raw[index:index + 2] for index in range(0, len(raw), 2))


def _set_ssl_certificate_info(info: dict) -> None:
    global _SSL_CERTIFICATE_INFO
    _SSL_CERTIFICATE_INFO = dict(info)
    app.config["HTTPS_CERTIFICATE"] = dict(info)


def should_open_admin_browser() -> bool:
    if os.getenv("STUDY_RUNNER_NO_BROWSER", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    return bool(getattr(sys, "frozen", False) or get_app_mode() == "packaged")


def open_admin_browser_later(url: str) -> None:
    def _open() -> None:
        time.sleep(0.8)
        try:
            webbrowser.open(url, new=2)
        except Exception as error:
            print(f"  Could not open browser automatically: {error}")

    threading.Thread(target=_open, daemon=True).start()


def run_app() -> None:
    host = read_server_host()
    port = read_server_port()
    local_ips = get_local_private_ips()
    ssl_context = get_ssl_context()
    scheme = "https" if ssl_context else "http"
    admin_url = f"{scheme}://localhost:{port}/admin"

    print("\n" + "-" * 50)
    print("  Study Runner is running")
    print(f"  Admin page:  {admin_url}")
    print(f"  Open on tablet: {scheme}://{local_ips[0]}:{port}")
    if len(local_ips) > 1:
        print(f"  Other local addresses: {', '.join(local_ips[1:])}")
    print(f"  Data folder: {app.config['DATA_DIR']}")
    if ssl_context:
        print("  HTTPS enabled for browser camera access.")
        certificate_info = app.config.get("HTTPS_CERTIFICATE", {})
        if certificate_info.get("mode") == "generated":
            print(f"  iPad trust certificate: {certificate_info.get('root_ca_file')}")
            print("  Install this Root CA on the iPad and enable full trust in iOS settings.")
            print(f"  Root CA SHA256: {certificate_info.get('root_ca_fingerprint_sha256')}")
        elif certificate_info.get("mode") == "adhoc":
            print("  Warning: using temporary adhoc certificate. Tablets may reject it.")
    print("-" * 50 + "\n")

    if should_open_admin_browser():
        open_admin_browser_later(admin_url)

    app.run(host=host, port=port, debug=is_debug_enabled(), ssl_context=ssl_context)


if __name__ == "__main__":
    run_app()
