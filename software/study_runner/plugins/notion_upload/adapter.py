"""
Notion adapter - uploads anonymized study results to a Notion database.

Each participant gets one database page identified by their pseudonymized hash ID.
Each completed study session is appended as a toggle block to that page.

Network failures are returned to the central upload-job service, which owns the
persistent retry journal for every destination.

Requires: notion-client  (auto-install optional)
Enable:   set "notion": { "enabled": true, ... } in study_content/settings/hardware_settings.json
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
import re
import time
from pathlib import Path
from typing import Any

from study_runner.plugin_framework.dependency_utils import ensure_requirements
from study_runner.backend.services.validation import PARTICIPANT_FIELD_ORDER


# Clients are cached by a hash of their API key, not by study id: two studies
# sharing one key share one client, renaming a study cannot orphan anything,
# and no second plaintext copy of the key sits around as a dict key.
_clients: dict[str, Any] = {}
_config: dict[str, Any] = {}


def _client_cache_key(api_key: str) -> str:
    return hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()[:16]


def get_client(api_key: str) -> Any:
    """Return a cached Notion client for this key, building it on first use.

    Returns None when the integration is off machine-side, when no key was
    given, or when the client library is unavailable - callers treat that as
    "not ready" exactly as they did with the old module-level client.
    """
    if not _config.get("enabled") or not api_key:
        return None

    cache_key = _client_cache_key(api_key)
    if cache_key in _clients:
        return _clients[cache_key]

    if not ensure_requirements(
        [("notion_client", "notion-client")],
        auto_install=True,
        label="NOTION",
    ):
        return None

    try:
        from notion_client import Client

        client = Client(auth=api_key, timeout_ms=_config.get("timeout_seconds", 10) * 1000)
    except Exception as error:
        print(f"[NOTION] Client initialization failed: {error}")
        return None

    _clients[cache_key] = client
    return client

PARTICIPANT_NOTION_PROPERTIES = {
    "first_name": ("First Name", "rich_text"),
    "last_name": ("Last Name", "rich_text"),
    "age_group": ("Age Group", "select"),
    "gender": ("Gender", "select"),
    "childhood_area": ("Childhood Area", "select"),
    "childhood_nearest_city": ("Childhood Nearest City", "rich_text"),
    "birth_place": ("Birth Place", "rich_text"),
    "birth_date": ("Birth Date", "date"),
}


def initialize(
    *,
    enabled: bool,
    api_key: str,
    auto_retry_failed: bool,
    timeout_seconds: int,
    data_dir: Path,
) -> None:
    global _config

    # Re-initializing drops every cached client, which is what the
    # hardware-config save path relies on to pick up a changed key.
    _clients.clear()
    _config = {
        "enabled": bool(enabled),
        "api_key": api_key or "",
        "auto_retry_failed": bool(auto_retry_failed),
        "timeout_seconds": max(1, int(timeout_seconds)),
    }

    if not enabled:
        print("[NOTION] Disabled.")
        return

    if not api_key:
        print("[NOTION] No API key configured - upload disabled.")
        return

    if not ensure_requirements(
        [("notion_client", "notion-client")],
        auto_install=True,
        label="NOTION",
    ):
        return

    if get_client(api_key) is not None:
        print("[NOTION] Client ready.")

def upload_study_result(
    result_payload: dict[str, Any],
    hardware_config: dict[str, Any],
    saved_output: dict[str, Any],
    config_data: dict[str, Any] = None,
    is_retry: bool = False,
) -> dict[str, Any]:
    """Upload one completed study session; retry persistence lives elsewhere."""
    if config_data is None:
        config_data = {}

    if is_retry:
        config_data = _refresh_config_for_retry(result_payload, config_data)

    study_settings = config_data.get("study_settings", {})
    if not study_settings.get("notion_enabled"):
        return {"ok": False, "skipped": True, "error": "Notion is disabled for this study."}

    canonical_summary_error = _canonical_card_summary_error(result_payload, saved_output)
    if canonical_summary_error:
        return {"ok": False, "error": canonical_summary_error}

    client = get_client(_config.get("api_key", ""))
    if client is None:
        error_message = "Notion client is not ready on the server."
        return {"ok": False, "error": error_message}

    try:
        db_id = _ensure_database(client, study_settings, config_data)
        page_id = _find_or_create_participant(client, db_id, result_payload, study_settings, config_data)
        session_id = str(result_payload.get("session_id") or "").strip()
        if not session_id:
            raise RuntimeError("Finalized result.json has no session_id.")
        existing_toggle = _find_session_toggle(client, page_id, session_id)
        current_count = _get_session_count(client, page_id)
        session_num = (
            _session_number_from_toggle(existing_toggle) or max(1, current_count)
            if existing_toggle
            else current_count + 1
        )
        upsert = _append_session_block(
            client,
            page_id,
            session_num,
            result_payload,
            hardware_config,
            saved_output,
            existing_toggle=existing_toggle,
        )
        _update_participant_properties(
            client,
            page_id,
            max(session_num, current_count),
            result_payload,
            config_data,
        )
        pid_short = str(result_payload.get("participant_id") or "?")[:8]
        print(f"[NOTION] Uploaded session {session_num} for participant {pid_short}…")
        return {"ok": True, "session_id": session_id, "upsert": upsert}
    except Exception as error:
        print(f"[NOTION] Upload failed{' (retry)' if is_retry else ''}: {error}")
        return {"ok": False, "error": str(error)}


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


# Same reason as in nextcloud_service: pytest would collect this as a test if a
# test module ever imported it by name.
test_connection.__test__ = False


def get_status() -> dict[str, Any]:
    return {
        "enabled": bool(_config.get("enabled")),
        "connected": bool(_clients),
        "queue_size": 0,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _stored_participant_metadata_fields(config_data: dict[str, Any]) -> list[str]:
    questions = config_data.get("questions") or []
    participant_question = next(
        (
            question
            for question in questions
            if isinstance(question, dict) and question.get("type") == "participant-id"
        ),
        None,
    )
    if not participant_question:
        return []

    fields = participant_question.get("fields") or {}
    stored_fields: list[str] = []
    for field_key in PARTICIPANT_FIELD_ORDER:
        field_config = fields.get(field_key) or {}
        if field_config.get("enabled") and field_config.get("store"):
            stored_fields.append(field_key)
    return stored_fields


def _build_participant_metadata_schema(config_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    schema: dict[str, dict[str, Any]] = {}
    for field_key in _stored_participant_metadata_fields(config_data):
        property_spec = PARTICIPANT_NOTION_PROPERTIES.get(field_key)
        if property_spec is None:
            print(f"[NOTION] No property mapping for participant field '{field_key}'; skipping it.")
            continue
        property_name, property_type = property_spec
        schema[property_name] = {property_type: {}}
    return schema


def _build_participant_metadata_properties(
    result_payload: dict[str, Any],
    config_data: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    metadata = result_payload.get("participant_metadata") or {}
    if not isinstance(metadata, dict):
        return {}

    properties: dict[str, dict[str, Any]] = {}
    for field_key in _stored_participant_metadata_fields(config_data):
        value = str(metadata.get(field_key) or "").strip()
        if not value:
            continue

        property_spec = PARTICIPANT_NOTION_PROPERTIES.get(field_key)
        if property_spec is None:
            print(f"[NOTION] No property mapping for participant field '{field_key}'; skipping it.")
            continue
        property_name, property_type = property_spec
        if property_type == "select":
            properties[property_name] = {"select": {"name": value}}
        elif property_type == "date":
            properties[property_name] = {"date": {"start": value}}
        else:
            properties[property_name] = {
                "rich_text": [{"type": "text", "text": {"content": _truncate(value)}}],
            }
    return properties


def _ensure_database(client: Any, study_settings: dict[str, Any], config_data: dict[str, Any]) -> str:
    db_id = study_settings.get("notion_database_id", "")
    if db_id:
        normalized_db_id = _strip_dashes(db_id)
        _ensure_participant_metadata_properties(client, normalized_db_id, study_settings, config_data)
        return normalized_db_id

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
    schema.update(_build_participant_metadata_schema(config_data))

    if hasattr(client, "data_sources"):
        db_args["initial_data_source"] = {"properties": schema}
    else:
        db_args["properties"] = schema

    db = client.databases.create(**db_args)

    new_id = _strip_dashes(db["id"])
    study_settings["notion_database_id"] = new_id
    
    if hasattr(client, "data_sources"):
        data_sources = db.get("data_sources", [])
        if data_sources:
            study_settings["notion_data_source_id"] = data_sources[0]["id"]
            
    _persist_study_database_id(config_data)
    print(f"[NOTION] Auto-created database: {new_id}")
    return new_id


def _ensure_participant_metadata_properties(
    client: Any,
    db_id: str,
    study_settings: dict[str, Any],
    config_data: dict[str, Any],
) -> None:
    desired_schema = _build_participant_metadata_schema(config_data)
    if not desired_schema:
        return

    if hasattr(client, "data_sources"):
        target_id = _get_data_source_id(client, db_id, study_settings, config_data)
        existing_properties = _retrieve_data_source_properties(client, target_id, db_id)
        missing_schema = {
            name: schema
            for name, schema in desired_schema.items()
            if name not in existing_properties
        }
        if missing_schema:
            if hasattr(client.data_sources, "update"):
                client.data_sources.update(data_source_id=target_id, properties=missing_schema)
            else:
                client.databases.update(database_id=db_id, properties=missing_schema)
        return

    db = client.databases.retrieve(database_id=db_id)
    existing_properties = db.get("properties", {})
    missing_schema = {
        name: schema
        for name, schema in desired_schema.items()
        if name not in existing_properties
    }
    if missing_schema:
        client.databases.update(database_id=db_id, properties=missing_schema)


def _retrieve_data_source_properties(client: Any, data_source_id: str, db_id: str) -> dict[str, Any]:
    try:
        data_source = client.data_sources.retrieve(data_source_id=data_source_id)
        return data_source.get("properties", {})
    except Exception:
        try:
            db = client.databases.retrieve(database_id=db_id)
            return db.get("properties", {})
        except Exception:
            return {}


def _get_data_source_id(client: Any, db_id: str, study_settings: dict[str, Any], config_data: dict[str, Any]) -> str:
    if not hasattr(client, "data_sources"):
        return db_id
    
    cached_ds = study_settings.get("notion_data_source_id")
    if cached_ds:
        return cached_ds
        
    try:
        db = client.databases.retrieve(database_id=db_id)
        data_sources = db.get("data_sources", [])
        if data_sources:
            ds_id = data_sources[0]["id"]
            study_settings["notion_data_source_id"] = ds_id
            _persist_study_database_id(config_data)
            return ds_id
    except Exception as e:
        print(f"[NOTION] Could not retrieve data source: {e}")
        
    return db_id

def _find_or_create_participant(client: Any, db_id: str, result_payload: dict[str, Any], study_settings: dict[str, Any], config_data: dict[str, Any]) -> str:
    participant_id = str(result_payload.get("participant_id") or "unknown")
    session_date = _session_date_iso(result_payload)

    ds_id = _get_data_source_id(client, db_id, study_settings, config_data)

    if hasattr(client, "data_sources"):
        results = client.data_sources.query(
            data_source_id=ds_id,
            filter={"property": "Participant ID", "title": {"equals": participant_id}},
        )
        parent_obj = {"type": "data_source_id", "data_source_id": ds_id}
    else:
        results = client.databases.query(
            database_id=ds_id,
            filter={"property": "Participant ID", "title": {"equals": participant_id}},
        )
        parent_obj = {"database_id": ds_id}

    if results.get("results"):
        return results["results"][0]["id"]

    page = client.pages.create(
        parent=parent_obj,
        properties={
            "Participant ID": {"title": [{"text": {"content": participant_id}}]},
            "Study Count": {"number": 0},
            "First Session": {"date": {"start": session_date}},
            "Last Session": {"date": {"start": session_date}},
            **_build_participant_metadata_properties(result_payload, config_data),
        },
    )
    return page["id"]


def _get_session_count(client: Any, page_id: str) -> int:
    try:
        page = client.pages.retrieve(page_id=page_id)
        count_prop = page.get("properties", {}).get("Study Count", {})
        return int(count_prop.get("number") or 0)
    except Exception:
        return 0


def _update_participant_properties(
    client: Any,
    page_id: str,
    session_num: int,
    result_payload: dict[str, Any],
    config_data: dict[str, Any],
) -> None:
    client.pages.update(
        page_id=page_id,
        properties={
            "Study Count": {"number": session_num},
            "Last Session": {"date": {"start": _session_date_iso(result_payload)}},
            **_build_participant_metadata_properties(result_payload, config_data),
        },
    )


def _append_session_block(
    client: Any,
    page_id: str,
    session_num: int,
    result_payload: dict[str, Any],
    hardware_config: dict[str, Any],
    saved_output: dict[str, Any],
    *,
    existing_toggle: dict[str, Any] | None = None,
) -> str:
    study_id = str(result_payload.get("study_id") or "—")
    session_id = str(result_payload.get("session_id") or "")
    session_date = _session_date_iso(result_payload)
    ts_start = str(result_payload.get("timestamp_start") or "")
    ts_end = str(result_payload.get("timestamp_end") or "")

    toggle_title = f"Session {session_num} · {study_id} · {session_date} [session:{session_id}]"
    canonical_output = _is_canonical_finalized_output(result_payload, saved_output)
    answer_lines = _format_answers(
        result_payload,
        include_legacy_sensor_summaries=not canonical_output,
    )
    biosignal_lines = _format_biosignals(
        hardware_config,
        saved_output,
        canonical_output=canonical_output,
    )

    children: list[dict[str, Any]] = [
        _paragraph(f"Dauer: {ts_start[:16]} → {ts_end[:16]} ({_duration_minutes(ts_start, ts_end)} min)"),
        _heading("Antworten"),
        *[_bullet(line) for line in (answer_lines or ["(keine Antworten)"])],
        _heading("Biosignale"),
        *[_bullet(line) for line in (biosignal_lines or ["(keine Sensoren aktiv)"])],
    ]

    commit_marker = f"study-runner-session-commit:{session_id}"
    if existing_toggle and _toggle_contains_text(
        client,
        str(existing_toggle.get("id") or ""),
        commit_marker,
    ):
        return "unchanged"

    if existing_toggle:
        toggle_id = str(existing_toggle.get("id") or "")
    else:
        # The session id in the title is the idempotency key. A retry reuses
        # this toggle even if the preceding network response was lost.
        response = client.blocks.children.append(
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

    children.append(_paragraph(commit_marker))

    # 2. Antworten und Biosignale sicher in 100er-Blöcken in den Toggle einfügen
    for i in range(0, len(children), 100):
        client.blocks.children.append(
            block_id=toggle_id,
            children=children[i:i+100]
        )
    return "updated" if existing_toggle else "created"


def _find_session_toggle(client: Any, page_id: str, session_id: str) -> dict[str, Any] | None:
    marker = f"[session:{session_id}]"
    cursor = None
    while True:
        kwargs: dict[str, Any] = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.blocks.children.list(**kwargs)
        for block in response.get("results") or []:
            if block.get("type") != "toggle":
                continue
            title = _rich_text_plain((block.get("toggle") or {}).get("rich_text") or [])
            if marker in title:
                return block
        if not response.get("has_more"):
            return None
        cursor = response.get("next_cursor")
        if not cursor:
            return None


def _session_number_from_toggle(toggle: dict[str, Any] | None) -> int | None:
    if not toggle:
        return None
    title = _rich_text_plain((toggle.get("toggle") or {}).get("rich_text") or [])
    match = re.match(r"Session\s+(\d+)", title)
    return int(match.group(1)) if match else None


def _toggle_contains_text(client: Any, toggle_id: str, marker: str) -> bool:
    if not toggle_id:
        return False
    cursor = None
    while True:
        kwargs: dict[str, Any] = {"block_id": toggle_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.blocks.children.list(**kwargs)
        for block in response.get("results") or []:
            block_type = str(block.get("type") or "")
            rich_text = (block.get(block_type) or {}).get("rich_text") or []
            if marker in _rich_text_plain(rich_text):
                return True
        if not response.get("has_more"):
            return False
        cursor = response.get("next_cursor")
        if not cursor:
            return False


def _rich_text_plain(items: list[dict[str, Any]]) -> str:
    return "".join(
        str(item.get("plain_text") or (item.get("text") or {}).get("content") or "")
        for item in items
        if isinstance(item, dict)
    )


def _format_answers(
    result_payload: dict[str, Any],
    *,
    include_legacy_sensor_summaries: bool = False,
) -> list[str]:
    answer_details = result_payload.get("answer_details") or []
    if isinstance(answer_details, list) and answer_details:
        return _format_answer_details(
            answer_details,
            include_legacy_sensor_summaries=include_legacy_sensor_summaries,
        )

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


def _format_answer_details(
    answer_details: list[dict[str, Any]],
    *,
    include_legacy_sensor_summaries: bool = False,
) -> list[str]:
    lines: list[str] = []
    for detail in answer_details:
        question_number = detail.get("question_number")
        question_type = detail.get("question_type") or "question"
        prompt = str(detail.get("question_prompt") or "").replace("\n", " ").strip()
        answer_text = "\u2014" if detail.get("skipped") else _format_answer_value(detail.get("answer"))
        interval_seconds = detail.get("interval_seconds", detail.get("seconds_since_previous_answer"))
        interval_text = f"{interval_seconds:.1f}s" if isinstance(interval_seconds, (int, float)) else "n/a"
        interval_kind = detail.get("biosignal_interval_kind") or "question_visible"
        answer_label = "Stimulus" if question_type == "stimulus" else f"Antwort: {answer_text}"
        line = (
            f"Q{question_number} [{question_type}] {prompt or '(ohne Prompt)'} | "
            f"{answer_label} | Intervall: {interval_kind}, {interval_text}"
        )
        if include_legacy_sensor_summaries:
            biomarker_text = _format_interval_biomarkers(detail.get("biosignal_interval") or {})
            line += f" | Legacy-RAM-Snapshot (nicht kanonisch): {biomarker_text}"
        lines.append(line)
    return lines


def _format_biosignals(
    hardware_config: dict[str, Any],
    saved_output: dict[str, Any],
    *,
    canonical_output: bool = False,
) -> list[str]:
    card_summary = saved_output.get("card_summary") or {}
    if isinstance(card_summary, dict) and isinstance(card_summary.get("cards"), list):
        return _format_card_summary(card_summary)

    # Finalized v3 results never fall back to old in-memory values. The upload
    # entry point rejects this state; the formatter guard keeps direct calls
    # fail-closed as well.
    if canonical_output:
        return []

    lines = []
    bio = saved_output.get("biosignal_summary") or {}

    brainbit = bio.get("brainbit") or {}
    brainbit_sidecar = saved_output.get("brainbit_file")
    if brainbit.get("active"):
        xdf = brainbit.get("xdf_path") or "—"
        lines.append(
            f"Legacy-RAM-Snapshot (nicht kanonisch) | BrainBit EEG: aktiv | "
            f"XDF: {xdf} | Sidecar: {brainbit_sidecar or 'n/a'}"
        )
    elif hardware_config.get("brainbit", {}).get("enabled"):
        lines.append("BrainBit EEG: konfiguriert (kein XDF dieser Session)")

    radar = bio.get("mini_radar") or {}
    radar_sidecar = saved_output.get("mr60_file")
    if radar.get("active"):
        lines.append(
            f"Legacy-RAM-Snapshot (nicht kanonisch) | Mini-Radar: aktiv | "
            f"Sidecar: {radar_sidecar or 'n/a'}"
        )
    elif hardware_config.get("mini_radar", {}).get("enabled"):
        lines.append("Mini-Radar: konfiguriert")

    cam = bio.get("camera_emotion") or {}
    if cam.get("active"):
        lines.append("Legacy-RAM-Snapshot (nicht kanonisch) | Camera Emotion: aktiv")
    elif hardware_config.get("camera_emotion", {}).get("enabled"):
        lines.append("Camera Emotion: konfiguriert")

    return lines


def _is_canonical_finalized_output(
    result_payload: dict[str, Any],
    saved_output: dict[str, Any],
) -> bool:
    return bool(
        isinstance(result_payload.get("server_finalization"), dict)
        or saved_output.get("card_summary_file")
        or saved_output.get("session_relative_path")
    )


def _canonical_card_summary_error(
    result_payload: dict[str, Any],
    saved_output: dict[str, Any],
) -> str | None:
    if not _is_canonical_finalized_output(result_payload, saved_output):
        return None
    summary = saved_output.get("card_summary")
    if not isinstance(summary, dict):
        return "Canonical Notion upload requires finalized card-summary.json."
    if summary.get("schema") != "study-runner/card-summary/v1":
        return "Canonical Notion upload received an unsupported card-summary.json schema."
    if not isinstance(summary.get("cards"), list):
        return "Canonical Notion upload requires a valid cards array from card-summary.json."
    return None


def _format_card_summary(summary: dict[str, Any]) -> list[str]:
    """Render arbitrary plugins from finalized card-summary.json."""

    lines: list[str] = []
    for card in summary.get("cards") or []:
        if not isinstance(card, dict):
            continue
        card_label = (
            f"Q{card.get('question_index')}"
            if card.get("question_index") is not None
            else str(card.get("card_id") or "Card")
        )
        streams = card.get("streams") if isinstance(card.get("streams"), dict) else {}
        for stream_key, stream in streams.items():
            if not isinstance(stream, dict):
                continue
            plugin = str(stream.get("plugin_key") or "plugin")
            quality = (
                f"n={stream.get('count', 0)}, valid={stream.get('valid_count', 0)}, "
                f"coverage={_fmt_metric(stream.get('coverage'))}, missing={stream.get('missing_count')}, "
                f"drops={stream.get('drop_count')}, max_gap={_fmt_metric(stream.get('max_gap_seconds'))}s"
            )
            channels = stream.get("channels") if isinstance(stream.get("channels"), dict) else {}
            if not channels:
                lines.append(f"{card_label} | {plugin}/{stream_key} | {quality}")
                continue
            for channel_name, channel in channels.items():
                if not isinstance(channel, dict):
                    continue
                if channel.get("kind") == "categorical":
                    stats = (
                        f"mode={channel.get('mode') or 'n/a'}, "
                        f"frequencies={channel.get('frequencies') or {}}"
                    )
                else:
                    stats = (
                        f"mean={_fmt_metric(channel.get('mean'))}, "
                        f"min={_fmt_metric(channel.get('min'))}, "
                        f"max={_fmt_metric(channel.get('max'))}, "
                        f"std={_fmt_metric(channel.get('stddev'))}"
                    )
                lines.append(
                    f"{card_label} | {plugin}/{stream_key}/{channel_name} | {stats} | {quality}"
                )
    return lines


def _format_interval_biomarkers(interval_summary: dict[str, Any]) -> str:
    parts: list[str] = []

    brainbit = interval_summary.get("brainbit") or {}
    if brainbit.get("available"):
        parts.append(
            "BrainBit "
            f"att={_fmt_metric(brainbit.get('avg_attention'))}, "
            f"rel={_fmt_metric(brainbit.get('avg_relaxation'))}, "
            f"delta={_fmt_metric(brainbit.get('avg_delta'))}, "
            f"theta={_fmt_metric(brainbit.get('avg_theta'))}, "
            f"alpha={_fmt_metric(brainbit.get('avg_alpha'))}, "
            f"beta={_fmt_metric(brainbit.get('avg_beta'))}, "
            f"gamma={_fmt_metric(brainbit.get('avg_gamma'))}"
        )
    else:
        parts.append("BrainBit n/a")

    radar = interval_summary.get("mini_radar") or {}
    if radar.get("available"):
        parts.append(
            "Radar "
            f"hr={_fmt_metric(radar.get('avg_heart_rate'))}, "
            f"br={_fmt_metric(radar.get('avg_breath_rate'))}, "
            f"q={_fmt_metric(radar.get('avg_quality'))}, "
            f"dist={_fmt_metric(radar.get('avg_distance'))}"
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
        from study_runner.backend.services.study_config_service import load_config, load_study

        if current_app:
            config_file = current_app.config["CONFIG_FILE"]
            studies_dir = current_app.config["SAVED_STUDIES_DIR"]
        else:
            raise RuntimeError("No active app context.")

        current_config = load_config(config_file)
        if str(current_config.get("study_id") or "").strip() == study_id:
            return current_config

        return load_study(studies_dir, study_id)
    except Exception:
        return queued_config_data


def _persist_study_database_id(config_data: dict[str, Any]) -> None:
    try:
        from flask import current_app
        from study_runner.backend.services.study_config_service import save_config, save_study
        from study_runner.backend.services.study_plugin_config import (
            normalize_study_settings_plugins,
        )

        canonical_config = deepcopy(config_data)
        study_settings = canonical_config.setdefault("study_settings", {})
        plugins = study_settings.setdefault("plugins", {})
        notion = plugins.setdefault(
            "notion",
            {"enabled": bool(study_settings.get("notion_enabled")), "required": False, "settings": {}},
        )
        notion_settings = notion.setdefault("settings", {})
        for setting_name, legacy_name in (
            ("parent_page_id", "notion_parent_page_id"),
            ("database_id", "notion_database_id"),
            ("data_source_id", "notion_data_source_id"),
        ):
            if legacy_name in study_settings:
                notion_settings[setting_name] = study_settings.get(legacy_name)
        canonical_config["study_settings"] = normalize_study_settings_plugins(
            study_settings
        )

        save_config(current_app.config["CONFIG_FILE"], canonical_config)
        studies_dir = current_app.config["SAVED_STUDIES_DIR"]
        save_study(studies_dir, canonical_config)
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
