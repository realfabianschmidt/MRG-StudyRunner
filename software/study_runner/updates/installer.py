from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time
from typing import Any

from study_runner.updates import archive_update

SERVER_EXIT_TIMEOUT_SECONDS = 60
INSTALL_SCRIPT_TIMEOUT_SECONDS = 1800


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        return 2

    state_file = Path(args[0]).expanduser().resolve()
    log_file = state_file.parent / "update-helper.log"

    try:
        state = _read_json(state_file)
        staged = state.get("staged") if isinstance(state, dict) else None
        if not isinstance(staged, dict):
            raise RuntimeError("No staged update is recorded.")

        if staged.get("mode") == "archive":
            _apply_archive_update(state, staged, log_file)
        elif staged.get("mode") == "source":
            _restart_source_checkout(state, log_file)
        else:
            _restart_packaged_build(state, staged, log_file)

        state["state"] = "applied"
        state["applied_at"] = _utc_now()
        _write_json(state_file, state)
        return 0
    except Exception as error:
        _append_log(log_file, f"Update helper failed: {error}")
        try:
            state = _read_json(state_file)
            if isinstance(state, dict):
                state["state"] = "install_failed"
                state["error"] = str(error)
                _write_json(state_file, state)
        except Exception:
            pass
        return 1


def _restart_packaged_build(state: dict[str, Any], staged: dict[str, Any], log_file: Path) -> None:
    executable = Path(str(staged.get("executable") or "")).expanduser().resolve()
    if not executable.exists():
        raise RuntimeError(f"Staged executable not found: {executable}")

    helper = state.get("helper") if isinstance(state.get("helper"), dict) else {}
    env = os.environ.copy()
    storage_root = str(helper.get("storage_root") or "").strip()
    if storage_root:
        env["STUDY_RUNNER_DATA_DIR"] = storage_root
    env["STUDY_RUNNER_APP_MODE"] = "packaged"

    for key in ("STUDY_RUNNER_HOST", "STUDY_RUNNER_PORT", "STUDY_RUNNER_HTTPS"):
        value = str(helper.get(key.lower().replace("study_runner_", "")) or env.get(key) or "").strip()
        if value:
            env[key] = value

    time.sleep(1.4)
    _spawn_detached([str(executable)], executable.parent, env)
    _append_log(log_file, f"Launched staged update: {executable}")


def _restart_source_checkout(state: dict[str, Any], log_file: Path) -> None:
    # The git checkout was already updated in place (git pull + install
    # script, in update_service.py's _apply_source_update); restarting only
    # means starting the server again once the old one has exited.
    restart = state.get("source_restart") if isinstance(state.get("source_restart"), dict) else {}
    install_root = Path(str(restart.get("install_root") or Path(str(restart.get("base_dir") or ".")).parent)).resolve()
    _wait_for_server_exit(restart, log_file)
    _start_visible(install_root, log_file)


def _apply_archive_update(state: dict[str, Any], staged: dict[str, Any], log_file: Path) -> None:
    """Swap program files, install, and restart -- or roll back and restart the old version."""
    restart = state.get("source_restart") if isinstance(state.get("source_restart"), dict) else {}
    install_root = Path(str(restart.get("install_root") or "")).resolve()
    release_root = Path(str(staged.get("release_root") or "")).resolve()
    if not (install_root / "software").is_dir() or not (release_root / "software" / "server.py").is_file():
        raise RuntimeError("The staged update or the installation folder is missing.")
    _wait_for_server_exit(restart, log_file)

    previous = archive_update.read_installed_version(install_root) or "previous"
    backup_root = install_root / ".tools" / "update-backup" / f"{previous}-{archive_update.timestamp()}"
    journal = archive_update.swap_program_files(install_root, release_root, backup_root)
    _append_log(log_file, f"Replaced program files; old version kept in {backup_root}")
    try:
        added = archive_update.merge_new_content(release_root, install_root)
        if added:
            _append_log(log_file, f"Added shipped content: {', '.join(added)}")
        _run_install_script(install_root, log_file)
    except Exception as error:
        _append_log(log_file, f"Install of the new version failed, restoring {previous}: {error}")
        archive_update.rollback(install_root, backup_root, journal)
        _start_visible(install_root, log_file)
        raise RuntimeError(f"The update could not be installed and was undone: {error}") from error
    _start_visible(install_root, log_file)


def _run_install_script(install_root: Path, log_file: Path) -> None:
    if os.name == "nt":
        command = [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe"), "/d", "/c",
                   str(install_root / "tools" / "install-windows.cmd")]
    else:
        command = ["/bin/bash", str(install_root / "tools" / "install-macos.sh")]
    with log_file.open("a", encoding="utf-8") as output:
        result = subprocess.run(
            command, cwd=str(install_root), stdout=output, stderr=subprocess.STDOUT,
            timeout=INSTALL_SCRIPT_TIMEOUT_SECONDS, env=_clean_env(),
        )
    if result.returncode != 0:
        raise RuntimeError(f"install script exited with code {result.returncode}; see {log_file}")


def _wait_for_server_exit(restart: dict[str, Any], log_file: Path) -> None:
    """Wait until the old server no longer answers on its port."""
    port = int(str(restart.get("port") or "0") or 0)
    deadline = time.monotonic() + SERVER_EXIT_TIMEOUT_SECONDS
    while port and time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                pass
        except OSError:
            break
        time.sleep(0.5)
    else:
        if port:
            _append_log(log_file, "The old server did not stop in time; continuing anyway.")
    time.sleep(1.5)  # let the process release its files


def _start_visible(install_root: Path, log_file: Path) -> None:
    """Start the server in a new visible window, so it can be stopped with Ctrl+C again."""
    env = _clean_env()
    if os.name == "nt":
        cmd_exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
        subprocess.Popen(
            [cmd_exe, "/k", str(install_root / "tools" / "start-windows.cmd")],
            cwd=str(install_root), env=env,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
    elif sys.platform == "darwin":
        script = f"cd {shlex.quote(str(install_root))} && bash tools/start-macos.sh"
        apple_script = 'tell application "Terminal" to do script ' + json.dumps(script)
        subprocess.Popen(["osascript", "-e", apple_script, "-e", 'tell application "Terminal" to activate'], env=env)
    else:
        _spawn_detached([sys.executable, str(install_root / "software" / "server.py")], install_root / "software", env)
    _append_log(log_file, f"Started Study Runner again from {install_root}")


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("WERKZEUG_RUN_MAIN", "WERKZEUG_SERVER_FD", "VIRTUAL_ENV", "PYTHONHOME"):
        env.pop(key, None)
    return env


def _spawn_detached(cmd: list[str], cwd: Path, env: dict[str, str]) -> None:
    kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as file_handle:
        return json.load(file_handle)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_handle:
        json.dump(payload, file_handle, indent=2, sort_keys=True)
        file_handle.write("\n")


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file_handle:
        file_handle.write(f"{_utc_now()} {message}\n")


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


if __name__ == "__main__":
    raise SystemExit(main())
