"""
Mini-radar adapter for pulse and breathing values.

The first implementation is deliberately raw-data-first:
  - read radar values from a serial/USB source when configured
  - validate and keep the latest sensor-near values
  - optionally publish values into LSL for LabRecorder and `.xdf`
  - expose status for the admin dashboard
"""
from __future__ import annotations

import atexit
import json
import threading
import time
from pathlib import Path
from typing import Any

from .dependency_utils import ensure_requirements


_lock = threading.Lock()
_state_lock = threading.Lock()
_config: dict[str, Any] = {}
_serial_connection: Any = None
_reader_thread: threading.Thread | None = None
_running = False
_recording_enabled = False
_registered_shutdown = False
_lsl_outlets: dict[str, Any] = {}
_latest_state: dict[str, Any] = {
    "status": "not_configured",
    "latest": {},
    "last_message": "Mini-radar adapter has not been configured.",
}


def initialize(
    *,
    enabled: bool = False,
    port: str = "",
    baudrate: int = 115200,
    auto_install: bool = True,
    auto_reconnect: bool = True,
    reconnect_delay: float = 5.0,
    data_timeout_seconds: float = 5.0,
    lsl_enabled: bool = False,
    lsl_auto_install: bool = True,
    lsl_stream_prefix: str = "MiniRadar",
    log_dir: str | None = None,
) -> None:
    """Configure the mini-radar adapter and start it if enabled."""
    global _config, _registered_shutdown

    _config = {
        "enabled": bool(enabled),
        "port": port,
        "baudrate": int(baudrate),
        "auto_install": bool(auto_install),
        "auto_reconnect": bool(auto_reconnect),
        "reconnect_delay": float(reconnect_delay),
        "data_timeout_seconds": max(1.0, float(data_timeout_seconds)),
        "lsl_enabled": bool(lsl_enabled),
        "lsl_auto_install": bool(lsl_auto_install),
        "lsl_stream_prefix": lsl_stream_prefix,
        "log_dir": str(Path(log_dir).expanduser()) if log_dir else "",
    }

    _set_state(
        {
            "status": "configured" if enabled else "disabled",
            "enabled": bool(enabled),
            "port": port,
            "last_message": "Mini-radar adapter configured.",
        }
    )

    if _config["enabled"] and _config["lsl_enabled"]:
        _initialize_lsl_outlets()

    if not _registered_shutdown:
        atexit.register(stop)
        _registered_shutdown = True

    if enabled:
        start()


def start() -> dict[str, Any]:
    """Start serial reading when mini-radar is enabled and a port is configured."""
    global _running, _reader_thread

    if not _config:
        _set_state({"status": "not_configured", "last_message": "Mini-radar adapter is not configured."})
        return get_status()
    if not _config.get("enabled"):
        _set_state({"status": "disabled", "last_message": "Mini-radar is disabled in hardware_config.json."})
        return get_status()
    if not _config.get("port"):
        _set_state({"status": "waiting", "last_message": "Mini-radar port is not configured."})
        return get_status()

    with _lock:
        if _running:
            return get_status()

        _running = True
        _reader_thread = threading.Thread(target=_read_loop, daemon=True)
        _reader_thread.start()

    _set_state({"status": "starting", "last_message": "Mini-radar reader starting."})
    return get_status()


def stop() -> dict[str, Any]:
    """Stop serial reading and close the radar connection."""
    global _running

    with _lock:
        _running = False
        _close_serial_connection()

    _set_state({"status": "stopped", "last_message": "Mini-radar reader stopped."})
    return get_status()


def restart() -> dict[str, Any]:
    stop()
    return start()


def ingest_sample(payload: dict[str, Any], *, source: str = "manual") -> dict[str, Any]:
    """Ingest one radar sample from serial parsing or a future direct API path."""
    sample = _normalize_sample(payload)
    sample["source"] = source
    sample["server_received_at"] = _timestamp()

    _set_state(
        {
            "status": "connected" if sample.get("present", True) else "no_presence",
            "latest": sample,
            "last_activity_at": sample["server_received_at"],
            "last_message": "Mini-radar sample received.",
        }
    )
    if _recording_enabled:
        _push_lsl_sample(sample)
    return sample


def set_recording(enabled: bool) -> None:
    """Control whether radar samples are mirrored to LSL during the active phase."""
    global _recording_enabled
    _recording_enabled = bool(enabled)
    _set_state(
        {
            "recording_enabled": _recording_enabled,
            "last_message": f"Mini-radar recording {'enabled' if _recording_enabled else 'disabled'}.",
        }
    )


def get_status() -> dict[str, Any]:
    with _state_lock:
        status = dict(_latest_state)

    latest = status.get("latest") or {}
    last_activity = latest.get("server_received_at") or status.get("last_activity_at")
    status["enabled"] = bool(_config.get("enabled", False))
    status["lsl_enabled"] = bool(_config.get("lsl_enabled", False))
    status["recording_enabled"] = bool(_recording_enabled)
    status["port"] = _config.get("port", "")
    status["streams"] = list(_lsl_outlets.keys())
    if last_activity:
        status["last_activity_at"] = last_activity
    return status


def _read_loop() -> None:
    global _running

    last_reconnect_attempt = 0.0
    while _running:
        if _serial_connection is None:
            now = time.time()
            if now - last_reconnect_attempt >= _config.get("reconnect_delay", 5.0):
                last_reconnect_attempt = now
                _open_serial_connection()
            time.sleep(0.2)
            continue

        try:
            raw_line = _serial_connection.readline()
        except Exception as error:
            _set_state({"status": "failed", "last_message": f"Mini-radar read failed: {error}"})
            _close_serial_connection()
            continue

        if not raw_line:
            continue

        try:
            line = raw_line.decode("utf-8", errors="replace").strip()
        except AttributeError:
            line = str(raw_line).strip()

        if not line:
            continue

        payload = _parse_line(line)
        if payload is None:
            _set_state({"last_message": f"Mini-radar line ignored: {line[:120]}"})
            continue

        ingest_sample(payload, source="serial")


def _open_serial_connection() -> None:
    global _serial_connection

    if not ensure_requirements(
        [("serial", "pyserial")],
        auto_install=bool(_config.get("auto_install", True)),
        label="Mini-radar serial",
    ):
        _set_state({"status": "failed", "last_message": "pyserial is unavailable."})
        return

    try:
        import serial

        _serial_connection = serial.Serial(
            port=_config["port"],
            baudrate=int(_config.get("baudrate", 115200)),
            timeout=0.2,
        )
        _set_state({"status": "connected", "last_message": f"Mini-radar connected on {_config['port']}."})
    except Exception as error:
        _serial_connection = None
        _set_state({"status": "waiting", "last_message": f"Mini-radar connection failed: {error}"})


def _close_serial_connection() -> None:
    global _serial_connection
    if _serial_connection is None:
        return
    try:
        _serial_connection.close()
    except Exception:
        pass
    _serial_connection = None


def _parse_line(line: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_sample(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "heartRate": _to_float(payload.get("heartRate")),
        "breathRate": _to_float(payload.get("breathRate")),
        "present": _to_bool(payload.get("present", payload.get("presence", True))),
        "valid": _to_bool(payload.get("valid", True)),
        "stabilized": _to_bool(payload.get("stabilized", True)),
        "quality": _to_float(payload.get("quality", payload.get("signalQuality"))),
        "distance": _to_float(payload.get("distance")),
        "heartPhase": _to_float(payload.get("heartPhase")),
        "breathPhase": _to_float(payload.get("breathPhase")),
        "totalPhase": _to_float(payload.get("totalPhase")),
        "validReadings": _to_float(payload.get("validReadings")),
        "invalidReadings": _to_float(payload.get("invalidReadings")),
        "source_timestamp": payload.get("timestamp"),
        "sequence_number": payload.get("sequence_number"),
    }


def _initialize_lsl_outlets() -> None:
    global _lsl_outlets

    if not ensure_requirements(
        [("pylsl", "pylsl")],
        auto_install=bool(_config.get("lsl_auto_install", True)),
        label="Mini-radar LSL",
    ):
        _lsl_outlets = {}
        return

    from pylsl import StreamInfo, StreamOutlet

    prefix = _config.get("lsl_stream_prefix", "MiniRadar")

    def create_outlet(suffix: str, labels: tuple[str, ...]) -> Any:
        info = StreamInfo(
            name=f"{prefix}_{suffix}",
            type=suffix,
            channel_count=len(labels),
            nominal_srate=0,
            channel_format="float32",
            source_id=f"{prefix.lower()}_{suffix.lower()}",
        )
        channels = info.desc().append_child("channels")
        for label in labels:
            channel = channels.append_child("channel")
            channel.append_child_value("label", label)
        return StreamOutlet(info)

    _lsl_outlets = {
        "VITALS": create_outlet("VITALS", ("heartRate", "breathRate", "quality", "distance")),
        "PHASES": create_outlet("PHASES", ("heartPhase", "breathPhase", "totalPhase")),
    }
    print("[MiniRadar] LSL outlets ready.")


def _push_lsl_sample(sample: dict[str, Any]) -> None:
    if not _lsl_outlets:
        return

    _push_lsl_values("VITALS", sample, ("heartRate", "breathRate", "quality", "distance"))
    _push_lsl_values("PHASES", sample, ("heartPhase", "breathPhase", "totalPhase"))


def _push_lsl_values(stream_key: str, sample: dict[str, Any], fields: tuple[str, ...]) -> None:
    outlet = _lsl_outlets.get(stream_key)
    if outlet is None:
        return

    values = []
    for field in fields:
        value = sample.get(field)
        values.append(float(value) if value is not None else 0.0)

    try:
        outlet.push_sample(values)
    except Exception as error:
        print(f"[MiniRadar] Could not push {stream_key} sample to LSL: {error}")


def _set_state(values: dict[str, Any]) -> None:
    with _state_lock:
        _latest_state.update(values)
        _latest_state["updated_at"] = _timestamp()


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "present"}
    return bool(value)


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
