"""Development entrypoint for running Study Runner from the software folder.

Keep using:

    python server.py
"""

from study_runner.app_server import app, get_local_ip, get_ssl_context, is_debug_enabled, run_app


if __name__ == "__main__":
    run_app()
