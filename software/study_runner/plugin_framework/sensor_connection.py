"""One connection pattern for every sensor plugin.

A sensor plugin reports facts only, in a ``connection`` block of its status:

    phase      off | starting | idle | searching | selection_required
               | connecting | connected | reconnecting | failed
               (idle = switched on and ready to connect; nothing runs by itself)
    device     {"id", "label"} or None
    candidates [{"id", "label", "payload"}]   (devices the operator can pick,
               for example the one used last time)
    signal     {"state": good | fair | poor | measuring | stale | unknown,
                "channels": {...}, "measured_at": "..."}
    setup      {"state": needed | running | done | stalled | not_needed,
                "progress_percent": 0..100}
    streaming  True while data is arriving
    detail     optional reason for idle/failed, e.g. not_found, connection_lost
    auto_reconnect {"enabled": bool, "had_connection": bool}   (optional)

The core then decides, the same way for every sensor:

    ready      the sensor delivers usable data and a participant can start
    next_step  the one action the operator should take now (the call to
               action in the dashboard): scan, select, measure_signal,
               initialize -- or None while waiting or when ready
    auto_reconnect.active
               whether a lost connection is restored without the operator.
               A sensor the operator connects by hand (scan/select) does this
               only while the study runs and after a device was connected, so
               nothing searches on its own while the operator sets up.

A plugin that reports no ``connection`` block gets one derived from its plain
``status`` and ``running`` fields, so the dashboard and the start check treat
it the same way without plugin changes. No function here names a plugin.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

PHASES = (
    "off",
    "starting",
    "idle",
    "searching",
    "selection_required",
    "connecting",
    "connected",
    "reconnecting",
    "failed",
)
SIGNAL_STATES = ("good", "fair", "poor", "measuring", "stale", "unknown")
SETUP_STATES = ("needed", "running", "done", "stalled", "not_needed")
ROLES = ("select", "scan", "measure_signal", "initialize", "auto_reconnect")
# Roles that mean the operator connects the device by hand.
_MANUAL_CONNECT_ROLES = frozenset({"scan", "select"})
# Earlier name of the idle phase, still accepted from plugins.
_PHASE_ALIASES = {"no_device": "idle"}

# Plain plugin statuses mapped onto phases for plugins without a block.
_CONNECTED_STATUSES = frozenset(
    {"connected", "receiving", "streaming", "ready", "recording", "active", "running", "no_presence"}
)
_SEARCHING_STATUSES = frozenset({"scanning", "searching"})
# "pending": the host has not heard from the freshly launched plugin yet.
_STARTING_STATUSES = frozenset({"starting", "pending"})
_CONNECTING_STATUSES = frozenset({"connecting", "waiting", "warming_up", "calibrating", "poor_contact"})
_FAILED_STATUSES = frozenset({"failed", "exited", "error", "unavailable"})
_OFF_STATUSES = frozenset({"stopped", "disabled", "off"})

# Signal states that stop a participant from starting.
_BLOCKING_SIGNALS = frozenset({"poor", "measuring", "stale"})


def action_roles(manifest: Mapping[str, Any] | None) -> dict[str, str]:
    """``{role: admin action key}`` declared in a normalized manifest."""

    config = ((manifest or {}).get("capability_config") or {}).get("admin_actions") or {}
    roles: dict[str, str] = {}
    for action in config.get("actions") or []:
        role = str((action or {}).get("role") or "")
        if role in ROLES and role not in roles:
            roles[role] = str(action.get("key") or "")
    return roles


def standardize_connection(
    raw_status: Mapping[str, Any],
    *,
    running: bool,
    roles: Iterable[str] = (),
    study_running: bool = False,
) -> dict[str, Any]:
    """Normalize a plugin's connection facts and add ``ready`` and ``next_step``."""

    role_set = {str(role) for role in roles if str(role) in ROLES}
    raw = raw_status.get("connection")
    if isinstance(raw, Mapping):
        connection = _normalize_block(raw)
    else:
        connection = _derived_block(raw_status, running=running)
    if not running:
        # A stopped plugin is off, whatever its last report said.
        connection["phase"] = "off"
        connection["streaming"] = False
    auto = connection.pop("auto_reconnect", None)
    if "auto_reconnect" in role_set:
        auto = auto if isinstance(auto, Mapping) else {}
        enabled = bool(auto.get("enabled", True))
        had_connection = bool(auto.get("had_connection", connection["phase"] in {"connected", "reconnecting"}))
        connection["auto_reconnect"] = {
            "enabled": enabled,
            "active": running and auto_reconnect_active(
                enabled=enabled,
                had_connection=had_connection,
                study_running=study_running,
                roles=role_set,
            ),
        }
    connection["ready"] = is_ready(connection, role_set)
    connection["next_step"] = next_step(connection, role_set)
    return connection


def auto_reconnect_active(
    *,
    enabled: bool,
    had_connection: bool,
    study_running: bool,
    roles: Iterable[str] = (),
) -> bool:
    """Whether a lost connection should be restored without the operator.

    Plugins call this too, so the dashboard and the plugin never disagree.
    """
    if not enabled or not had_connection:
        return False
    if _MANUAL_CONNECT_ROLES & set(roles):
        # Before the study runs the operator is setting up; a search running
        # by itself would take the buttons away from them.
        return bool(study_running)
    return True


def is_ready(connection: Mapping[str, Any], roles: Iterable[str] = ()) -> bool:
    role_set = set(roles)
    if connection.get("phase") != "connected" or not connection.get("streaming"):
        return False
    signal = str((connection.get("signal") or {}).get("state") or "unknown")
    if signal in _BLOCKING_SIGNALS:
        return False
    if signal == "unknown" and "measure_signal" in role_set:
        # The plugin can measure its signal but has not yet.
        return False
    setup = str((connection.get("setup") or {}).get("state") or "not_needed")
    return setup in {"done", "not_needed"}


def next_step(connection: Mapping[str, Any], roles: Iterable[str] = ()) -> str | None:
    """The one action that moves this sensor towards ready, if it has it."""

    role_set = set(roles)
    phase = _PHASE_ALIASES.get(str(connection.get("phase") or ""), connection.get("phase"))
    if phase in {"idle", "failed"}:
        # A device in the list (the one used last time, or a search result)
        # is one click away; otherwise search.
        if connection.get("candidates") and "select" in role_set:
            return "select"
        return "scan" if "scan" in role_set else None
    if phase == "selection_required":
        return "select" if "select" in role_set else None
    if phase != "connected":
        # off: the switch is the action; starting/searching/connecting/
        # reconnecting: wait.
        return None
    signal = str((connection.get("signal") or {}).get("state") or "unknown")
    if signal == "measuring":
        return None
    if signal in {"poor", "stale"} or (signal == "unknown" and "measure_signal" in role_set):
        return "measure_signal" if "measure_signal" in role_set else None
    setup = str((connection.get("setup") or {}).get("state") or "not_needed")
    if setup in {"needed", "stalled"}:
        return "initialize" if "initialize" in role_set else None
    return None


def presence_sensor_connection(
    status: str,
    *,
    running: bool,
    message: str = "",
    auto_reconnect: bool | None = None,
    had_connection: bool = False,
    device_label: str = "",
) -> dict[str, Any]:
    """Connection facts for sensors that detect a person (radar, hub).

    They need no setup. Whether a person is detected is shown as the signal
    but never blocks the start: the participant may sit down afterwards.
    ``auto_reconnect`` is the operator's switch, for plugins that offer it.
    ``device_label`` names what it is connected to (address, transport, ID)
    for the tile's device bar.
    """
    value = str(status or "").strip().lower()
    if not running or value in _OFF_STATUSES:
        phase, streaming = "off", False
    elif value in _STARTING_STATUSES:
        phase, streaming = "starting", False
    elif value in _SEARCHING_STATUSES:
        phase, streaming = "searching", False
    elif value in {"connected", "no_presence"}:
        phase, streaming = "connected", True
    elif value == "stale":
        phase, streaming = "reconnecting", False
    elif value in _FAILED_STATUSES:
        phase, streaming = "failed", False
    else:
        phase, streaming = "connecting", False
    signal: dict[str, Any] = {"state": "unknown"}
    if value == "connected":
        signal = {"state": "good", "detail": "presence"}
    elif value == "no_presence":
        signal = {"state": "unknown", "detail": "no_presence"}
    label = str(device_label or "").strip()
    block: dict[str, Any] = {
        "phase": phase,
        "device": {"id": label, "label": label} if label else None,
        "candidates": [],
        "signal": signal,
        "setup": {"state": "not_needed"},
        "streaming": streaming,
        "message": str(message or ""),
    }
    if auto_reconnect is not None:
        block["auto_reconnect"] = {
            "enabled": bool(auto_reconnect),
            "had_connection": bool(had_connection or phase in {"connected", "reconnecting"}),
        }
    return block


def _normalize_block(raw: Mapping[str, Any]) -> dict[str, Any]:
    phase = str(raw.get("phase") or "connecting")
    phase = _PHASE_ALIASES.get(phase, phase)
    if phase not in PHASES:
        phase = "connecting"
    signal = raw.get("signal") if isinstance(raw.get("signal"), Mapping) else {}
    signal_state = str(signal.get("state") or "unknown")
    if signal_state not in SIGNAL_STATES:
        signal_state = "unknown"
    setup = raw.get("setup") if isinstance(raw.get("setup"), Mapping) else {}
    setup_state = str(setup.get("state") or "not_needed")
    if setup_state not in SETUP_STATES:
        setup_state = "not_needed"
    device = raw.get("device") if isinstance(raw.get("device"), Mapping) else None
    candidates = [
        {
            "id": str(item.get("id") or ""),
            "label": str(item.get("label") or item.get("id") or ""),
            "payload": dict(item.get("payload") or {}) if isinstance(item.get("payload"), Mapping) else {},
            # "last_used": offered from memory, not found by a search just now.
            **({"note": str(item["note"])} if item.get("note") else {}),
        }
        for item in raw.get("candidates") or []
        if isinstance(item, Mapping) and str(item.get("id") or "")
    ]
    normalized_signal: dict[str, Any] = {"state": signal_state}
    for key in ("channels", "measured_at", "detail"):
        if signal.get(key) not in (None, "", {}):
            normalized_signal[key] = signal[key]
    normalized_setup: dict[str, Any] = {"state": setup_state}
    progress = setup.get("progress_percent")
    if isinstance(progress, (int, float)) and not isinstance(progress, bool):
        normalized_setup["progress_percent"] = max(0, min(100, round(float(progress))))
    normalized: dict[str, Any] = {
        "phase": phase,
        "device": (
            {"id": str(device.get("id") or ""), "label": str(device.get("label") or device.get("id") or "")}
            if device
            else None
        ),
        "candidates": candidates,
        "signal": normalized_signal,
        "setup": normalized_setup,
        "streaming": bool(raw.get("streaming")),
        "message": str(raw.get("message") or ""),
    }
    if raw.get("detail"):
        normalized["detail"] = str(raw["detail"])
    if isinstance(raw.get("auto_reconnect"), Mapping):
        normalized["auto_reconnect"] = dict(raw["auto_reconnect"])
    return normalized


def _derived_block(raw_status: Mapping[str, Any], *, running: bool) -> dict[str, Any]:
    status = str(raw_status.get("status") or "").strip().lower()
    if not running or status in _OFF_STATUSES:
        phase = "off"
    elif status in _STARTING_STATUSES:
        phase = "starting"
    elif status in _CONNECTED_STATUSES:
        phase = "connected"
    elif status in _SEARCHING_STATUSES:
        phase = "searching"
    elif status in _FAILED_STATUSES:
        phase = "failed"
    else:
        phase = "connecting"
    return {
        "phase": phase,
        "device": None,
        "candidates": [],
        "signal": {"state": "unknown"},
        "setup": {"state": "not_needed"},
        "streaming": phase == "connected",
        "message": str(raw_status.get("last_message") or ""),
        "derived": True,
    }
