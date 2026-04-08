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


if __name__ == "__main__":
    local_ip = get_local_ip()

    print("\n" + "-" * 50)
    print("  Study Runner is running")
    print("  Admin page:  http://localhost:3000/admin")
    print(f"  Open on iPad: http://{local_ip}:3000")
    print("-" * 50 + "\n")

    app.run(host="0.0.0.0", port=3000, debug=is_debug_enabled())
