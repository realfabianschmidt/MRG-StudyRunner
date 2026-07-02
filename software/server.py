"""Development entrypoint for running Study Runner from the software folder.

Keep using:

    python server.py
"""

import sys

from study_runner.app_server import app, get_local_ip, get_ssl_context, is_debug_enabled, run_app


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--apply-update":
        from study_runner.update_helper import main as run_update_helper

        raise SystemExit(run_update_helper(sys.argv[2:]))
    run_app()
