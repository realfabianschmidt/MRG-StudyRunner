"""
Mini-radar adapter for pulse and breathing values.

The adapter can read sensor-near values either from a serial/USB JSON stream or
from the ESP32C6 BLE notification packet used by the MR60BHA2 firmware.
"""
from __future__ import annotations

import atexit
import asyncio
import contextlib
import json
import struct
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from ..dependency_utils import ensure_requirements


BLE_SERVICE_UUID = "9d6f0001-7d2a-4c6b-9f4e-5c2b1f4a6e10"
BLE_CHARACTERISTIC_UUID = "9d6f0002-7d2a-4c6b-9f4e-5c2b1f4a6e10"
BLE_DEVICE_NAME = "MR60_BLE"
BLE_PACKET = struct.Struct("<BBHIhhhhhh")
MISSING_INT16 = -32768

_lock = threading.Lock()
_state_lock = threading.Lock()
_config: dict[str, Any] = {}
_serial_connection: Any = None
_reader_thread: threading.Thread | None = None
_running = False
_stop_event = threading.Event()
_recording_enabled = False
_registered_shutdown = False
_lsl_outlets: dict[str, Any] = {}
_history: deque[dict[str, Any]] = deque(maxlen=4096)
_latest_state: dict[str, Any] = {
    "status": "not_configured",
    "latest": {},
    "last_message": "Mini-radar adapter has not been configured.",
}
_last_ble_sequence: int | None = None
_last_ble_timestamp_ms: int | None = None
_last_ble_host_epoch: float | None = None
_ble_total_dropped = 0


def initialize(
    *,
    enabled: bool = False,
    port: str = "",
    baudrate: int = 115200,
    connection_type: str = "serial",
    auto_install: bool = True,
    auto_reconnect: bool = True,
    reconnect_delay: float = 5.0,
    data_timeout_seconds: float = 5.0,
    lsl_enabled: bool = False,
    lsl_auto_install: bool = True,
    lsl_stream_prefix: str = "MiniRadar",
    log_dir: str | None = None,
    ble_device_name: str = BLE_DEVICE_NAME,
    ble_address: str = "",
    ble_scan_timeout_seconds: float = 5.0,
    ble_service_uuid: str = BLE_SERVICE_UUID,
    ble_characteristic_uuid: str = BLE_CHARACTERISTIC_UUID,
) -> None:
    """Configure the mini-radar adapter and start it if enabled."""
    global _config, _registered_shutdown

    normalized_connection_type = str(connection_type or "serial").strip().lower()
    if normalized_connection_type in {"bluetooth", "ble_notify"}:
        normalized_connection_type = "ble"

    _config = {
        "enabled": bool(enabled),
        "connection_type": normalized_connection_type,
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
        "ble_device_name": str(ble_device_name or BLE_DEVICE_NAME),
        "ble_address": str(ble_address or ""),
        "ble_scan_timeout_seconds": max(1.0, float(ble_scan_timeout_seconds)),
        "ble_service_uuid": str(ble_service_uuid or BLE_SERVICE_UUID),
        "ble_characteristic_uuid": str(ble_characteristic_uuid or BLE_CHARACTERISTIC_UUID),
    }

    _set_state(
        {
            "status": "configured" if enabled else "disabled",
            "enabled": bool(enabled),
            "connection_type": normalized_connection_type,
            "port": port,
            "ble_device_name": _config["ble_device_name"],
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
    """Start serial or BLE reading when mini-radar is enabled."""
    global _running, _reader_thread

    if not _config:
        _set_state({"status": "not_configured", "last_message": "Mini-radar adapter is not configured."})
        return get_status()
    if not _config.get("enabled"):
        _set_state({"status": "disabled", "last_message": "Mini-radar is disabled in hardware settings."})
        return get_status()
    if _connection_type() != "ble" and not _config.get("port"):
        _set_state({"status": "waiting", "last_message": "Mini-radar port is not configured."})
        return get_status()

    with _lock:
        if _running:
            return get_status()

        _running = True
        _stop_event.clear()
        if _connection_type() == "ble":
            _reset_ble_stats()
            target = _ble_loop
        else:
            target = _read_loop
        _reader_thread = threading.Thread(target=target, daemon=True)
        _reader_thread.start()

    _set_state(
        {
            "status": "starting",
            "connection_type": _connection_type(),
            "last_message": "Mini-radar BLE reader starting."
            if _connection_type() == "ble"
            else "Mini-radar serial reader starting.",
        }
    )
    return get_status()


def stop() -> dict[str, Any]:
    """Stop reading and close the radar connection."""
    global _running

    with _lock:
        _running = False
        _stop_event.set()
        _close_serial_connection()

    _set_state({"status": "stopped", "last_message": "Mini-radar reader stopped."})
    return get_status()


def restart() -> dict[str, Any]:
    stop()
    return start()


def is_configured() -> bool:
    """Return True after initialize() stored mini-radar settings."""
    return bool(_config)


def ingest_sample(payload: dict[str, Any], *, source: str = "manual") -> dict[str, Any]:
    """Ingest one radar sample from serial parsing, BLE parsing, or a direct API path."""
    sample = _normalize_sample(payload)
    sample["source"] = source
    sample["connection_type"] = _connection_type()
    sample["server_received_at"] = _timestamp()
    sample["_epoch"] = time.time()
    _history.append(dict(sample))

    _set_state(
        {
            "status": "connected" if sample.get("present", True) else "no_presence",
            "latest": sample,
            "last_activity_at": sample["server_received_at"],
            "last_activity_epoch": sample["_epoch"],
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

    latest = dict(status.get("latest") or {})
    last_activity = latest.get("server_received_at") or status.get("last_activity_at")
    status["latest"] = latest
    status["enabled"] = bool(_config.get("enabled", False))
    status["lsl_enabled"] = bool(_config.get("lsl_enabled", False))
    status["recording_enabled"] = bool(_recording_enabled)
    status["connection_type"] = _connection_type()
    status["port"] = _config.get("port", "")
    status["ble_device_name"] = _config.get("ble_device_name", BLE_DEVICE_NAME)
    status["streams"] = list(_lsl_outlets.keys())
    status["scan_timeout_seconds"] = _config.get("ble_scan_timeout_seconds")
    status["auto_reconnect"] = bool(_config.get("auto_reconnect", True))
    if last_activity:
        status["last_activity_at"] = last_activity

    last_epoch = _to_float(latest.get("_epoch") or status.get("last_activity_epoch"))
    if last_epoch is not None:
        age = max(0.0, time.time() - last_epoch)
        status["seconds_since_last_activity"] = round(age, 3)
        timeout = float(_config.get("data_timeout_seconds", 5.0))
        if _running and age > timeout and status.get("status") in {"connected", "no_presence", "starting"}:
            status["status"] = "stale"
            status["last_message"] = f"No mini-radar data for {age:.1f}s."

    return status


def get_interval_summary(start_epoch: float, end_epoch: float) -> dict[str, Any]:
    samples = _samples_in_interval(start_epoch, end_epoch)
    if not samples:
        return {
            "available": False,
            "sample_count": 0,
            "avg_heart_rate": None,
            "avg_breath_rate": None,
            "avg_quality": None,
            "avg_distance": None,
        }

    return {
        "available": True,
        "sample_count": len(samples),
        "avg_heart_rate": _mean(samples, "heartRate"),
        "avg_breath_rate": _mean(samples, "breathRate"),
        "avg_quality": _mean(samples, "quality"),
        "avg_distance": _mean(samples, "distance"),
        "total_dropped": max(
            int(sample.get("total_dropped") or 0)
            for sample in samples
        ),
    }


def export_interval_samples(start_epoch: float, end_epoch: float) -> list[dict[str, Any]]:
    """Return raw-ish history samples for compact JSON sidecar export."""
    return [_public_sample(sample) for sample in _samples_in_interval(start_epoch, end_epoch)]


def _read_loop() -> None:
    global _running

    last_reconnect_attempt = 0.0
    while _running:
        if _serial_connection is None:
            now = time.time()
            if now - last_reconnect_attempt >= _config.get("reconnect_delay", 5.0):
                last_reconnect_attempt = now
                _open_serial_connection()
                if _serial_connection is None and not _config.get("auto_reconnect", True):
                    break
            _stop_event.wait(0.2)
            continue

        try:
            raw_line = _serial_connection.readline()
        except Exception as error:
            _set_state({"status": "failed", "last_message": f"Mini-radar read failed: {error}"})
            _close_serial_connection()
            if not _config.get("auto_reconnect", True):
                break
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

    with _lock:
        if _reader_thread is threading.current_thread():
            _running = False


def _ble_loop() -> None:
    global _running

    if not ensure_requirements(
        [("bleak", "bleak")],
        auto_install=bool(_config.get("auto_install", True)),
        label="Mini-radar BLE",
    ):
        _set_state({"status": "failed", "last_message": "bleak is unavailable."})
        with _lock:
            if _reader_thread is threading.current_thread():
                _running = False
        return

    try:
        asyncio.run(_ble_async_loop())
    except Exception as error:
        _set_state({"status": "failed", "last_message": f"Mini-radar BLE loop failed: {error}"})
    finally:
        with _lock:
            if _reader_thread is threading.current_thread():
                _running = False


async def _ble_async_loop() -> None:
    while _running:
        try:
            device = await _find_ble_device()
            if device is None:
                retry_delay = float(_config.get("reconnect_delay", 5.0))
                _set_state(
                    {
                        "status": "waiting",
                        "last_scan_finished_at": _timestamp(),
                        "next_retry_at": _timestamp_from_epoch(time.time() + retry_delay),
                        "last_message": f"BLE device {_config.get('ble_device_name', BLE_DEVICE_NAME)} not found.",
                    }
                )
                if not _config.get("auto_reconnect", True):
                    break
                await _ble_delay(retry_delay)
                continue

            await _run_ble_client(device)
        except Exception as error:
            if _running:
                _set_state({"status": "waiting", "last_message": f"Mini-radar BLE connection failed: {error}"})

        if not _running or not _config.get("auto_reconnect", True):
            break
        await _ble_delay(float(_config.get("reconnect_delay", 5.0)))


async def _find_ble_device() -> Any:
    from bleak import BleakScanner

    address = str(_config.get("ble_address") or "").strip()
    if address:
        return address

    device_name = str(_config.get("ble_device_name") or BLE_DEVICE_NAME)
    timeout = float(_config.get("ble_scan_timeout_seconds", 5.0))
    _set_state(
        {
            "status": "scanning",
            "scan_timeout_seconds": timeout,
            "last_scan_started_at": _timestamp(),
            "last_scan_finished_at": None,
            "next_retry_at": None,
            "last_message": f"Scanning for BLE device {device_name} for {timeout:g} seconds.",
        }
    )
    devices = await BleakScanner.discover(timeout=timeout)
    _set_state({"last_scan_finished_at": _timestamp()})
    for device in devices:
        names = {getattr(device, "name", None)}
        details = getattr(device, "metadata", {}) or {}
        names.add(details.get("local_name"))
        if device_name in {name for name in names if name}:
            return device
    return None


async def _run_ble_client(device: Any) -> None:
    from bleak import BleakClient

    characteristic_uuid = _config.get("ble_characteristic_uuid", BLE_CHARACTERISTIC_UUID)
    device_label = _config.get("ble_device_name", BLE_DEVICE_NAME)
    async with BleakClient(device) as client:
        if not client.is_connected:
            raise RuntimeError(f"Could not connect to {device_label}")

        _set_state({"status": "connected", "last_message": f"Mini-radar BLE connected to {device_label}."})
        await client.start_notify(characteristic_uuid, _handle_ble_notification)
        try:
            while _running and client.is_connected:
                await asyncio.sleep(0.2)
        finally:
            with contextlib.suppress(Exception):
                await client.stop_notify(characteristic_uuid)

    if _running:
        _set_state({"status": "waiting", "last_message": "Mini-radar BLE disconnected."})


async def _ble_delay(seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while _running and time.monotonic() < deadline:
        await asyncio.sleep(min(0.2, max(0.0, deadline - time.monotonic())))


def _handle_ble_notification(_: Any, data: bytearray) -> None:
    payload = _decode_ble_packet(bytes(data))
    if payload is None:
        return
    ingest_sample(payload, source="ble")


def _decode_ble_packet(data: bytes) -> dict[str, Any] | None:
    global _last_ble_sequence, _last_ble_timestamp_ms, _last_ble_host_epoch, _ble_total_dropped

    if len(data) < BLE_PACKET.size:
        _set_state({"last_message": f"BLE packet ignored: expected 20 bytes, got {len(data)}."})
        return None

    (
        version,
        flags,
        sequence,
        timestamp_ms,
        heart_rate_x10,
        breath_rate_x10,
        distance_cm_x10,
        heart_phase_x100,
        breath_phase_x100,
        total_phase_x100,
    ) = BLE_PACKET.unpack(data[: BLE_PACKET.size])

    host_epoch = time.time()
    dropped_since_previous = 0
    device_interval_ms = None
    host_interval_ms = None
    jitter_ms = None

    if _last_ble_sequence is not None:
        expected_sequence = (_last_ble_sequence + 1) & 0xFFFF
        if sequence != expected_sequence:
            sequence_gap = (sequence - expected_sequence) & 0xFFFF
            if sequence_gap < 32768:
                dropped_since_previous = sequence_gap
                _ble_total_dropped += dropped_since_previous

    if _last_ble_timestamp_ms is not None:
        device_interval_ms = (int(timestamp_ms) - int(_last_ble_timestamp_ms)) & 0xFFFFFFFF
        if device_interval_ms > 0x7FFFFFFF:
            device_interval_ms = None

    if _last_ble_host_epoch is not None:
        host_interval_ms = (host_epoch - _last_ble_host_epoch) * 1000.0

    if device_interval_ms is not None and host_interval_ms is not None:
        jitter_ms = host_interval_ms - float(device_interval_ms)

    _last_ble_sequence = int(sequence)
    _last_ble_timestamp_ms = int(timestamp_ms)
    _last_ble_host_epoch = host_epoch

    return {
        "version": int(version),
        "flags": int(flags),
        "valid": bool(flags & 0x01),
        "stabilized": bool(flags & 0x02),
        "present": bool(flags & 0x04),
        "sequence_number": int(sequence),
        "timestamp": int(timestamp_ms),
        "timestamp_ms": int(timestamp_ms),
        "heartRate": _scale_int16(heart_rate_x10, 10.0),
        "breathRate": _scale_int16(breath_rate_x10, 10.0),
        "distance": _scale_int16(distance_cm_x10, 10.0),
        "heartPhase": _scale_int16(heart_phase_x100, 100.0),
        "breathPhase": _scale_int16(breath_phase_x100, 100.0),
        "totalPhase": _scale_int16(total_phase_x100, 100.0),
        "dropped_since_previous": dropped_since_previous,
        "total_dropped": _ble_total_dropped,
        "device_interval_ms": device_interval_ms,
        "host_interval_ms": round(host_interval_ms, 3) if host_interval_ms is not None else None,
        "jitter_ms": round(jitter_ms, 3) if jitter_ms is not None else None,
    }


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
        "version": _to_int(payload.get("version")),
        "flags": _to_int(payload.get("flags")),
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
        "source_timestamp": payload.get("timestamp", payload.get("timestamp_ms")),
        "timestamp_ms": _to_int(payload.get("timestamp_ms", payload.get("timestamp"))),
        "sequence_number": _to_int(payload.get("sequence_number", payload.get("sequence"))),
        "dropped_since_previous": _to_int(payload.get("dropped_since_previous")),
        "total_dropped": _to_int(payload.get("total_dropped")),
        "device_interval_ms": _to_float(payload.get("device_interval_ms")),
        "host_interval_ms": _to_float(payload.get("host_interval_ms")),
        "jitter_ms": _to_float(payload.get("jitter_ms")),
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


def _samples_in_interval(start_epoch: float, end_epoch: float) -> list[dict[str, Any]]:
    return [
        sample for sample in list(_history)
        if start_epoch <= float(sample.get("_epoch", 0.0)) <= end_epoch
    ]


def _public_sample(sample: dict[str, Any]) -> dict[str, Any]:
    public: dict[str, Any] = {}
    for key, value in sample.items():
        if key == "_epoch":
            public["server_received_epoch"] = value
        elif not key.startswith("_"):
            public[key] = value
    return public


def _connection_type() -> str:
    return str(_config.get("connection_type") or "serial")


def _reset_ble_stats() -> None:
    global _last_ble_sequence, _last_ble_timestamp_ms, _last_ble_host_epoch, _ble_total_dropped
    _last_ble_sequence = None
    _last_ble_timestamp_ms = None
    _last_ble_host_epoch = None
    _ble_total_dropped = 0


def _scale_int16(value: int, divisor: float) -> float | None:
    if int(value) == MISSING_INT16:
        return None
    return round(float(value) / divisor, 4)


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "present"}
    return bool(value)


def _mean(samples: list[dict[str, Any]], key: str) -> float | None:
    values = [float(sample[key]) for sample in samples if sample.get(key) is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _timestamp_from_epoch(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))

