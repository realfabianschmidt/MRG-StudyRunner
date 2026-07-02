from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


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
        state["state"] = "applied"
        state["applied_at"] = _utc_now()
        _write_json(state_file, state)
        _append_log(log_file, f"Launched staged update: {executable}")
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
