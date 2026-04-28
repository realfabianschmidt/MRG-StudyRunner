"""
Notion adapter — uploads anonymized study results to a Notion database.

Each participant gets one database page identified by their pseudonymized hash ID.
Each completed study session is appended as a toggle block to that page.

If the Notion API is unreachable, the payload is written to a local JSONL queue
file and re-attempted on the next server start or via the admin flush endpoint.

Requires: notion-client  (auto-install optional)
Enable:   set "notion": { "enabled": true, ... } in hardware_config.json
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .dependency_utils import ensure_requirements


_client: Any = None
_config: dict[str, Any] = {}
_queue_path: Path | None = None


def initialize(
    *,
    enabled: bool,
    api_key: str,
    parent_page_id: str,
    database_id: str,
    auto_create_database: bool,
    auto_retry_failed: bool,
    timeout_seconds: int,
    data_dir: Path,
) -> None:
    global _client, _config, _queue_path

    _config = {
        "enabled": bool(enabled),
        "api_key": api_key or "",
        "parent_page_id": _strip_dashes(parent_page_id or ""),
        "database_id": _strip_dashes(database_id or ""),
        "data_source_id": "",
        "auto_create_database": bool(auto_create_database),
        "auto_retry_failed": bool(auto_retry_failed),
        "timeout_seconds": max(1, int(timeout_seconds)),
    }

    _queue_path = Path(data_dir) / "notion_upload_queue.jsonl"

    if not enabled:
        print("[NOTION] Disabled.")
        return

    if not api_key:
        print("[NOTION] No API key configured — upload disabled.")
        return

    if not ensure_requirements(
        [("notion_client", "notion-client")],
        auto_install=True,
        label="NOTION",
    ):
        return

    try:
        from notion_client import Client
        _client = Client(
            auth=api_key,
            timeout_ms=_config["timeout_seconds"] * 1000,
        )
        print("[NOTION] Client ready.")
    except Exception as error:
        print(f"[NOTION] Client initialization failed: {error}")
        return

    if auto_retry_failed and _queue_path and _queue_path.exists():
        _try_flush_queue()


def upload_study_result(
    result_payload: dict[str, Any],
    hardware_config: dict[str, Any],
    saved_output: dict[str, Any],
    is_retry: bool = False,
) -> dict[str, Any]:
    """Upload one completed study session to Notion. Returns {ok, queued?, error?}."""
    if not _config.get("enabled") or _client is None:
        return {"ok": False, "error": "Notion not configured or disabled."}

    try:
        db_id = _ensure_database()
        page_id = _find_or_create_participant(db_id, result_payload)
        session_num = _get_session_count(page_id) + 1
        _append_session_block(page_id, session_num, result_payload, hardware_config, saved_output)
        _update_participant_properties(page_id, session_num, result_payload)
        pid_short = str(result_payload.get("participant_id") or "?")[:8]
        print(f"[NOTION] Uploaded session {session_num} for participant {pid_short}…")
        return {"ok": True}
    except Exception as error:
        print(f"[NOTION] Upload failed{' (retry)' if is_retry else ', queuing'}: {error}")
        if not is_retry:
            _enqueue(result_payload, hardware_config, saved_output)
        return {"ok": False, "queued": True, "error": str(error)}


def flush_queue() -> dict[str, Any]:
    """Retry all queued uploads. Returns attempt stats."""
    return _try_flush_queue()


def test_connection(
    *,
    api_key: str,
    parent_page_id: str,
    database_id: str,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    """
    Test Notion connectivity with the given credentials (without saving anything).
    Returns a list of named checks so the UI can show granular status.
    """
    if not ensure_requirements(
        [("notion_client", "notion-client")],
        auto_install=True,
        label="NOTION",
    ):
        return {"ok": False, "checks": [{"name": "Paket", "ok": False, "message": "notion-client konnte nicht installiert werden."}]}

    from notion_client import Client, APIErrorCode, APIResponseError

    checks: list[dict[str, Any]] = []

    # 1. API-Key validieren (eigene Bot-Info abrufen)
    try:
        client = Client(auth=api_key.strip(), timeout_ms=timeout_seconds * 1000)
        me = client.users.me()
        bot_name = me.get("name") or me.get("bot", {}).get("owner", {}).get("user", {}).get("name") or "Integration"
        checks.append({"name": "API Key", "ok": True, "message": f'Verbunden als „{bot_name}"'})
    except APIResponseError as error:
        msg = "Ungültiger API Key." if error.code == APIErrorCode.Unauthorized else str(error)
        checks.append({"name": "API Key", "ok": False, "message": msg})
        return {"ok": False, "checks": checks}
    except Exception as error:
        checks.append({"name": "API Key", "ok": False, "message": f"Verbindung fehlgeschlagen: {error}"})
        return {"ok": False, "checks": checks}

    # 2. Parent Page prüfen (falls angegeben)
    clean_parent = _strip_dashes(parent_page_id or "")
    if clean_parent:
        try:
            page = client.pages.retrieve(page_id=clean_parent)
            title_prop = page.get("properties", {}).get("title", {})
            title_parts = title_prop.get("title", [])
            page_title = title_parts[0].get("plain_text", clean_parent[:8]) if title_parts else clean_parent[:8]
            checks.append({"name": "Parent Page", "ok": True, "message": f'Seite „{page_title}" erreichbar'})
        except APIResponseError as error:
            if error.code == APIErrorCode.ObjectNotFound:
                msg = "Seite nicht gefunden — Integration eingeladen? (Share → Add connections)"
            elif error.code == APIErrorCode.Unauthorized:
                msg = "Kein Zugriff auf diese Seite."
            else:
                msg = str(error)
            checks.append({"name": "Parent Page", "ok": False, "message": msg})
        except Exception as error:
            checks.append({"name": "Parent Page", "ok": False, "message": str(error)})
    else:
        checks.append({"name": "Parent Page", "ok": None, "message": "Nicht angegeben — wird für Auto-Create benötigt"})  # type: ignore[dict-item]

    # 3. Database ID prüfen (falls angegeben)
    clean_db = _strip_dashes(database_id or "")
    if clean_db:
        try:
            db = client.databases.retrieve(database_id=clean_db)
            db_title_parts = db.get("title", [])
            db_title = db_title_parts[0].get("plain_text", clean_db[:8]) if db_title_parts else clean_db[:8]
            # Check required properties
            if hasattr(client, "data_sources"):
                data_sources = db.get("data_sources", [])
                if data_sources:
                    ds_id = data_sources[0]["id"]
                    ds = client.data_sources.retrieve(data_source_id=ds_id)
                    props = ds.get("properties", {})
                else:
                    props = db.get("properties", {})
            else:
                props = db.get("properties", {})
            missing = [p for p in ("Participant ID", "Study Count", "First Session", "Last Session") if p not in props]
            if missing:
                checks.append({"name": "Datenbank", "ok": False,
                               "message": f'„{db_title}" erreichbar, aber fehlende Spalten: {", ".join(missing)}'})
            else:
                checks.append({"name": "Datenbank", "ok": True, "message": f'„{db_title}" erreichbar, Schema korrekt'})
        except APIResponseError as error:
            if error.code == APIErrorCode.ObjectNotFound:
                msg = "Datenbank nicht gefunden — Integration eingeladen?"
            else:
                msg = str(error)
            checks.append({"name": "Datenbank", "ok": False, "message": msg})
        except Exception as error:
            checks.append({"name": "Datenbank", "ok": False, "message": str(error)})
    else:
        checks.append({"name": "Datenbank", "ok": None, "message": "Nicht angegeben — wird beim ersten Upload auto-erstellt"})  # type: ignore[dict-item]

    overall_ok = all(c["ok"] is not False for c in checks)
    return {"ok": overall_ok, "checks": checks}


def get_status() -> dict[str, Any]:
    queue_size = 0
    if _queue_path and _queue_path.exists():
        try:
            with _queue_path.open(encoding="utf-8") as fh:
                queue_size = sum(1 for line in fh if line.strip())
        except OSError:
            pass
    return {
        "enabled": bool(_config.get("enabled")),
        "connected": _client is not None,
        "database_id": _config.get("database_id", ""),
        "queue_size": queue_size,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _ensure_database() -> str:
    db_id = _config.get("database_id", "")
    if db_id:
        return db_id

    if not _config.get("auto_create_database"):
        raise RuntimeError("No database_id configured and auto_create_database is disabled.")

    parent_page_id = _config.get("parent_page_id", "")
    if not parent_page_id:
        raise RuntimeError("parent_page_id is required to auto-create a Notion database.")

    db_args = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": "StudyRunner Participants"}}],
    }

    schema = {
        "Participant ID": {"title": {}},
        "Study Count": {"number": {"format": "number"}},
        "First Session": {"date": {}},
        "Last Session": {"date": {}},
    }

    if hasattr(_client, "data_sources"):
        db_args["initial_data_source"] = {"properties": schema}
    else:
        db_args["properties"] = schema

    db = _client.databases.create(**db_args)

    new_id = _strip_dashes(db["id"])
    _config["database_id"] = new_id
    
    if hasattr(_client, "data_sources"):
        data_sources = db.get("data_sources", [])
        if data_sources:
            _config["data_source_id"] = data_sources[0]["id"]
            
    _persist_database_id(new_id)
    print(f"[NOTION] Auto-created database: {new_id}")
    return new_id

def _get_data_source_id(db_id: str) -> str:
    if not hasattr(_client, "data_sources"):
        return db_id
    
    cached_ds = _config.get("data_source_id")
    if cached_ds:
        return cached_ds
        
    try:
        db = _client.databases.retrieve(database_id=db_id)
        data_sources = db.get("data_sources", [])
        if data_sources:
            ds_id = data_sources[0]["id"]
            _config["data_source_id"] = ds_id
            return ds_id
    except Exception as e:
        print(f"[NOTION] Could not retrieve data source: {e}")
        
    return db_id

def _find_or_create_participant(db_id: str, result_payload: dict[str, Any]) -> str:
    participant_id = str(result_payload.get("participant_id") or "unknown")
    session_date = _session_date_iso(result_payload)

    ds_id = _get_data_source_id(db_id)

    if hasattr(_client, "data_sources"):
        results = _client.data_sources.query(
            data_source_id=ds_id,
            filter={"property": "Participant ID", "title": {"equals": participant_id}},
        )
        parent_obj = {"type": "data_source_id", "data_source_id": ds_id}
    else:
        results = _client.databases.query(
            database_id=ds_id,
            filter={"property": "Participant ID", "title": {"equals": participant_id}},
        )
        parent_obj = {"database_id": ds_id}

    if results.get("results"):
        return results["results"][0]["id"]

    page = _client.pages.create(
        parent=parent_obj,
        properties={
            "Participant ID": {"title": [{"text": {"content": participant_id}}]},
            "Study Count": {"number": 0},
            "First Session": {"date": {"start": session_date}},
            "Last Session": {"date": {"start": session_date}},
        },
    )
    return page["id"]


def _get_session_count(page_id: str) -> int:
    try:
        page = _client.pages.retrieve(page_id=page_id)
        count_prop = page.get("properties", {}).get("Study Count", {})
        return int(count_prop.get("number") or 0)
    except Exception:
        return 0


def _update_participant_properties(
    page_id: str, session_num: int, result_payload: dict[str, Any]
) -> None:
    _client.pages.update(
        page_id=page_id,
        properties={
            "Study Count": {"number": session_num},
            "Last Session": {"date": {"start": _session_date_iso(result_payload)}},
        },
    )


def _append_session_block(
    page_id: str,
    session_num: int,
    result_payload: dict[str, Any],
    hardware_config: dict[str, Any],
    saved_output: dict[str, Any],
) -> None:
    study_id = str(result_payload.get("study_id") or "—")
    session_date = _session_date_iso(result_payload)
    ts_start = str(result_payload.get("timestamp_start") or "")
    ts_end = str(result_payload.get("timestamp_end") or "")

    toggle_title = f"Session {session_num} · {study_id} · {session_date}"
    answer_lines = _format_answers(result_payload)
    biosignal_lines = _format_biosignals(hardware_config, saved_output)

    children: list[dict[str, Any]] = [
        _paragraph(f"Dauer: {ts_start[:16]} → {ts_end[:16]} ({_duration_minutes(ts_start, ts_end)} min)"),
        _heading("Antworten"),
        *[_bullet(line) for line in (answer_lines or ["(keine Antworten)"])],
        _heading("Biosignale"),
        *[_bullet(line) for line in (biosignal_lines or ["(keine Sensoren aktiv)"])],
    ]

    # 1. Toggle-Block erstellen (ohne nested children, um Notion-Limits zu umgehen)
    response = _client.blocks.children.append(
        block_id=page_id,
        children=[{
            "object": "block",
            "type": "toggle",
            "toggle": {
                "rich_text": [{"type": "text", "text": {"content": _truncate(toggle_title)}}],
            },
        }],
    )

    toggle_id = response["results"][0]["id"]

    # 2. Antworten und Biosignale sicher in 100er-Blöcken in den Toggle einfügen
    for i in range(0, len(children), 100):
        _client.blocks.children.append(
            block_id=toggle_id,
            children=children[i:i+100]
        )


def _format_answers(result_payload: dict[str, Any]) -> list[str]:
    answers = result_payload.get("answers") or {}
    lines = []
    for key, value in sorted(answers.items()):
        if value is None:
            continue
        if isinstance(value, list):
            formatted = " → ".join(str(v) for v in value)
        else:
            formatted = str(value)
        lines.append(f"{key}: {formatted}")
    return lines


def _format_biosignals(hardware_config: dict[str, Any], saved_output: dict[str, Any]) -> list[str]:
    lines = []
    bio = saved_output.get("biosignal_summary") or {}

    brainbit = bio.get("brainbit") or {}
    if brainbit.get("active"):
        xdf = brainbit.get("xdf_path") or "—"
        lines.append(f"BrainBit EEG: aktiv | Rohdaten: {xdf}")
    elif hardware_config.get("brainbit", {}).get("enabled"):
        lines.append("BrainBit EEG: konfiguriert (kein XDF dieser Session)")

    radar = bio.get("mini_radar") or {}
    if radar.get("active"):
        lines.append("Mini-Radar: aktiv")
    elif hardware_config.get("mini_radar", {}).get("enabled"):
        lines.append("Mini-Radar: konfiguriert")

    cam = bio.get("camera_emotion") or {}
    if cam.get("active"):
        lines.append("Camera Emotion: aktiv")
    elif hardware_config.get("camera_emotion", {}).get("enabled"):
        lines.append("Camera Emotion: konfiguriert")

    return lines


def _enqueue(
    result_payload: dict[str, Any],
    hardware_config: dict[str, Any],
    saved_output: dict[str, Any],
) -> None:
    if not _queue_path:
        return
    entry = {
        "result_payload": result_payload,
        "hardware_config": hardware_config,
        "saved_output": saved_output,
        "queued_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        with _queue_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as error:
        print(f"[NOTION] Could not write to queue: {error}")


def _try_flush_queue() -> dict[str, Any]:
    if not _queue_path or not _queue_path.exists():
        return {"attempted": 0, "succeeded": 0, "remaining": 0}

    try:
        with _queue_path.open(encoding="utf-8") as fh:
            entries = [json.loads(line) for line in fh if line.strip()]
    except OSError as error:
        return {"attempted": 0, "succeeded": 0, "remaining": 0, "error": str(error)}

    if _client is None:
        return {
            "attempted": len(entries),
            "succeeded": 0,
            "remaining": len(entries),
            "last_error": "Notion-Client ist nicht verbunden. Hast du das Terminal (den Server) nach dem Speichern der API-Daten neu gestartet?"
        }

    succeeded = 0
    remaining = []
    last_error = None
    for entry in entries:
        result = upload_study_result(
            entry["result_payload"],
            entry.get("hardware_config", {}),
            entry.get("saved_output", {}),
            is_retry=True,
        )
        if result.get("ok"):
            succeeded += 1
        else:
            remaining.append(entry)
            last_error = result.get("error", "Unknown error")

    try:
        if remaining:
            with _queue_path.open("w", encoding="utf-8") as fh:
                for entry in remaining:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        else:
            _queue_path.unlink(missing_ok=True)
    except OSError as error:
        print(f"[NOTION] Could not rewrite queue: {error}")

    print(f"[NOTION] Queue flush: {succeeded}/{len(entries)} succeeded, {len(remaining)} remaining.")
    return {"attempted": len(entries), "succeeded": succeeded, "remaining": len(remaining), "last_error": last_error}


def _persist_database_id(db_id: str) -> None:
    config_path = Path(__file__).resolve().parent.parent.parent / "hardware_config.json"
    try:
        raw = config_path.read_text(encoding="utf-8")
        config = json.loads(raw)
        config.setdefault("notion", {})["database_id"] = db_id
        tmp = config_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(config, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        tmp.replace(config_path)
        print(f"[NOTION] Persisted database_id to hardware_config.json.")
    except Exception as error:
        print(f"[NOTION] Could not persist database_id: {error}")


def _strip_dashes(value: str) -> str:
    return value.replace("-", "").strip()


def _session_date_iso(result_payload: dict[str, Any]) -> str:
    ts = result_payload.get("timestamp_start") or ""
    return ts[:10] if ts else time.strftime("%Y-%m-%d")


def _duration_minutes(ts_start: str, ts_end: str) -> str:
    try:
        import datetime
        t0 = datetime.datetime.fromisoformat(ts_start.replace("Z", "+00:00"))
        t1 = datetime.datetime.fromisoformat(ts_end.replace("Z", "+00:00"))
        return str(int((t1 - t0).total_seconds() / 60))
    except Exception:
        return "?"


def _truncate(text: str, max_len: int = 2000) -> str:
    text = str(text)
    if not text:
        return "—"
    return text if len(text) <= max_len else text[:max_len-3] + "..."


def _paragraph(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": _truncate(text)}}]},
    }


def _bullet(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": _truncate(text)}}]},
    }


def _heading(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "heading_3",
        "heading_3": {"rich_text": [{"type": "text", "text": {"content": _truncate(text)}}]},
    }
