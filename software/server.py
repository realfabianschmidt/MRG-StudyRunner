"""Compatibility entrypoint for running Study Runner from the repository root.

Keep using:

    python server.py
"""

from study_runner.server import app, get_local_ip, get_ssl_context, is_debug_enabled
from study_runner.backend.services.runtime_config import get_local_private_ips, read_server_host, read_server_port


if __name__ == "__main__":
    host = read_server_host()
    port = read_server_port()
    local_ips = get_local_private_ips()
    ssl_context = get_ssl_context()
    scheme = "https" if ssl_context else "http"

    print("\n" + "-" * 50)
    print("  Study Runner is running")
    print(f"  Admin page:  {scheme}://localhost:{port}/admin")
    print(f"  Open on tablet: {scheme}://{local_ips[0]}:{port}")
    if len(local_ips) > 1:
        print(f"  Other local addresses: {', '.join(local_ips[1:])}")
    print(f"  Data folder: {app.config['DATA_DIR']}")
    if ssl_context:
        print("  HTTPS enabled for browser camera access.")
    print("-" * 50 + "\n")

    app.run(host=host, port=port, debug=is_debug_enabled(), ssl_context=ssl_context)
