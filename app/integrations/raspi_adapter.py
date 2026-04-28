from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any


DEFAULT_TIMEOUT_SECONDS = 2.0
SENSOR_NAMES = {"brainbit", "emg", "radar", "camera"}
COMMAND_NAMES = {"start", "stop", "restart"}


def get_status(raspi_config: dict[str, Any]) -> dict[str, Any]:
    """Return one dashboard-friendly status payload for the Raspberry Pi gateway."""
    if not raspi_config.get("enabled"):
        return {
            "enabled": False,
            "connected": False,
            "status": "disabled",
            "host": raspi_config.get("host", ""),
            "port": int(raspi_config.get("port", 3001) or 3001),
            "sensors": {},
            "last_message": "Raspberry Pi gateway disabled in hardware_config.json.",
        }

    response = _request_json(
        "GET",
        raspi_config,
        "/status",
    )
    if not response.get("ok"):
        return {
            "enabled": True,
            "connected": False,
            "status": "unreachable",
            "host": raspi_config.get("host", ""),
            "port": int(raspi_config.get("port", 3001) or 3001),
            "sensors": _fallback_sensor_state(raspi_config),
            "last_message": response.get("error", "Raspberry Pi gateway unreachable."),
        }

    payload = response.get("data")
    if not isinstance(payload, dict):
        payload = {}

    sensors = payload.get("sensors")
    if not isinstance(sensors, dict):
        sensors = {}

    return {
        "enabled": True,
        "connected": bool(payload.get("connected", True)),
        "status": str(payload.get("status") or "connected"),
        "host": str(payload.get("host") or raspi_config.get("host", "")),
        "port": int(payload.get("port") or raspi_config.get("port", 3001) or 3001),
        "sensors": {**_fallback_sensor_state(raspi_config), **sensors},
        "last_message": str(payload.get("message") or payload.get("last_message") or "RPi status received."),
        "updated_at": payload.get("updated_at"),
    }


def push_config(raspi_config: dict[str, Any]) -> dict[str, Any]:
    """Push the current sensor configuration to the Raspberry Pi manager."""
    payload = {
        "mac_host": _resolve_mac_host(raspi_config),
        "mac_port": int(raspi_config.get("mac_port", 3000) or 3000),
        "sensors": raspi_config.get("sensors", {}),
    }
    response = _request_json("POST", raspi_config, "/config", payload)
    if response.get("ok"):
        return {
            "ok": True,
            "message": _response_message(response.get("data"), "Raspberry Pi config pushed."),
            "data": response.get("data"),
        }
    return {
        "ok": False,
        "message": response.get("error", "Could not push Raspberry Pi config."),
    }


def control_sensor(raspi_config: dict[str, Any], *, sensor: str, command: str) -> dict[str, Any]:
    """Forward a start/stop/restart command for one Raspberry Pi sensor."""
    normalized_sensor = str(sensor or "").strip().lower()
    normalized_command = str(command or "").strip().lower()
    if normalized_sensor not in SENSOR_NAMES:
        raise ValueError(f"Unknown Raspberry Pi sensor: {sensor!r}")
    if normalized_command not in COMMAND_NAMES:
        raise ValueError(f"Unknown Raspberry Pi command: {command!r}")

    response = _request_json(
        "POST",
        raspi_config,
        f"/{normalized_command}",
        {"sensor": normalized_sensor},
    )
    if response.get("ok"):
        return {
            "ok": True,
            "message": _response_message(
                response.get("data"),
                f"Raspberry Pi sensor {normalized_sensor} {normalized_command} sent.",
            ),
            "data": response.get("data"),
        }
    return {
        "ok": False,
        "message": response.get(
            "error",
            f"Could not {normalized_command} Raspberry Pi sensor {normalized_sensor}.",
        ),
    }


def _request_json(
    method: str,
    raspi_config: dict[str, Any],
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    host = str(raspi_config.get("host") or "").strip()
    port = int(raspi_config.get("port", 3001) or 3001)
    if not host:
        return {"ok": False, "error": "Raspberry Pi host is not configured."}

    data: bytes | None = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        f"http://{host}:{port}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        error_body = _decode_json_bytes(error.read())
        error_message = _response_message(
            error_body,
            f"HTTP {error.code} while contacting Raspberry Pi gateway.",
        )
        return {"ok": False, "error": error_message}
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        return {"ok": False, "error": _format_network_error(error, host, port)}

    body = _decode_json_bytes(raw)
    if isinstance(body, dict):
        return {"ok": True, "data": body}
    return {"ok": True, "data": {}}


def _decode_json_bytes(raw: bytes) -> dict[str, Any] | None:
    if not raw:
        return {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _format_network_error(error: Exception, host: str, port: int) -> str:
    reason = getattr(error, "reason", error)
    return f"Could not reach Raspberry Pi gateway at http://{host}:{port}: {reason}"


def _fallback_sensor_state(raspi_config: dict[str, Any]) -> dict[str, Any]:
    sensors = raspi_config.get("sensors", {})
    if not isinstance(sensors, dict):
        sensors = {}

    fallback = {}
    for name in SENSOR_NAMES:
        sensor_config = sensors.get(name, {})
        enabled = bool(sensor_config.get("enabled", False)) if isinstance(sensor_config, dict) else False
        fallback[name] = {
            "enabled": enabled,
            "status": "configured" if enabled else "disabled",
        }
    return fallback


def _response_message(payload: Any, default: str) -> str:
    if isinstance(payload, dict):
        for key in ("message", "last_message", "error"):
            value = payload.get(key)
            if value:
                return str(value)
    return default


def _resolve_mac_host(raspi_config: dict[str, Any]) -> str:
    configured_host = str(raspi_config.get("mac_host") or "").strip()
    if configured_host:
        return configured_host

    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return ""
