"""Internal Flask app/server module for Study Runner.

Use ``software/server.py`` as the local development entrypoint.
"""

import errno
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
from study_runner.backend.services.ssl_service import ensure_local_ssl_certificate


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
        cert_file, key_file, info = ensure_local_ssl_certificate(Path(app.config["SETTINGS_DIR"]))
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


def _fail_for_port_conflict(port: int) -> None:
    print("\n" + "!" * 50)
    print(f"  Port {port} is already in use.")
    print("  Is Study Runner (or another program) already running on this computer?")
    print("  Close the other program, or set STUDY_RUNNER_PORT to a free port,")
    print("  then start Study Runner again.")
    print("!" * 50 + "\n")
    raise SystemExit(1)


def _ensure_port_is_free(host: str, port: int) -> None:
    """Refuse to start when the port is taken, with a plain-language message.

    On Windows two processes can silently bind the same wildcard port
    (SO_REUSEADDR semantics), so a second Study Runner would appear to
    start fine while requests go to the first one. SO_EXCLUSIVEADDRUSE
    makes the probe bind fail instead.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((host, port))
    except OSError:
        probe.close()
        _fail_for_port_conflict(port)
    finally:
        probe.close()


def run_app() -> None:
    host = read_server_host()
    port = read_server_port()
    _ensure_port_is_free(host, port)
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

    try:
        app.run(host=host, port=port, debug=is_debug_enabled(), ssl_context=ssl_context)
    except OSError as error:
        # Second line of defense for a race between the probe and app.run.
        if error.errno in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", 10048)):
            _fail_for_port_conflict(port)
        raise


if __name__ == "__main__":
    run_app()
