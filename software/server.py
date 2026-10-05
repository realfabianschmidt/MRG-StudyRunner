"""Development entrypoint for running Study Runner from the software folder.

Keep using:

    python server.py
"""

import multiprocessing
import sys

if __name__ != "__main__":
    from study_runner.app_server import app, get_local_ip, get_ssl_context, is_debug_enabled, run_app


def _run_worker_mode(mode: str, action):
    """Run one CLI worker-mode branch and never let it crash silently.

    Without this, an uncaught exception inside a worker mode (e.g. the
    emotion worker failing to import a native dependency) propagates as a
    bare traceback with no indication of which mode failed. ``SystemExit``
    and ``KeyboardInterrupt`` are not ``Exception`` subclasses, so a worker's
    own deliberate ``sys.exit(n)`` still passes through unchanged.
    """
    try:
        return action()
    except Exception as error:
        print(f"\nFATAL: {mode} crashed: {error}\n", file=sys.stderr)
        return 2


if __name__ == "__main__":
    # Required in frozen (PyInstaller) builds: without it, any library that
    # spawns a child process would re-run this entrypoint recursively.
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--emotion-worker":
        from study_runner.plugins.sensors.camera_emotion.worker.server import main as run_emotion_worker

        raise SystemExit(_run_worker_mode("--emotion-worker", lambda: run_emotion_worker(sys.argv[2:])))
    if len(sys.argv) > 1 and sys.argv[1] == "--emotion-worker-self-test":
        from study_runner.plugins.sensors.camera_emotion.worker.server import self_test_main

        raise SystemExit(_run_worker_mode("--emotion-worker-self-test", lambda: self_test_main(sys.argv[2:])))
    if len(sys.argv) > 1 and sys.argv[1] == "--brainbit-cli":
        # Packaged builds have no separate Python interpreter to run the BrainBit
        # CLI script with, so the frozen executable re-invokes itself instead.
        from study_runner.plugins.sensors.brainbit.brainbit_realtime_cli import main as run_brainbit_cli

        raise SystemExit(_run_worker_mode("--brainbit-cli", lambda: run_brainbit_cli(sys.argv[2:])))
    if len(sys.argv) > 2 and sys.argv[1] == "--plugin-driver":
        from study_runner.plugin_framework.driver_runtime import run_plugin_driver

        raise SystemExit(_run_worker_mode("--plugin-driver", lambda: run_plugin_driver(sys.argv[2])))
    if len(sys.argv) > 1 and sys.argv[1] == "--recording-worker":
        from study_runner.data_core.worker.application import main as run_recording_worker

        raise SystemExit(_run_worker_mode("--recording-worker", lambda: run_recording_worker(sys.argv[2:])))
    if len(sys.argv) > 2 and sys.argv[1] == "--recording-worker-probe":
        import json
        from pathlib import Path

        from study_runner.data_core.worker.core import probe_core_library

        def _run_probe():
            probe = probe_core_library(Path(sys.argv[2]))
            print(json.dumps(probe.as_dict(), ensure_ascii=False, sort_keys=True))
            return 0 if probe.usable else 2

        raise SystemExit(_run_worker_mode("--recording-worker-probe", _run_probe))
    if len(sys.argv) > 1 and sys.argv[1] == "--apply-update":
        from study_runner.updates.installer import main as run_installer

        raise SystemExit(_run_worker_mode("--apply-update", lambda: run_installer(sys.argv[2:])))
    if len(sys.argv) > 3 and sys.argv[1] == "--restart-when-stopped":
        from pathlib import Path

        from study_runner.updates.installer import restart_when_stopped

        raise SystemExit(
            _run_worker_mode(
                "--restart-when-stopped",
                lambda: restart_when_stopped(
                    Path(sys.argv[2]), sys.argv[3], Path(sys.argv[2]) / ".tools" / "restart.log"
                ),
            )
        )
    if len(sys.argv) > 1 and sys.argv[1] == "--self-check":
        from study_runner.self_check import main as run_self_check

        raise SystemExit(_run_worker_mode("--self-check", run_self_check))
    # Before the app is imported: importing it creates the app in the data folder.
    from study_runner.runtime_core.settings.data_folder import DataFolderError, apply_data_folder_setting

    try:
        apply_data_folder_setting()
    except DataFolderError as error:
        print("\n" + "!" * 60 + f"\n{error}\n" + "!" * 60 + "\n")
        raise SystemExit(1)
    from study_runner.app_server import run_app

    run_app()
