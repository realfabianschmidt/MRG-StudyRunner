"""Development entrypoint for running Study Runner from the software folder.

Keep using:

    python server.py
"""

import multiprocessing
import sys

if __name__ != "__main__":
    from study_runner.app_server import app, get_local_ip, get_ssl_context, is_debug_enabled, run_app


if __name__ == "__main__":
    # Required in frozen (PyInstaller) builds: without it, any library that
    # spawns a child process would re-run this entrypoint recursively.
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--emotion-worker":
        from study_runner.plugins.camera_emotion.worker.server import main as run_emotion_worker

        raise SystemExit(run_emotion_worker(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "--emotion-worker-self-test":
        from study_runner.plugins.camera_emotion.worker.server import self_test_main

        raise SystemExit(self_test_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "--brainbit-cli":
        # Packaged builds have no separate Python interpreter to run the BrainBit
        # CLI script with, so the frozen executable re-invokes itself instead.
        from study_runner.plugins.brainbit.brainbit_realtime_cli import main as run_brainbit_cli

        raise SystemExit(run_brainbit_cli(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "--recording-worker":
        from study_runner.recording_worker.application import main as run_recording_worker

        raise SystemExit(run_recording_worker(sys.argv[2:]))
    if len(sys.argv) > 2 and sys.argv[1] == "--recording-worker-probe":
        import json
        from pathlib import Path

        from study_runner.recording_worker.core import probe_core_library

        probe = probe_core_library(Path(sys.argv[2]))
        print(json.dumps(probe.as_dict(), ensure_ascii=False, sort_keys=True))
        raise SystemExit(0 if probe.usable else 2)
    if len(sys.argv) > 1 and sys.argv[1] == "--apply-update":
        from study_runner.update_helper import main as run_update_helper

        raise SystemExit(run_update_helper(sys.argv[2:]))
    from study_runner.app_server import run_app

    run_app()
