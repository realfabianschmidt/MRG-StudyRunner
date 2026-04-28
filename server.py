"""
Study Runner - server.py
Runs on macOS or Windows and hosts the study for the iPad.
Start with: python server.py
Open on the iPad: http://<your-computer-ip>:3000
"""

import os
import socket

from app import create_app


app = create_app()


def get_local_ip() -> str:
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


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


if __name__ == "__main__":
    local_ip = get_local_ip()
    ssl_context = get_ssl_context()
    scheme = "https" if ssl_context else "http"

    print("\n" + "-" * 50)
    print("  Study Runner is running")
    print(f"  Admin page:  {scheme}://localhost:3000/admin")
    print(f"  Open on iPad: {scheme}://{local_ip}:3000")
    if ssl_context:
        print("  HTTPS enabled for browser camera access.")
    print("-" * 50 + "\n")

    app.run(host="0.0.0.0", port=3000, debug=is_debug_enabled(), ssl_context=ssl_context)
