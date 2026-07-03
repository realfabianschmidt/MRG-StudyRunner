"""Internal Flask app/server module for Study Runner.

Use ``software/server.py`` as the local development entrypoint.
"""

import os
import sys
import threading
import time
import webbrowser

from study_runner.backend import create_app
from study_runner.backend.services.runtime_config import (
    get_app_mode,
    get_local_private_ips,
    read_server_host,
    read_server_port,
)


app = create_app()


def get_local_ip() -> str:
    return get_local_private_ips()[0]


def is_debug_enabled() -> bool:
    return os.getenv("STUDY_RUNNER_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


def get_ssl_context():
    """Return an SSL context when HTTPS is requested for camera access on iPad."""
    if os.getenv("STUDY_RUNNER_HTTPS", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return None

    cert_file = os.getenv("STUDY_RUNNER_SSL_CERT", "").strip()
    key_file = os.getenv("STUDY_RUNNER_SSL_KEY", "").strip()
    if cert_file and key_file:
        return (cert_file, key_file)

    return "adhoc"


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
    print("-" * 50 + "\n")

    if should_open_admin_browser():
        open_admin_browser_later(admin_url)

    app.run(host=host, port=port, debug=is_debug_enabled(), ssl_context=ssl_context)


if __name__ == "__main__":
    run_app()
