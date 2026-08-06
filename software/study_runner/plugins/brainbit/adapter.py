"""
BrainBit adapter - launches a repo-local BrainBit CLI process and optionally mirrors its output to LSL.

Expected setup inside this repository:
  - BrainBit Python CLI script in the project folder, for example:
      study_runner/plugins/brainbit/brainbit_realtime_cli.py
  - TouchDesigner project listening for OSC on the configured port, for example:
      study_runner/plugins/brainbit/HelloEEG_HelloMYO_01.3.toe

The BrainBit CLI itself is responsible for Bluetooth scanning and SDK usage. This adapter keeps
Study Runner in charge of:
  - starting the external process at server startup
  - stopping it cleanly on server shutdown
  - relaying selected numeric outputs into optional LSL streams for LabRecorder
  - forwarding selected BrainBit values to TouchDesigner based on the active stimulus card
  - keeping the main server terminal quiet by writing details to log/state files
"""
from __future__ import annotations

import atexit
from collections import deque
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from study_runner.plugin_framework.history_buffer import history_maxlen, max_gap_seconds, samples_in_interval, truncation_info

from study_runner.plugin_framework.dependency_utils import ensure_requirements
from .brainbit_realtime_cli import (
    EXIT_BLE_UNAVAILABLE,
    EXIT_MISSING_DEPENDENCY,
    EXIT_NO_DEVICE_FOUND,
)


LSL_SOURCE_IDS = {
    "eeg": "study_runner.brainbit.eeg",
    "bands": "study_runner.brainbit.bands",
    "mental": "study_runner.brainbit.mental",
    "quality": "study_runner.brainbit.quality",
    "battery": "study_runner.brainbit.battery",
}
LSL_CHANNEL_UNITS = {
    "eeg": ("microvolt",) * 4,
    "bands": ("relative_power",) * 5,
    "mental": ("ratio",) * 4,
    "quality": ("ohm",) * 4,
    "battery": ("percent",),
}


# How the CLI's exit codes translate for the operator. "retry" means a restart
# can plausibly help (device switched on late, transient crash); the others need
# a human, so retrying would only hide the real message.
_EXIT_REASONS: dict[int, dict[str, Any]] = {
    EXIT_MISSING_DEPENDENCY: {
        "detail_key": "brainbit.error.missingDependency",
        "message": "The BrainBit software libraries are missing and could not be installed automatically.",
        "retry": False,
    },
    EXIT_NO_DEVICE_FOUND: {
        "detail_key": "brainbit.error.deviceNotFound",
        "message": "No BrainBit headset was found. Switch it on, keep it close, then start BrainBit again.",
        "retry": True,
    },
    EXIT_BLE_UNAVAILABLE: {
        "detail_key": "brainbit.error.bluetoothUnavailable",
        "message": "Bluetooth is switched off or unavailable on this computer.",
        "retry": False,
    },
}
_CRASH_REASON: dict[str, Any] = {
    "detail_key": "brainbit.error.crashed",
    "message": "The BrainBit connection stopped unexpectedly.",
    "retry": True,
}

_lock = threading.Lock()
_state_lock = threading.Lock()
_routing_lock = threading.Lock()
_process: subprocess.Popen[str] | None = None
_reader_thread: threading.Thread | None = None
_watchdog_thread: threading.Thread | None = None
_registered_shutdown = False
_config: dict[str, Any] = {}
_lsl_outlets: dict[str, Any] = {}
_td_client: Any = None
_latest_state: dict[str, Any] = {}
_last_state_write = 0.0
_last_state_write_error_at = 0.0
_last_activity_at = 0.0
_last_any_line_at = 0.0
_last_sensor_activity_at = 0.0
_last_eeg_at = 0.0
_last_quality_at = 0.0
_last_derived_at = 0.0
_log_handle: Any = None
_routing_state = {
    "forward_to_lsl": False,
    "forward_to_touchdesigner": False,
}
_auto_restart_count = 0
_last_auto_restart_at = 0.0
# Set by start()/stop() so the watchdog knows whether an exited process should
# be revived or was stopped on purpose.
_desired_running = False
_last_exit_code: int | None = None
_last_exit_at = 0.0
# The CLI emits a handful of JSON lines per second; sized for a full session.
_history: deque[dict[str, Any]] = deque(maxlen=history_maxlen(10.0))
_SENSOR_ACTIVITY_TAGS = {
    "RESIST",
    "QUALITY",
    "CALIB",
    "ARTIFACT",
    "BATTERY",
    "EEG",
    "BANDS",
    "MENTAL",
    "STATE",
}
_IDENTITY_TAGS = {"DEVICE", "DEVICE_SELECTED"}
_HISTORY_TAGS = {"BANDS", "MENTAL", "QUALITY", "BATTERY"}
_DERIVED_TAGS = {"BANDS", "MENTAL"}


def _default_python_executable(python_executable: str | None) -> str:
    """Return the interpreter used to run the CLI script, or "" in packaged builds.

    Packaged builds have no interpreter to hand and do not need one: they
    re-invoke their own executable through the --brainbit-cli entrypoint.
    """
    if python_executable:
        return python_executable

    from study_runner.backend.services.settings.runtime_config import get_app_mode, is_frozen

    if is_frozen() and get_app_mode() in {"desktop", "packaged"}:
        return ""

    return sys.executable


def _uses_frozen_self_dispatch() -> bool:
    """True when the CLI must be started as `<own exe> --brainbit-cli ...`."""
    from study_runner.backend.services.settings.runtime_config import is_frozen

    return not _config.get("python_executable") and is_frozen()


def _build_cli_command() -> list[str] | None:
    """Build the CLI command line, or None when no way to launch it exists.

    Two launch modes:
      - source checkout: `<python> <script.py> <args>`
      - packaged build:  `<own executable> --brainbit-cli <args>`
    """
    if _uses_frozen_self_dispatch():
        launcher = [sys.executable, "--brainbit-cli"]
    elif _config.get("python_executable"):
        launcher = [_config["python_executable"], _config["script_path"]]
    else:
        return None

    command = [
        *launcher,
        "--no-osc",
        "--scan-seconds",
        str(_config.get("scan_seconds", 5)),
        "--device-index",
        str(_config["device_index"] if _config.get("device_index") is not None else 0),
        "--resist-seconds",
        str(_config.get("resist_seconds", 6)),
        "--signal-seconds",
        str(_config.get("signal_seconds", 0)),
    ]
    if _config.get("serial_number"):
        command.extend(["--serial-number", str(_config["serial_number"])])
    if _config.get("device_address"):
        command.extend(["--device-address", str(_config["device_address"])])
    if _config.get("device_name"):
        command.extend(["--device-name", str(_config["device_name"])])
    if _config.get("pretty"):
        command.append("--pretty")
    if _config.get("debug"):
        command.append("--debug")
    return command


# ============================================================
#  1. LIFECYCLE - configure, start/stop/restart the external CLI
# ============================================================
def initialize(
    *,
    script_path: str,
    working_dir: str | None = None,
    python_executable: str | None = None,
    osc_host: str = "127.0.0.1",
    osc_port: int = 8000,
    scan_seconds: int = 5,
    device_index: int | None = 0,
    device_address: str | None = None,
    serial_number: str | None = None,
    device_name: str | None = None,
    resist_seconds: int = 6,
    signal_seconds: int = 0,
    pretty: bool = False,
    debug: bool = False,
    lsl_enabled: bool = False,
    lsl_auto_install: bool = True,
    lsl_stream_prefix: str = "BrainBit",
    quiet_output: bool = True,
    monitor_refresh_ms: int = 1000,
    disconnect_timeout_ms: int = 20000,
    log_dir: str | None = None,
) -> None:
    """Store BrainBit settings, prepare optional LSL mirrors, and start the external CLI."""
    global _registered_shutdown, _config

    script_file = Path(script_path).expanduser()
    if not script_file.exists() and script_file.name == "brainbit_realtime_cli_OSC_15.py":
        # Saved hardware settings from releases before the 0.4 rename.
        renamed = script_file.with_name("brainbit_realtime_cli.py")
        if renamed.exists():
            script_file = renamed

    from study_runner.backend.services.settings.runtime_config import is_frozen

    resolved_python = _default_python_executable(python_executable)
    # Packaged builds run the CLI through their own executable, so the script
    # file is not on disk there and its absence is not an error.
    self_dispatch = not resolved_python and is_frozen()
    if not script_file.exists() and not self_dispatch:
        print(f"[BrainBit] Script not found: {script_file}")
        return

    resolved_working_dir = Path(working_dir).expanduser() if working_dir else script_file.parent
    resolved_log_dir = Path(log_dir).expanduser() if log_dir else resolved_working_dir / "logs"
    try:
        resolved_log_dir.mkdir(parents=True, exist_ok=True)
        resolved_working_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        message = f"BrainBit cannot write to its log folder ({resolved_log_dir}): {error}"
        print(f"[BrainBit] {message}")
        _set_state(
            {
                "status": "failed",
                "status_detail_key": "brainbit.error.logFolderUnwritable",
                "last_message": message,
            },
            force=True,
        )
        return

    _config = {
        "script_path": str(script_file),
        "working_dir": str(resolved_working_dir),
        "python_executable": resolved_python,
        "osc_host": osc_host,
        "osc_port": int(osc_port),
        "scan_seconds": int(scan_seconds),
        "device_index": device_index,
        "device_address": str(device_address or "").strip(),
        "serial_number": str(serial_number or "").strip(),
        "device_name": str(device_name or "").strip(),
        "resist_seconds": int(resist_seconds),
        "signal_seconds": int(signal_seconds),
        "pretty": bool(pretty),
        "debug": bool(debug),
        "lsl_enabled": bool(lsl_enabled),
        "lsl_auto_install": bool(lsl_auto_install),
        "lsl_stream_prefix": lsl_stream_prefix,
        "quiet_output": bool(quiet_output),
        "monitor_refresh_ms": max(250, int(monitor_refresh_ms)),
        "disconnect_timeout_ms": max(1000, int(disconnect_timeout_ms)),
        "log_dir": str(resolved_log_dir),
        "raw_log_path": str(resolved_log_dir / "brainbit_runtime.log"),
        "state_path": str(resolved_log_dir / "brainbit_state.json"),
    }
    with _routing_lock:
        _routing_state["forward_to_lsl"] = bool(lsl_enabled)
        _routing_state["forward_to_touchdesigner"] = False

    _set_state(
        {
            "status": "configured",
            "script_path": _config["script_path"],
            "working_dir": _config["working_dir"],
            "osc_target": f"{_config['osc_host']}:{_config['osc_port']}",
            "raw_log_path": _config["raw_log_path"],
            "state_path": _config["state_path"],
            "target_device": _target_device_from_config(),
            "last_message": "BrainBit adapter configured.",
        },
        force=True,
    )

    _initialize_touchdesigner_client()

    if _config["lsl_enabled"]:
        _initialize_lsl_outlets()

    if not _registered_shutdown:
        atexit.register(stop)
        _registered_shutdown = True

    start()


def start() -> None:
    """Start the repo-local BrainBit process if it is not already running."""
    global _process, _reader_thread, _log_handle, _watchdog_thread, _desired_running
    global _last_activity_at, _last_any_line_at, _last_sensor_activity_at
    global _last_eeg_at, _last_quality_at, _last_derived_at

    if not _config:
        print("[BrainBit] Adapter not configured.")
        return

    with _lock:
        if _process is not None and _process.poll() is None:
            return

        command = _build_cli_command()
        if command is None:
            message = (
                "BrainBit needs a Python interpreter path on this installation. "
                "Set brainbit.python_executable in hardware settings."
            )
            print(f"[BrainBit] {message}")
            _set_state(
                {
                    "status": "not_configured",
                    "status_detail_key": "brainbit.error.noInterpreter",
                    "last_message": message,
                },
                force=True,
            )
            return
        _desired_running = True

        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        try:
            _log_handle = Path(_config["raw_log_path"]).open("a", encoding="utf-8")
            _process = subprocess.Popen(
                command,
                cwd=_config["working_dir"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as error:
            print(f"[BrainBit] Could not start external process: {error}")
            _process = None
            _close_log_handle()
            _set_state({"status": "failed", "last_message": str(error)}, force=True)
            return

        _set_state(
            {
                "status": "scanning",
                "pid": _process.pid,
                "scan_timeout_seconds": int(_config.get("scan_seconds", 5)),
                "last_scan_started_at": _timestamp(),
                "last_scan_finished_at": None,
                "next_retry_at": None,
                "device": None,
                "selected_device": None,
                "scan_candidates": [],
                "target_device": _target_device_from_config(),
                "last_message": f"Scanning for BrainBit for {_config.get('scan_seconds', 5)} seconds.",
            },
            force=True,
        )
        now = time.time()
        _last_activity_at = now
        _last_any_line_at = now
        _last_sensor_activity_at = 0.0
        _last_eeg_at = 0.0
        _last_quality_at = 0.0
        _last_derived_at = 0.0
        _reader_thread = threading.Thread(target=_read_output, args=(_process,), daemon=True)
        _reader_thread.start()
        if _watchdog_thread is None or not _watchdog_thread.is_alive():
            _watchdog_thread = threading.Thread(target=_watch_connection_health, daemon=True)
            _watchdog_thread.start()
        launch_label = "--brainbit-cli" if _uses_frozen_self_dispatch() else Path(_config["script_path"]).name
        print(
            "[BrainBit] External CLI started "
            f"({launch_label} -> OSC {_config['osc_host']}:{_config['osc_port']})"
        )
        print(f"[BrainBit] State file: {_config['state_path']}")
        print(f"[BrainBit] Raw log: {_config['raw_log_path']}")


def stop() -> None:
    """Stop the repo-local BrainBit process if it is running."""
    global _process, _desired_running

    _desired_running = False
    with _lock:
        process = _process
        if process is None or process.poll() is not None:
            _process = None
            _set_state({"status": "stopped", "last_message": "BrainBit CLI already stopped."}, force=True)
            _close_log_handle()
            return

    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGINT)
        process.wait(timeout=5)
    except Exception:
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
    finally:
        with _lock:
            if _process is process:
                _process = None
        _set_state({"status": "stopped", "last_message": "BrainBit CLI stopped."}, force=True)
        _close_log_handle()
        print("[BrainBit] External CLI stopped.")



def restart() -> None:
    """Restart the repo-local BrainBit process using the current configuration."""
    stop()
    start()


def is_configured() -> bool:
    """Return True after initialize() stored a usable adapter configuration."""
    return bool(_config)


# ============================================================
#  2. STATUS - what the dashboard shows
# ============================================================
def get_status() -> dict[str, Any]:
    """Return the current process/state-file status in the common plugin shape."""
    with _state_lock:
        latest = dict(_latest_state)

    with _lock:
        running = _process is not None and _process.poll() is None
        pid = _process.pid if running and _process is not None else latest.get("pid")

    contact_state, contact_channels = _derive_contact_quality(latest.get("quality"))
    latest.setdefault("contact_quality_state", contact_state)
    latest.setdefault("contact_quality_channels", contact_channels)

    status_value = _derive_status(latest, running)
    seconds_since = _seconds_since_values(latest)
    health = _build_health(latest, running, contact_state)
    return {
        **latest,
        "latest": latest,
        "enabled": bool(_config),
        "runtime_enabled": running,
        "status": status_value,
        "pid": pid,
        "lsl_enabled": bool(_config.get("lsl_enabled", False)),
        "recording_enabled": bool(_config.get("lsl_enabled", False) and _lsl_outlets),
        "touchdesigner_forwarding_enabled": bool(_routing_state.get("forward_to_touchdesigner", False)),
        "scan_timeout_seconds": int(_config.get("scan_seconds", 5)) if _config else None,
        "last_scan_started_at": latest.get("last_scan_started_at"),
        "last_scan_finished_at": latest.get("last_scan_finished_at"),
        "next_retry_at": None,
        "state_file": _config.get("state_path") if _config else None,
        "raw_log_path": _config.get("raw_log_path") if _config else None,
        "last_activity_at": latest.get("last_activity_at"),
        **seconds_since,
        "contact_quality_state": contact_state,
        "contact_quality_channels": contact_channels,
        "scan_candidates": latest.get("scan_candidates") or [],
        "selected_device": latest.get("selected_device") or latest.get("device"),
        "target_device": latest.get("target_device") or _target_device_from_config(),
        "health": health,
        "last_message": latest.get("last_message", "BrainBit adapter is not configured."),
    }


# ============================================================
#  3. CLI OUTPUT - read and parse the JSON line stream
# ============================================================
def _read_output(process: subprocess.Popen[str]) -> None:
    try:
        if process.stdout is None:
            return
        for raw_line in process.stdout:
            line = raw_line.rstrip()
            if not line:
                continue
            _append_raw_log(line)
            important = _update_state_from_line(line)
            _forward_line_to_touchdesigner(line)
            _mirror_line_to_lsl(line)
            if not _config.get("quiet_output", True) or important:
                print(f"[BrainBit] {line}")
    finally:
        exit_code = process.poll()
        with _lock:
            global _process, _last_exit_code, _last_exit_at
            if _process is process:
                _process = None
            _last_exit_code = exit_code
            _last_exit_at = time.time()
        with _state_lock:
            previous_status = _latest_state.get("status")
            previous_message = _latest_state.get("last_message")

        reason = _exit_reason(exit_code)
        if reason is not None:
            final_status = "failed"
            final_message = reason["message"]
            detail_key = reason["detail_key"]
        elif previous_status == "failed":
            final_status = "failed"
            final_message = previous_message
            detail_key = None
        else:
            final_status = "exited"
            final_message = f"BrainBit CLI exited with code {exit_code}."
            detail_key = None

        update: dict[str, Any] = {
            "status": final_status,
            "exit_code": exit_code,
            "last_scan_finished_at": _timestamp(),
            "last_message": final_message,
        }
        if detail_key:
            update["status_detail_key"] = detail_key
        _set_state(update, force=True)
        _close_log_handle()
        print(f"[BrainBit] External CLI exited with code {exit_code}. {final_message or ''}".rstrip())


def _exit_reason(exit_code: int | None) -> dict[str, Any] | None:
    """Translate a CLI exit code into an operator-facing reason, or None if clean."""
    if exit_code is None or exit_code == 0:
        return None
    return _EXIT_REASONS.get(exit_code, _CRASH_REASON)


def _update_state_from_line(line: str) -> bool:
    global _last_activity_at, _last_any_line_at, _last_sensor_activity_at
    global _last_eeg_at, _last_quality_at, _last_derived_at

    important = False
    now = time.time()
    now_text = _timestamp(now)
    _last_any_line_at = now
    state_update: dict[str, Any] = {
        "updated_at": now_text,
        "last_line": line,
        "last_any_line_at": now_text,
        "last_any_line_epoch": now,
    }

    parts = line.split(" ", 1)
    if len(parts) == 2 and parts[1].startswith("{"):
        tag, payload_text = parts
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            if tag in _SENSOR_ACTIVITY_TAGS:
                _last_activity_at = now
                _last_sensor_activity_at = now
                state_update["last_activity_at"] = now_text
                state_update["last_sensor_activity_at"] = now_text
                state_update["last_sensor_activity_epoch"] = now
                state_update["status"] = "connected"
            if tag in _HISTORY_TAGS:
                _history.append({"tag": tag, "payload": dict(payload), "server_received_at": now_text, "_epoch": now})
            if tag == "SCAN":
                state_update["scan_candidates"] = _merge_scan_candidate(payload)
                state_update["last_scan"] = payload
                state_update["status"] = "scanning"
            elif tag in _IDENTITY_TAGS:
                state_update["device"] = payload
                state_update["selected_device"] = payload
                state_update["last_message"] = "BrainBit device selected. Waiting for live sensor data."
                important = True
            elif tag == "DEVICE_TARGET_MISSING":
                # Not a failure: the CLI falls back to the headset that is
                # actually in range. Say which one, so the operator can tell.
                state_update["selection_error"] = payload
                state_update["status_detail_key"] = "brainbit.error.targetMissingFallback"
                state_update["last_message"] = (
                    "The BrainBit saved in the settings was not found. Using the headset that is in range instead."
                )
                important = True
            elif tag in {"NO_DEVICE_FOUND", "SETUP_FAIL", "BLE_UNAVAILABLE"}:
                # The CLI exits right after these; the exit-code handler in
                # _read_output writes the operator-facing message.
                state_update["selection_error"] = payload
                important = True
            elif tag == "BATTERY":
                state_update["battery"] = payload
            elif tag == "RESIST":
                state_update["resist"] = payload
                if any(payload.get(channel) is None for channel in ("O1", "O2", "T3", "T4")):
                    state_update["status"] = "poor_contact"
                    state_update["last_message"] = "BrainBit electrode values are missing. Adjust band and electrodes."
                    important = True
            elif tag == "QUALITY":
                _last_quality_at = now
                state_update["quality"] = payload
                state_update["last_quality_at"] = now_text
                state_update["last_quality_epoch"] = now
                contact_state, contact_channels = _derive_contact_quality(payload)
                state_update["contact_quality_state"] = contact_state
                state_update["contact_quality_channels"] = contact_channels
                if contact_state == "poor":
                    state_update["status"] = "poor_contact"
                    state_update["last_message"] = "BrainBit is receiving data, but electrode contact is poor."
                    important = True
                elif contact_state == "mixed":
                    state_update["last_message"] = "BrainBit electrode contact is mixed. Adjust the band before recording."
            elif tag == "EEG":
                _last_eeg_at = now
                state_update["eeg"] = payload
                state_update["last_eeg_at"] = now_text
                state_update["last_eeg_epoch"] = now
                if _last_derived_at <= 0:
                    state_update["status"] = "warming_up"
                    state_update["last_message"] = "BrainBit EEG is arriving; waiting for calibration and derived metrics."
            elif tag in _DERIVED_TAGS:
                _last_derived_at = now
                state_update["last_derived_at"] = now_text
                state_update["last_derived_epoch"] = now
                state_update["status"] = "connected"
                state_update["last_message"] = "BrainBit derived metrics are available."
                if tag == "BANDS":
                    state_update["bands"] = payload
                else:
                    state_update["mental"] = payload
            elif tag == "STATE":
                state_update["sensor_state"] = payload
                important = True
            elif tag == "CALIB":
                state_update["calibration"] = payload
                if payload.get("event"):
                    state_update["last_message"] = f"Calibration: {payload['event']}"
                    important = True
                if payload.get("event") == "START" or "progress_percent" in payload:
                    state_update["status"] = "calibrating"
                elif payload.get("event") in {"FINISHED", "FORCED_FINISH"}:
                    state_update["status"] = "warming_up"
            elif tag == "ARTIFACT":
                state_update["artifact"] = payload
                if payload.get("both_now") or payload.get("sequence"):
                    state_update["last_message"] = "BrainBit artifact detected. Reduce movement and check contact."
                    important = True
            elif tag == "EMO_INIT_FAIL":
                state_update["status"] = "failed"
                state_update["last_message"] = payload.get("error", "EmotionalMath init failed.")
                important = True
    elif line.startswith("[WARN]") or line.startswith("# ERROR") or line.startswith("# FATAL"):
        state_update["last_message"] = line
        if "No valid EEG frames" in line or "Missing electrode" in line:
            state_update["status"] = "poor_contact"
        important = True
    elif line.startswith("# ") or line.startswith("[STATUS]"):
        state_update["last_message"] = line

    _set_state(state_update, force=important)
    return important


def _set_state(values: dict[str, Any], *, force: bool = False) -> None:
    global _latest_state, _last_state_write, _last_state_write_error_at

    with _state_lock:
        _latest_state.update(values)
        _latest_state["updated_at"] = _timestamp()
        refresh_seconds = max(0.25, _config.get("monitor_refresh_ms", 1000) / 1000.0) if _config else 1.0
        now = time.time()
        if not force and (now - _last_state_write) < refresh_seconds:
            return

        state_path = _config.get("state_path")
        if not state_path:
            return

        path = Path(state_path)
        temp_path = path.with_suffix(f"{path.suffix}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            temp_path.write_text(
                json.dumps(_latest_state, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(path)
            _last_state_write = now
        except OSError as error:
            _last_state_write = now
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            if (now - _last_state_write_error_at) >= 10.0:
                _last_state_write_error_at = now
                print(f"[BrainBit] State file write skipped because the file is locked: {error}")


def _append_raw_log(line: str) -> None:
    if _log_handle is None:
        return
    try:
        _log_handle.write(line + "\n")
        _log_handle.flush()
    except Exception:
        pass


def _close_log_handle() -> None:
    global _log_handle
    if _log_handle is None:
        return
    try:
        _log_handle.close()
    except Exception:
        pass
    _log_handle = None


# ============================================================
#  4. FORWARDING - LSL / TouchDesigner mirrors
# ============================================================
def _mirror_line_to_lsl(line: str) -> None:
    if not _lsl_outlets:
        return

    parsed = _parse_json_line(line)
    if parsed is None:
        return

    tag, payload = parsed

    if tag == "EEG":
        _push_sample("EEG", payload, ("O1", "O2", "T3", "T4"))
    elif tag == "BANDS":
        _push_sample("BANDS", payload, ("delta", "theta", "alpha", "beta", "gamma"))
    elif tag == "MENTAL":
        _push_sample(
            "MENTAL",
            payload,
            ("Inst_Attention", "Inst_Relaxation", "Rel_Attention", "Rel_Relaxation"),
        )
    elif tag == "QUALITY":
        _push_sample("QUALITY", payload, ("O1", "O2", "T3", "T4"))
    elif tag == "BATTERY":
        _push_sample("BATTERY", payload, ("percent",))


def _push_sample(stream_key: str, payload: dict[str, Any], fields: tuple[str, ...]) -> None:
    outlet = _lsl_outlets.get(stream_key)
    if outlet is None:
        return

    try:
        values = [float(payload[field]) for field in fields]
    except (KeyError, TypeError, ValueError):
        return

    try:
        outlet.push_sample(values)
    except Exception as error:
        print(f"[BrainBit] Could not push {stream_key} sample to LSL: {error}")


def set_routing(
    *,
    forward_to_lsl: bool | None = None,
    forward_to_touchdesigner: bool | None = None,
) -> None:
    with _routing_lock:
        if forward_to_lsl is not None:
            _routing_state["forward_to_lsl"] = bool(forward_to_lsl)
        if forward_to_touchdesigner is not None:
            _routing_state["forward_to_touchdesigner"] = bool(forward_to_touchdesigner)

        state_snapshot = dict(_routing_state)

    _set_state({"routing": state_snapshot}, force=True)
    print(
        "[BrainBit] Routing updated: "
        f"LSL={'on' if state_snapshot['forward_to_lsl'] else 'off'}, "
        f"TouchDesigner={'on' if state_snapshot['forward_to_touchdesigner'] else 'off'}"
    )


def _is_routing_enabled(key: str) -> bool:
    with _routing_lock:
        return bool(_routing_state.get(key, False))


def _initialize_touchdesigner_client() -> None:
    global _td_client

    if not ensure_requirements(
        [("pythonosc", "python-osc")],
        auto_install=True,
        label="BrainBit OSC",
    ):
        _td_client = None
        return

    try:
        from pythonosc.udp_client import SimpleUDPClient

        _td_client = SimpleUDPClient(_config["osc_host"], int(_config["osc_port"]))
        print(
            "[BrainBit] TouchDesigner OSC proxy ready: "
            f"{_config['osc_host']}:{_config['osc_port']}"
        )
    except Exception as error:
        _td_client = None
        print(f"[BrainBit] Could not initialize TouchDesigner OSC proxy: {error}")


def _forward_line_to_touchdesigner(line: str) -> None:
    if _td_client is None or not _is_routing_enabled("forward_to_touchdesigner"):
        return

    parsed = _parse_json_line(line)
    if parsed is None:
        return

    tag, payload = parsed

    if tag == "EEG":
        for name in ("O1", "O2", "T3", "T4"):
            _send_td_num("EEG", name, payload.get(name), root_name=name)
    elif tag == "BANDS":
        for source_name, osc_name in (
            ("delta", "Delta"),
            ("theta", "Theta"),
            ("alpha", "Alpha"),
            ("beta", "Beta"),
            ("gamma", "Gamma"),
        ):
            _send_td_num("BANDS", osc_name, payload.get(source_name), root_name=osc_name)
    elif tag == "MENTAL":
        for name in (
            "Inst_Attention",
            "Inst_Relaxation",
            "Rel_Attention",
            "Rel_Relaxation",
        ):
            _send_td_num("MENTAL", name, payload.get(name), root_name=name)
    elif tag == "QUALITY":
        for name in ("O1", "O2", "T3", "T4"):
            _send_td_num("QUALITY", name, payload.get(name))
    elif tag == "BATTERY":
        _send_td_num("BATTERY", "percent", payload.get("percent"))
    elif tag == "ARTIFACT":
        _send_td_num("ARTIFACT", "Both", payload.get("both_now"))
        _send_td_num("ARTIFACT", "Seq", payload.get("sequence"))
    elif tag == "CALIB":
        if "progress_percent" in payload:
            try:
                _send_td_num("CALIB", "Progress", float(payload["progress_percent"]) / 100.0)
            except (TypeError, ValueError):
                pass
        event = payload.get("event")
        if event == "START":
            _send_td_num("CALIB", "Started", 1.0)
        elif event == "FINISHED":
            _send_td_num("CALIB", "Finished", 1.0)
        elif event == "FORCED_FINISH":
            _send_td_num("CALIB", "Finished", 1.0)
            _send_td_num("CALIB", "Forced", 1.0)


def _send_td_num(label: str, name: str, value: Any, root_name: str | None = None) -> None:
    if _td_client is None or value is None:
        return

    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return

    try:
        _td_client.send_message(f"/BrainBit/{label}/{name}", numeric_value)
        if root_name:
            _td_client.send_message(f"/BrainBit/{root_name}", numeric_value)
    except Exception as error:
        print(f"[BrainBit] Could not forward OSC {label}/{name}: {error}")


def _parse_json_line(line: str) -> tuple[str, dict[str, Any]] | None:
    parts = line.split(" ", 1)
    if len(parts) != 2 or not parts[1].startswith("{"):
        return None

    try:
        payload = json.loads(parts[1])
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None

    return parts[0], payload


def _initialize_lsl_outlets() -> None:
    global _lsl_outlets

    if not ensure_requirements(
        [("pylsl", "pylsl")],
        auto_install=bool(_config.get("lsl_auto_install", True)),
        label="BrainBit LSL",
    ):
        print("[BrainBit] LSL mirror disabled because pylsl is unavailable.")
        _lsl_outlets = {}
        return

    from pylsl import StreamInfo, StreamOutlet

    def create_outlet(stream_suffix: str, channel_labels: tuple[str, ...]) -> Any:
        stream_prefix = _config.get("lsl_stream_prefix", "BrainBit")
        nominal_rates = {"EEG": 250.0}
        info = StreamInfo(
            name=f"{stream_prefix}_{stream_suffix}",
            type=stream_suffix,
            channel_count=len(channel_labels),
            nominal_srate=nominal_rates.get(stream_suffix, 0.0),
            channel_format="float32",
            source_id=LSL_SOURCE_IDS[stream_suffix.lower()],
        )
        channels = info.desc().append_child("channels")
        units = LSL_CHANNEL_UNITS[stream_suffix.lower()]
        for label, unit in zip(channel_labels, units, strict=True):
            channel = channels.append_child("channel")
            channel.append_child_value("label", label)
            channel.append_child_value("unit", unit)
        return StreamOutlet(info)

    _lsl_outlets = {
        "EEG": create_outlet("EEG", ("O1", "O2", "T3", "T4")),
        "BANDS": create_outlet("BANDS", ("delta", "theta", "alpha", "beta", "gamma")),
        "MENTAL": create_outlet(
            "MENTAL",
            ("Inst_Attention", "Inst_Relaxation", "Rel_Attention", "Rel_Relaxation"),
        ),
        "QUALITY": create_outlet("QUALITY", ("O1", "O2", "T3", "T4")),
        "BATTERY": create_outlet("BATTERY", ("percent",)),
    }
    print("[BrainBit] LSL mirror outlets ready.")


def _derive_status(latest: dict[str, Any], running: bool) -> str:
    status = latest.get("status") or ("running" if running else "not_configured")
    if not running:
        if status in {"failed", "exited", "stopped", "not_configured", "disabled"}:
            return str(status)
        return "stopped"
    if not _has_recent_any_output(latest):
        return "stale"
    if status in {"failed", "exited", "stopped", "not_configured", "disabled", "stale", "scanning"}:
        return str(status)
    contact_state = latest.get("contact_quality_state")
    if contact_state == "poor":
        return "poor_contact"
    calibration = latest.get("calibration") if isinstance(latest.get("calibration"), dict) else {}
    if calibration and calibration.get("event") == "START":
        return "calibrating"
    if calibration and "progress_percent" in calibration and not latest.get("last_derived_at"):
        return "calibrating"
    if latest.get("last_eeg_at") and not latest.get("last_derived_at"):
        return "warming_up"
    if not _has_recent_sensor_activity(latest):
        return "warming_up" if latest.get("selected_device") or latest.get("device") else "waiting"
    return str(status)


def _derive_contact_quality(quality: Any) -> tuple[str, dict[str, str]]:
    if not isinstance(quality, dict) or not quality:
        return "unknown", {}

    channels: dict[str, str] = {}
    values: list[float] = []
    for channel in ("O1", "O2", "T3", "T4"):
        raw_value = quality.get(channel)
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            channels[channel] = "poor"
            continue
        values.append(value)
        if value <= 0 or value < 0.10:
            channels[channel] = "poor"
        elif value < 0.25:
            channels[channel] = "mixed"
        else:
            channels[channel] = "usable"

    if not values or any(state == "poor" for state in channels.values()):
        return "poor", channels
    if any(state == "mixed" for state in channels.values()):
        return "mixed", channels
    return "usable", channels


def _seconds_since_values(latest: dict[str, Any]) -> dict[str, float | None]:
    return {
        "seconds_since_last_any_line": _seconds_since(_epoch_from_latest(latest, "last_any_line_epoch", _last_any_line_at)),
        "seconds_since_last_activity": _seconds_since(
            _epoch_from_latest(latest, "last_sensor_activity_epoch", _last_sensor_activity_at or _last_activity_at)
        ),
        "seconds_since_last_eeg": _seconds_since(_epoch_from_latest(latest, "last_eeg_epoch", _last_eeg_at)),
        "seconds_since_last_quality": _seconds_since(_epoch_from_latest(latest, "last_quality_epoch", _last_quality_at)),
        "seconds_since_last_derived": _seconds_since(_epoch_from_latest(latest, "last_derived_epoch", _last_derived_at)),
    }


def _build_health(latest: dict[str, Any], running: bool, contact_state: str) -> dict[str, str]:
    calibration = latest.get("calibration") if isinstance(latest.get("calibration"), dict) else {}
    calibration_state = "waiting"
    if calibration.get("event") == "FINISHED":
        calibration_state = "ready"
    elif calibration.get("event") == "FORCED_FINISH":
        calibration_state = "forced"
    elif calibration.get("event") == "START" or "progress_percent" in calibration:
        calibration_state = "calibrating"

    if not running:
        connection_state = "stopped"
    elif _has_recent_sensor_activity(latest):
        connection_state = "connected"
    elif _has_recent_any_output(latest):
        connection_state = "waiting"
    else:
        connection_state = "stale"

    return {
        "process": "running" if running else "stopped",
        "connection": connection_state,
        "contact": contact_state,
        "calibration": calibration_state,
        "eeg": "receiving" if latest.get("last_eeg_at") else "waiting",
        "derived_metrics": "ready" if latest.get("last_derived_at") else "waiting",
        "recording": "recording" if bool(_config.get("lsl_enabled", False) and _lsl_outlets) else "disabled",
    }


def _epoch_from_latest(latest: dict[str, Any], key: str, fallback: float) -> float | None:
    try:
        value = float(latest.get(key) or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    if value > 0:
        return value
    return fallback if fallback > 0 else None


def _seconds_since(epoch: float | None) -> float | None:
    if not epoch:
        return None
    return round(max(0.0, time.time() - epoch), 1)


def _has_recent_any_output(latest: dict[str, Any]) -> bool:
    return _is_recent(_epoch_from_latest(latest, "last_any_line_epoch", _last_any_line_at))


def _has_recent_sensor_activity(latest: dict[str, Any]) -> bool:
    return _is_recent(_epoch_from_latest(latest, "last_sensor_activity_epoch", _last_sensor_activity_at or _last_activity_at))


def _is_recent(epoch: float | None) -> bool:
    if not epoch:
        return False
    timeout_s = max(1.0, _config.get("disconnect_timeout_ms", 20000) / 1000.0)
    return (time.time() - epoch) < timeout_s


def _target_device_from_config() -> dict[str, Any]:
    if not _config:
        return {}
    target = {
        "serial_number": _config.get("serial_number") or "",
        "address": _config.get("device_address") or "",
        "name": _config.get("device_name") or "",
        "index": _config.get("device_index"),
    }
    return {key: value for key, value in target.items() if value not in (None, "")}


def _merge_scan_candidate(payload: dict[str, Any]) -> list[dict[str, Any]]:
    with _state_lock:
        candidates = list(_latest_state.get("scan_candidates") or [])
    normalized = _normalize_scan_candidate(payload)
    identity = (
        str(normalized.get("serial") or normalized.get("serial_number") or "").lower(),
        str(normalized.get("address") or "").lower(),
        str(normalized.get("name") or "").lower(),
        str(normalized.get("index") if normalized.get("index") is not None else ""),
    )
    for idx, existing in enumerate(candidates):
        existing_identity = (
            str(existing.get("serial") or existing.get("serial_number") or "").lower(),
            str(existing.get("address") or "").lower(),
            str(existing.get("name") or "").lower(),
            str(existing.get("index") if existing.get("index") is not None else ""),
        )
        if existing_identity == identity:
            candidates[idx] = {**existing, **normalized}
            return candidates
    candidates.append(normalized)
    return candidates[-12:]


def _normalize_scan_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": payload.get("index"),
        "name": payload.get("name") or payload.get("Name"),
        "family": payload.get("family") or payload.get("SensFamily"),
        "address": payload.get("address") or payload.get("Address"),
        "serial": payload.get("serial") or payload.get("serial_number") or payload.get("SerialNumber"),
        "pairing_required": payload.get("pairing_required") or payload.get("PairingRequired"),
        "rssi": payload.get("rssi") or payload.get("RSSI"),
    }


def _timestamp(epoch: float | None = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch or time.time()))


# ============================================================
#  5. RESULTS - per-card interval summaries and sidecar export
# ============================================================
def get_interval_summary(start_epoch: float, end_epoch: float) -> dict[str, Any]:
    samples = samples_in_interval(_history, start_epoch, end_epoch)
    if not samples:
        return {
            "available": False,
            "sample_count": 0,
            "avg_attention": None,
            "avg_relaxation": None,
            "avg_alpha": None,
            "avg_beta": None,
            "avg_theta": None,
            "avg_delta": None,
            "avg_gamma": None,
            **truncation_info(_history, start_epoch),
        }

    mental_payloads = [sample["payload"] for sample in samples if sample.get("tag") == "MENTAL"]
    band_payloads = [sample["payload"] for sample in samples if sample.get("tag") == "BANDS"]

    return {
        "available": bool(mental_payloads or band_payloads),
        "sample_count": len(samples),
        "avg_attention": _mean_payload(mental_payloads, "Rel_Attention"),
        "avg_relaxation": _mean_payload(mental_payloads, "Rel_Relaxation"),
        "avg_alpha": _mean_payload(band_payloads, "alpha"),
        "avg_beta": _mean_payload(band_payloads, "beta"),
        "avg_theta": _mean_payload(band_payloads, "theta"),
        "avg_delta": _mean_payload(band_payloads, "delta"),
        "avg_gamma": _mean_payload(band_payloads, "gamma"),
        "max_gap_seconds": max_gap_seconds(samples),
        **truncation_info(_history, start_epoch),
    }


def export_interval_samples(start_epoch: float, end_epoch: float) -> list[dict[str, Any]]:
    """Return BrainBit adapter history for compact JSON sidecar export."""
    samples = samples_in_interval(_history, start_epoch, end_epoch)
    return [_public_history_sample(sample) for sample in samples]


def _public_history_sample(sample: dict[str, Any]) -> dict[str, Any]:
    return {
        "tag": sample.get("tag"),
        "payload": sample.get("payload") or {},
        "server_received_at": sample.get("server_received_at"),
        "server_received_epoch": sample.get("_epoch"),
    }


def _mean_payload(payloads: list[dict[str, Any]], key: str) -> float | None:
    values = []
    for payload in payloads:
        value = payload.get(key)
        try:
            if value is not None:
                values.append(float(value))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    return round(sum(values) / len(values), 4)


# ============================================================
#  6. WATCHDOG - stale detection and automatic restart
# ============================================================
def _watch_connection_health() -> None:
    while True:
        time.sleep(1.0)

        if not _check_connection_health_once():
            return


def _check_connection_health_once(now: float | None = None) -> bool:
    """Run one health check. Returns False when the watchdog should stop."""
    global _auto_restart_count

    stale_timeout = max(1.0, _config.get("disconnect_timeout_ms", 20000) / 1000.0)
    process = _process
    if process is None or process.poll() is not None:
        # The CLI is gone. Before 0.5 the watchdog ended here, which meant a CLI
        # that exited within seconds (no headset, Bluetooth off) was never
        # retried and left no visible trace in packaged builds.
        if not _desired_running:
            return False
        return _maybe_restart_after_exit(now or time.time())

    last_epoch = max(_last_any_line_at, _last_sensor_activity_at, _last_activity_at)
    if last_epoch <= 0:
        return True

    now_value = now or time.time()
    age = now_value - last_epoch
    if age < stale_timeout:
        # Real device data after an automatic restart proves recovery.
        if _auto_restart_count and _last_sensor_activity_at > _last_auto_restart_at:
            _auto_restart_count = 0
        return True

    with _state_lock:
        status = _latest_state.get("status")
    if status != "stale":
        _set_state(
            {
                "status": "stale",
                "last_message": (
                    f"No BrainBit output for {age:.1f}s - the CLI may be stuck or the device may be out of range."
                ),
                "seconds_since_last_activity": round(age, 1),
            },
            force=True,
        )
    _maybe_auto_restart(age, stale_timeout, now_value)
    return True


def _restart_backoff_seconds(attempt_count: int) -> float:
    """Wait before the next restart attempt: 5s, 15s, then 60s and up."""
    return min(300.0, 5.0 * (3 ** attempt_count))


def _maybe_restart_after_exit(now_value: float) -> bool:
    """Revive a CLI that exited on its own. Returns False to stop watching.

    Only exit codes where a retry can plausibly help are retried; a missing
    dependency or switched-off Bluetooth needs a human, so the watchdog stops
    and leaves the explanation on the dashboard.
    """
    global _auto_restart_count, _last_auto_restart_at

    reason = _exit_reason(_last_exit_code)
    if reason is None:
        return False  # clean exit: signal-seconds elapsed or stopped on purpose
    if not reason.get("retry") or not _config.get("auto_restart", True):
        return False

    max_attempts = int(_config.get("auto_restart_max_attempts", 3))
    if _auto_restart_count >= max_attempts:
        with _state_lock:
            already_final = _latest_state.get("auto_restart_exhausted")
        if not already_final:
            _set_state(
                {
                    "status": "failed",
                    "status_detail_key": reason["detail_key"],
                    "status_detail_hint_key": "brainbit.error.retriesExhausted",
                    "auto_restart_exhausted": True,
                    "last_message": (
                        f"{reason['message']} Study Runner tried {max_attempts} times. "
                        "Use Restart on the dashboard once the headset is ready."
                    ),
                },
                force=True,
            )
        return False

    backoff_seconds = _restart_backoff_seconds(_auto_restart_count)
    if _last_auto_restart_at and now_value - _last_auto_restart_at < backoff_seconds:
        return True
    if now_value - _last_exit_at < backoff_seconds:
        return True

    _auto_restart_count += 1
    _last_auto_restart_at = now_value
    print(
        f"[BrainBit] CLI exited with code {_last_exit_code} - retrying "
        f"(attempt {_auto_restart_count}/{max_attempts})."
    )
    _set_state(
        {
            "status": "restarting",
            "status_detail_key": "brainbit.error.restarting",
            "auto_restart_count": _auto_restart_count,
            "last_message": (
                f"{reason['message']} Trying again automatically "
                f"(attempt {_auto_restart_count} of {max_attempts})."
            ),
        },
        force=True,
    )
    try:
        start()
    except Exception as error:
        _set_state(
            {"status": "failed", "last_message": f"Automatic BrainBit restart failed: {error}"},
            force=True,
        )
    return True


def _maybe_auto_restart(age: float, stale_timeout: float, now_value: float) -> None:
    """Relaunch a hung CLI instead of waiting for the operator to notice.

    The MR60 adapter already reconnects on its own; this gives BrainBit the
    same self-healing. Limited attempts with exponential backoff so a dead
    device does not cause an endless restart loop.
    """
    global _auto_restart_count, _last_auto_restart_at

    if not _config.get("auto_restart", True) or _build_cli_command() is None:
        return
    max_attempts = int(_config.get("auto_restart_max_attempts", 3))
    if _auto_restart_count >= max_attempts:
        return
    if age < stale_timeout * 2:
        return  # short silence: give the device a chance to come back on its own
    backoff_seconds = min(300.0, 30.0 * (2 ** _auto_restart_count))
    if _last_auto_restart_at and now_value - _last_auto_restart_at < backoff_seconds:
        return

    _auto_restart_count += 1
    _last_auto_restart_at = now_value
    print(
        f"[BrainBit] No data for {age:.0f}s - restarting the CLI automatically "
        f"(attempt {_auto_restart_count}/{max_attempts})."
    )
    _set_state(
        {
            "status": "restarting",
            "auto_restart_count": _auto_restart_count,
            "last_message": (
                f"BrainBit was silent for {age:.0f}s. Restarting automatically "
                f"(attempt {_auto_restart_count} of {max_attempts})."
            ),
        },
        force=True,
    )
    try:
        restart()
    except Exception as error:
        _set_state(
            {"status": "failed", "last_message": f"Automatic BrainBit restart failed: {error}"},
            force=True,
        )


