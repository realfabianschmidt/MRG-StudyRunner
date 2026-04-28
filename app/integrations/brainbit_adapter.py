"""
BrainBit adapter - launches a repo-local BrainBit CLI process and optionally mirrors its output to LSL.

Expected setup inside this repository:
  - BrainBit Python CLI script in the project folder, for example:
      brainbit/brainbit_realtime_cli_OSC_15.py
  - TouchDesigner project listening for OSC on the configured port, for example:
      brainbit/HelloEEG_HelloMYO_01.3.toe

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
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .dependency_utils import ensure_requirements


_lock = threading.Lock()
_state_lock = threading.Lock()
_routing_lock = threading.Lock()
_process: subprocess.Popen[str] | None = None
_reader_thread: threading.Thread | None = None
_monitor_process: subprocess.Popen[str] | None = None
_watchdog_thread: threading.Thread | None = None
_registered_shutdown = False
_config: dict[str, Any] = {}
_lsl_outlets: dict[str, Any] = {}
_td_client: Any = None
_latest_state: dict[str, Any] = {}
_last_state_write = 0.0
_last_state_write_error_at = 0.0
_last_activity_at = 0.0
_log_handle: Any = None
_routing_state = {
    "forward_to_lsl": False,
    "forward_to_touchdesigner": False,
}


def initialize(
    *,
    script_path: str,
    working_dir: str | None = None,
    python_executable: str | None = None,
    osc_host: str = "127.0.0.1",
    osc_port: int = 8000,
    scan_seconds: int = 5,
    resist_seconds: int = 6,
    signal_seconds: int = 0,
    pretty: bool = False,
    debug: bool = False,
    lsl_enabled: bool = False,
    lsl_auto_install: bool = True,
    lsl_stream_prefix: str = "BrainBit",
    quiet_output: bool = True,
    open_monitor_terminal: bool = True,
    monitor_refresh_ms: int = 1000,
    disconnect_timeout_ms: int = 5000,
    log_dir: str | None = None,
) -> None:
    """Store BrainBit settings, prepare optional LSL mirrors, and start the external CLI."""
    global _registered_shutdown, _config

    script_file = Path(script_path).expanduser()
    if not script_file.exists():
        print(f"[BrainBit] Script not found: {script_file}")
        return

    resolved_working_dir = Path(working_dir).expanduser() if working_dir else script_file.parent
    resolved_log_dir = Path(log_dir).expanduser() if log_dir else resolved_working_dir / "logs"
    resolved_log_dir.mkdir(parents=True, exist_ok=True)

    _config = {
        "script_path": str(script_file),
        "working_dir": str(resolved_working_dir),
        "python_executable": python_executable or sys.executable,
        "osc_host": osc_host,
        "osc_port": int(osc_port),
        "scan_seconds": int(scan_seconds),
        "resist_seconds": int(resist_seconds),
        "signal_seconds": int(signal_seconds),
        "pretty": bool(pretty),
        "debug": bool(debug),
        "lsl_enabled": bool(lsl_enabled),
        "lsl_auto_install": bool(lsl_auto_install),
        "lsl_stream_prefix": lsl_stream_prefix,
        "quiet_output": bool(quiet_output),
        "open_monitor_terminal": bool(open_monitor_terminal),
        "monitor_refresh_ms": max(250, int(monitor_refresh_ms)),
        "disconnect_timeout_ms": max(1000, int(disconnect_timeout_ms)),
        "log_dir": str(resolved_log_dir),
        "raw_log_path": str(resolved_log_dir / "brainbit_runtime.log"),
        "state_path": str(resolved_log_dir / "brainbit_state.json"),
    }

    _set_state(
        {
            "status": "configured",
            "script_path": _config["script_path"],
            "working_dir": _config["working_dir"],
            "osc_target": f"{_config['osc_host']}:{_config['osc_port']}",
            "raw_log_path": _config["raw_log_path"],
            "state_path": _config["state_path"],
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
    global _process, _reader_thread, _log_handle, _watchdog_thread, _last_activity_at

    if not _config:
        print("[BrainBit] Adapter not configured.")
        return

    with _lock:
        if _process is not None and _process.poll() is None:
            return

        command = [
            _config["python_executable"],
            _config["script_path"],
            "--no-osc",
            "--scan-seconds",
            str(_config["scan_seconds"]),
            "--resist-seconds",
            str(_config["resist_seconds"]),
            "--signal-seconds",
            str(_config["signal_seconds"]),
        ]
        if _config["pretty"]:
            command.append("--pretty")
        if _config["debug"]:
            command.append("--debug")

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
                "status": "starting",
                "pid": _process.pid,
                "last_message": "BrainBit CLI started.",
            },
            force=True,
        )
        _last_activity_at = time.time()
        _reader_thread = threading.Thread(target=_read_output, args=(_process,), daemon=True)
        _reader_thread.start()
        if _watchdog_thread is None or not _watchdog_thread.is_alive():
            _watchdog_thread = threading.Thread(target=_watch_connection_health, daemon=True)
            _watchdog_thread.start()
        _start_monitor_terminal()
        print(
            "[BrainBit] External CLI started "
            f"({Path(_config['script_path']).name} -> OSC {_config['osc_host']}:{_config['osc_port']})"
        )
        print(f"[BrainBit] Monitor state: {_config['state_path']}")
        print(f"[BrainBit] Raw log: {_config['raw_log_path']}")


def stop() -> None:
    """Stop the repo-local BrainBit process if it is running."""
    global _process

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
            global _process
            if _process is process:
                _process = None
        _set_state(
            {
                "status": "exited",
                "exit_code": exit_code,
                "last_message": f"BrainBit CLI exited with code {exit_code}.",
            },
            force=True,
        )
        _close_log_handle()
        print(f"[BrainBit] External CLI exited with code {exit_code}.")


def _update_state_from_line(line: str) -> bool:
    global _last_activity_at

    important = False
    state_update: dict[str, Any] = {"updated_at": _timestamp(), "last_line": line}

    parts = line.split(" ", 1)
    if len(parts) == 2 and parts[1].startswith("{"):
        tag, payload_text = parts
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            if tag in {"EEG", "QUALITY", "BATTERY", "BANDS", "MENTAL", "STATE", "DEVICE"}:
                _last_activity_at = time.time()
                state_update["last_activity_at"] = _timestamp()
                state_update["status"] = "connected"
            if tag == "SCAN":
                state_update["last_scan"] = payload
            elif tag == "DEVICE":
                state_update["device"] = payload
                state_update["status"] = "connected"
                state_update["last_message"] = "BrainBit device connected."
                important = True
            elif tag == "BATTERY":
                state_update["battery"] = payload
            elif tag == "QUALITY":
                state_update["quality"] = payload
            elif tag == "EEG":
                state_update["eeg"] = payload
            elif tag == "BANDS":
                state_update["bands"] = payload
            elif tag == "MENTAL":
                state_update["mental"] = payload
            elif tag == "STATE":
                state_update["sensor_state"] = payload
                important = True
            elif tag == "CALIB":
                state_update["calibration"] = payload
                if payload.get("event"):
                    state_update["last_message"] = f"Calibration: {payload['event']}"
                    important = True
            elif tag == "EMO_INIT_FAIL":
                state_update["status"] = "failed"
                state_update["last_message"] = payload.get("error", "EmotionalMath init failed.")
                important = True
    elif line.startswith("[WARN]") or line.startswith("# ERROR") or line.startswith("# FATAL"):
        state_update["last_message"] = line
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


def _start_monitor_terminal() -> None:
    global _monitor_process

    if not _config.get("open_monitor_terminal", True):
        return
    if _monitor_process is not None and _monitor_process.poll() is None:
        return

    monitor_script = Path(__file__).with_name("brainbit_monitor.py")
    if not monitor_script.exists():
        print(f"[BrainBit] Monitor script not found: {monitor_script}")
        return

    command_args = [
        _config["python_executable"],
        str(monitor_script),
        "--state-file",
        _config["state_path"],
        "--log-file",
        _config["raw_log_path"],
        "--refresh-ms",
        str(_config["monitor_refresh_ms"]),
    ]

    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        try:
            _monitor_process = subprocess.Popen(
                command_args,
                cwd=_config["working_dir"],
                creationflags=creationflags,
            )
        except OSError as error:
            print(f"[BrainBit] Could not open monitor terminal: {error}")
        return

    if sys.platform == "darwin":
        shell_command = "cd {cwd} && {command}".format(
            cwd=shlex.quote(_config["working_dir"]),
            command=" ".join(shlex.quote(part) for part in command_args),
        )
        apple_script = (
            'tell application "Terminal"\n'
            "  activate\n"
            f'  do script "{_escape_for_applescript(shell_command)}"\n'
            "end tell"
        )
        try:
            _monitor_process = subprocess.Popen(["osascript", "-e", apple_script])
        except OSError as error:
            print(f"[BrainBit] Could not open macOS Terminal monitor: {error}")
        return

    print("[BrainBit] Automatic monitor terminal is currently supported on Windows and macOS.")


def _mirror_line_to_lsl(line: str) -> None:
    if not _lsl_outlets or not _is_routing_enabled("forward_to_lsl"):
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
        info = StreamInfo(
            name=f"{stream_prefix}_{stream_suffix}",
            type=stream_suffix,
            channel_count=len(channel_labels),
            nominal_srate=0,
            channel_format="float32",
            source_id=f"{stream_prefix.lower()}_{stream_suffix.lower()}",
        )
        channels = info.desc().append_child("channels")
        for label in channel_labels:
            channel = channels.append_child("channel")
            channel.append_child_value("label", label)
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


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _escape_for_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _watch_connection_health() -> None:
    stale_timeout = max(1.0, _config.get("disconnect_timeout_ms", 5000) / 1000.0)

    while True:
        time.sleep(1.0)

        process = _process
        if process is None or process.poll() is not None:
            return

        if _last_activity_at <= 0:
            continue

        age = time.time() - _last_activity_at
        if age < stale_timeout:
            continue

        status = _latest_state.get("status")
        if status == "stale":
            continue

        _set_state(
            {
                "status": "stale",
                "last_message": (
                    f"No BrainBit data for {age:.1f}s - connection may be lost or the band may be off."
                ),
                "seconds_since_last_activity": round(age, 1),
            },
            force=True,
        )
