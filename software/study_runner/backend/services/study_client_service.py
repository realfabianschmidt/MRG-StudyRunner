import time
import uuid
from threading import Lock
from typing import Any


STALE_AFTER_SECONDS = 5.0
HIDE_AFTER_SECONDS = 15.0
DROP_AFTER_SECONDS = 60.0

_clients: dict[str, dict[str, Any]] = {}
_lock = Lock()


def register_heartbeat(payload: dict[str, Any], remote_addr: str | None, user_agent: str | None) -> dict[str, Any]:
    """Register that a study page is open and recently active."""
    now = time.time()
    client_id = str(payload.get("client_id") or uuid.uuid4()).strip()
    if not client_id:
        client_id = str(uuid.uuid4())

    client_state = {
        "client_id": client_id,
        "participant_id": str(payload.get("participant_id") or "").strip(),
        "study_id": str(payload.get("study_id") or "").strip(),
        "current_index": payload.get("current_index"),
        "current_type": payload.get("current_type"),
        "is_stimulus_active": bool(payload.get("is_stimulus_active", False)),
        "camera_permission": payload.get("camera_permission", "unknown"),
        "camera_monitor_requested": bool(payload.get("camera_monitor_requested", False)),
        "camera_monitor_active": bool(payload.get("camera_monitor_active", False)),
        "camera_last_error": str(payload.get("camera_last_error") or "").strip(),
        "study_started": bool(payload.get("study_started", False)),
        "study_run_status": str(payload.get("study_run_status") or "").strip(),
        "waiting_for_admin_start": bool(payload.get("waiting_for_admin_start", False)),
        "session_id": str(payload.get("session_id") or "").strip(),
        "client_captured_at": payload.get("client_timestamp"),
        "sequence_number": payload.get("sequence_number"),
        "remote_addr": remote_addr,
        "user_agent": user_agent or "",
        "last_seen": now,
        "last_seen_at": _format_time(now),
    }

    with _lock:
        _clients[client_id] = client_state
        _drop_old_clients(now)

    return {"client_id": client_id, "server_received_at": _format_time(now)}


def get_client_status(active_study_id: str = "", assigned_client_id: str = "") -> dict[str, Any]:
    """Return active or recently stale study clients for the admin dashboard."""
    now = time.time()
    with _lock:
        _drop_old_clients(now)
        clients = [_public_client_state(client, now) for client in _clients.values()]

    clients.sort(key=lambda client: client["age_seconds"])
    has_connected_client = any(client["age_seconds"] <= HIDE_AFTER_SECONDS for client in clients)
    return {
        "dashboard_available": has_connected_client,
        "clients": clients,
        "active_count": sum(1 for client in clients if client["status"] == "active"),
        "stale_count": sum(1 for client in clients if client["status"] == "stale"),
        "single_tablet": _single_tablet_state(clients, active_study_id, assigned_client_id),
    }


def _public_client_state(client: dict[str, Any], now: float) -> dict[str, Any]:
    age_seconds = max(0.0, now - float(client.get("last_seen", now)))
    status = "active" if age_seconds <= STALE_AFTER_SECONDS else "stale"
    return {
        "client_id": client.get("client_id"),
        "participant_id": client.get("participant_id"),
        "study_id": client.get("study_id"),
        "current_index": client.get("current_index"),
        "current_type": client.get("current_type"),
        "is_stimulus_active": client.get("is_stimulus_active", False),
        "camera_permission": client.get("camera_permission", "unknown"),
        "camera_monitor_requested": client.get("camera_monitor_requested", False),
        "camera_monitor_active": client.get("camera_monitor_active", False),
        "camera_last_error": client.get("camera_last_error", ""),
        "study_started": client.get("study_started", False),
        "study_run_status": client.get("study_run_status", ""),
        "waiting_for_admin_start": client.get("waiting_for_admin_start", False),
        "session_id": client.get("session_id", ""),
        "client_captured_at": client.get("client_captured_at"),
        "server_received_at": client.get("last_seen_at"),
        "sequence_number": client.get("sequence_number"),
        "age_seconds": round(age_seconds, 2),
        "status": status,
        "remote_addr": client.get("remote_addr"),
        "user_agent": client.get("user_agent", ""),
    }


def reset_client_status() -> None:
    """Clear in-memory tablet heartbeat state.

    Heartbeats are process-local runtime signals, so a fresh Flask app/process
    starts without any remembered tablet clients.
    """
    with _lock:
        _clients.clear()


def _drop_old_clients(now: float) -> None:
    old_client_ids = [
        client_id
        for client_id, client in _clients.items()
        if (now - float(client.get("last_seen", now))) > DROP_AFTER_SECONDS
    ]
    for client_id in old_client_ids:
        _clients.pop(client_id, None)


def _single_tablet_state(clients: list[dict[str, Any]], active_study_id: str, assigned_client_id: str) -> dict[str, Any]:
    study_id = str(active_study_id or "").strip()
    assigned = str(assigned_client_id or "").strip()
    active_clients = [client for client in clients if client.get("status") == "active"]
    study_clients = [
        client
        for client in active_clients
        if not study_id or str(client.get("study_id") or "").strip() == study_id
    ]
    selected = None
    if assigned:
        selected = next((client for client in active_clients if client.get("client_id") == assigned), None)
    elif len(study_clients) == 1:
        selected = study_clients[0]

    conflict_clients = [
        client
        for client in study_clients
        if not selected or client.get("client_id") != selected.get("client_id")
    ]

    if assigned and selected is None:
        status = "assigned_missing"
        can_start = False
    elif len(study_clients) == 0:
        status = "waiting_for_tablet"
        can_start = False
    elif len(study_clients) > 1 and not assigned:
        status = "conflict"
        can_start = False
    elif conflict_clients:
        status = "conflict"
        can_start = False
    else:
        status = "ready"
        can_start = selected is not None

    return {
        "status": status,
        "can_start": bool(can_start),
        "active_client_count": len(active_clients),
        "active_study_client_count": len(study_clients),
        "assigned_client_id": assigned,
        "selected_client_id": selected.get("client_id") if selected else "",
        "conflict_client_ids": [client.get("client_id") for client in conflict_clients],
    }


def _format_time(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
