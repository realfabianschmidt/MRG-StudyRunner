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


def get_client_status() -> dict[str, Any]:
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
        "client_captured_at": client.get("client_captured_at"),
        "server_received_at": client.get("last_seen_at"),
        "sequence_number": client.get("sequence_number"),
        "age_seconds": round(age_seconds, 2),
        "status": status,
        "remote_addr": client.get("remote_addr"),
        "user_agent": client.get("user_agent", ""),
    }


def _drop_old_clients(now: float) -> None:
    old_client_ids = [
        client_id
        for client_id, client in _clients.items()
        if (now - float(client.get("last_seen", now))) > DROP_AFTER_SECONDS
    ]
    for client_id in old_client_ids:
        _clients.pop(client_id, None)


def _format_time(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
