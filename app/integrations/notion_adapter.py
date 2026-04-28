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
    auto_retry_failed: bool,
    timeout_seconds: int,
    data_dir: Path,
) -> None:
    global _client, _config, _queue_path

    _client = None
    _config = {
        "enabled": bool(enabled),
        "api_key": api_key or "",
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
    config_data: dict[str, Any] = None,
    is_retry: bool = False,
) -> dict[str, Any]:
    """Upload one completed study session to Notion. Returns {ok, queued?, error?}."""
    if config_data is None:
        config_data = {}

    if is_retry:
        config_data = _refresh_config_for_retry(result_payload, config_data)

    study_settings = config_data.get("study_settings", {})
    if not study_settings.get("notion_enabled"):
        return {"ok": False, "skipped": True, "error": "Notion is disabled for this study."}

    if _client is None:
        error_message = "Notion client is not ready on the server."
        if _config.get("enabled") and not is_retry:
            _enqueue(result_payload, hardware_config, saved_output, config_data)
            return {"ok": False, "queued": True, "error": error_message}
        return {"ok": False, "skipped": True, "error": error_message}

    try:
        db_id = _ensure_database(study_settings, config_data)
        page_id = _find_or_create_participant(db_id, result_payload, study_settings, config_data)
        session_num = _get_session_count(page_id) + 1
        _append_session_block(page_id, session_num, result_payload, hardware_config, saved_output)
        _update_participant_properties(page_id, session_num, result_payload)
        pid_short = str(result_payload.get("participant_id") or "?")[:8]
        print(f"[NOTION] Uploaded session {session_num} for participant {pid_short}…")
        return {"ok": True}
    except Exception as error:
        print(f"[NOTION] Upload failed{' (retry)' if is_retry else ', queuing'}: {error}")
        if not is_retry:
            _enqueue(result_payload, hardware_config, saved_output, config_data)
        return {"ok": False, "queued": True, "error": str(error)}


def flush_queue() -> dict[str, Any]:
    """Retry all queued uploads. Returns attempt stats."""
    return _try_flush_queue()


def test_connection(
    *,
    api_key: str,
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
        "queue_size": queue_size,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _ensure_database(study_settings: dict[str, Any], config_data: dict[str, Any]) -> str:
    db_id = study_settings.get("notion_database_id", "")
    if db_id:
        return _strip_dashes(db_id)

    parent_page_id = _strip_dashes(study_settings.get("notion_parent_page_id", ""))
    if not parent_page_id:
        raise RuntimeError("Notion parent_page_id is required in study_settings to auto-create a Notion database.")

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
    study_settings["notion_database_id"] = new_id
    
    if hasattr(_client, "data_sources"):
        data_sources = db.get("data_sources", [])
        if data_sources:
            study_settings["notion_data_source_id"] = data_sources[0]["id"]
            
    _persist_study_database_id(config_data)
    print(f"[NOTION] Auto-created database: {new_id}")
    return new_id

def _get_data_source_id(db_id: str, study_settings: dict[str, Any], config_data: dict[str, Any]) -> str:
    if not hasattr(_client, "data_sources"):
        return db_id
    
    cached_ds = study_settings.get("notion_data_source_id")
    if cached_ds:
        return cached_ds
        
    try:
        db = _client.databases.retrieve(database_id=db_id)
        data_sources = db.get("data_sources", [])
        if data_sources:
            ds_id = data_sources[0]["id"]
            study_settings["notion_data_source_id"] = ds_id
            _persist_study_database_id(config_data)
            return ds_id
    except Exception as e:
        print(f"[NOTION] Could not retrieve data source: {e}")
        
    return db_id

def _find_or_create_participant(db_id: str, result_payload: dict[str, Any], study_settings: dict[str, Any], config_data: dict[str, Any]) -> str:
    participant_id = str(result_payload.get("participant_id") or "unknown")
    session_date = _session_date_iso(result_payload)

    ds_id = _get_data_source_id(db_id, study_settings, config_data)

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
    answer_details = result_payload.get("answer_details") or []
    if isinstance(answer_details, list) and answer_details:
        return _format_answer_details(answer_details)

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


def _format_answer_details(answer_details: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for detail in answer_details:
        question_number = detail.get("question_number")
        question_type = detail.get("question_type") or "question"
        prompt = str(detail.get("question_prompt") or "").replace("\n", " ").strip()
        answer_text = _format_answer_value(detail.get("answer"))
        delta = detail.get("seconds_since_previous_answer")
        delta_text = f"{delta:.1f}s" if isinstance(delta, (int, float)) else "n/a"
        biomarker_text = _format_interval_biomarkers(detail.get("biosignal_interval") or {})
        lines.append(
            f"Q{question_number} [{question_type}] {prompt or '(ohne Prompt)'} | Antwort: {answer_text} | Seit letzter Antwort: {delta_text} | Biomarker: {biomarker_text}"
        )
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


def _format_interval_biomarkers(interval_summary: dict[str, Any]) -> str:
    parts: list[str] = []

    brainbit = interval_summary.get("brainbit") or {}
    if brainbit.get("available"):
        parts.append(
            "BrainBit "
            f"att={_fmt_metric(brainbit.get('avg_attention'))}, "
            f"rel={_fmt_metric(brainbit.get('avg_relaxation'))}, "
            f"alpha={_fmt_metric(brainbit.get('avg_alpha'))}, "
            f"beta={_fmt_metric(brainbit.get('avg_beta'))}"
        )
    else:
        parts.append("BrainBit n/a")

    radar = interval_summary.get("mini_radar") or {}
    if radar.get("available"):
        parts.append(
            "Radar "
            f"hr={_fmt_metric(radar.get('avg_heart_rate'))}, "
            f"br={_fmt_metric(radar.get('avg_breath_rate'))}, "
            f"q={_fmt_metric(radar.get('avg_quality'))}"
        )
    else:
        parts.append("Radar n/a")

    camera = interval_summary.get("camera_emotion") or {}
    if camera.get("available"):
        parts.append(
            "Camera "
            f"emotion={camera.get('dominant_emotion') or 'n/a'}, "
            f"face={_fmt_metric(camera.get('avg_face_confidence'))}, "
            f"conf={_fmt_metric(camera.get('avg_emotion_confidence'))}"
        )
    else:
        parts.append("Camera n/a")

    return " | ".join(parts)


def _format_answer_value(value: Any) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{key}={val}" for key, val in value.items()) or "n/a"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "n/a"
    if value in (None, ""):
        return "n/a"
    return str(value)


def _fmt_metric(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, (int, float)):
        return f"{value:.2f}"
    return str(value)


def _enqueue(
    result_payload: dict[str, Any],
    hardware_config: dict[str, Any],
    saved_output: dict[str, Any],
    config_data: dict[str, Any],
) -> None:
    if not _queue_path:
        return
    entry = {
        "result_payload": result_payload,
        "hardware_config": hardware_config,
        "saved_output": saved_output,
        "config_data": config_data,
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
            config_data=entry.get("config_data", {}),
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


def _refresh_config_for_retry(
    result_payload: dict[str, Any],
    queued_config_data: dict[str, Any],
) -> dict[str, Any]:
    study_id = str(
        result_payload.get("study_id")
        or queued_config_data.get("study_id")
        or ""
    ).strip()
    if not study_id:
        return queued_config_data

    try:
        from flask import current_app
        from ..config_service import load_config, load_study

        if current_app:
            config_file = current_app.config["CONFIG_FILE"]
            studies_dir = current_app.config["BASE_DIR"] / "studies"
        else:
            raise RuntimeError("No active app context.")

        current_config = load_config(config_file)
        if str(current_config.get("study_id") or "").strip() == study_id:
            return current_config

        return load_study(studies_dir, study_id)
    except Exception:
        try:
            from ..config_service import load_config, load_study

            if not _queue_path:
                return queued_config_data

            base_dir = _queue_path.parent.parent
            config_file = base_dir / "study_config.json"
            studies_dir = base_dir / "studies"
            current_config = load_config(config_file)
            if str(current_config.get("study_id") or "").strip() == study_id:
                return current_config
            return load_study(studies_dir, study_id)
        except Exception:
            return queued_config_data


def _persist_study_database_id(config_data: dict[str, Any]) -> None:
    try:
        from flask import current_app
        from ..config_service import save_study, save_config
        save_config(current_app.config["CONFIG_FILE"], config_data)
        studies_dir = current_app.config["BASE_DIR"] / "studies"
        save_study(studies_dir, config_data)
        print(f"[NOTION] Persisted database_id to study config.")
    except Exception as error:
        print(f"[NOTION] Could not persist database_id to study config: {error}")


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
