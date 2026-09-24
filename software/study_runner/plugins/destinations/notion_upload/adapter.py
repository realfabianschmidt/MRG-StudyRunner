"""
Notion adapter - uploads anonymized study results to a Notion database.

Two databases under the study's parent page: "StudyRunner Participants" (one
row per pseudonymized participant, with the stored intake fields) and
"StudyRunner Sessions" (one row per session, related to its participant). A
session's page holds real tables: every card's answer, and every sensor's
per-card statistics from the finalized card-summary.json.

Network failures are returned to the central upload-job service, which owns the
persistent retry journal for every destination.

Requires: notion-client  (auto-install optional)
Enable:   set "notion": { "enabled": true, ... } in study_content/settings/hardware_settings.json
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from study_runner.shared.dependency_utils import ensure_requirements
from study_runner.contracts.participant_fields import PARTICIPANT_FIELD_ORDER


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
    api_key: str = "",
) -> dict[str, Any]:
    """Upload one completed study session.

    Retries are the persistent upload-job queue's responsibility
    (`upload_jobs_service.py`), not this function's: it re-invokes
    `publish_destination` with the same queued payload rather than asking
    this adapter to refresh anything itself.

    This runs inside the plugin's own `driver.py` subprocess
    (`study-runner-stdio/v1`), which may not import `backend` -- so it
    cannot save the auto-created `database_id`/`data_source_id` back to the
    study config file itself the way it used to. Instead it reports what
    changed via `study_config_updates` in the result; the host
    (`upload_runtime.py`, which owns that dependency) persists it after this
    call returns. See docs/archive/architecture-1.0-umbau.md, Phase 2.3.
    """
    if config_data is None:
        config_data = {}

    study_settings = config_data.get("study_settings", {})
    if not study_settings.get("notion_enabled"):
        # Nothing to publish is not a failure: never retry or report it.
        return {"ok": True, "skipped": True, "reason": "Notion is disabled for this study."}

    canonical_summary_error = _canonical_card_summary_error(result_payload, saved_output)
    if canonical_summary_error:
        return {"ok": False, "error": canonical_summary_error}

    key = str(api_key or _config.get("api_key", "") or "").strip()
    if not key:
        return {
            "ok": False,
            "permanent": True,
            "error": "No Notion API key is stored for this study. Add it in the study's Notion settings.",
        }
    client = get_client(key)
    if client is None:
        if not _config.get("enabled"):
            return {
                "ok": False,
                "permanent": True,
                "error": "Notion upload is not ready: it is switched off on this computer. "
                "Turn it on under Settings > This computer > Notion.",
            }
        return {"ok": False, "error": "Notion upload is not ready: the Notion client could not be started."}

    study_config_updates: dict[str, str] = {}
    try:
        db_id = _ensure_database(client, study_settings, config_data, study_config_updates)
        page_id = _find_or_create_participant(
            client, db_id, result_payload, study_settings, config_data, study_config_updates,
        )
        session_id = str(result_payload.get("session_id") or "").strip()
        if not session_id:
            raise RuntimeError("Finalized result.json has no session_id.")
        sessions_db_id = _ensure_sessions_database(client, db_id, study_settings, study_config_updates)
        upsert = _upsert_session_page(
            client,
            sessions_db_id,
            page_id,
            result_payload,
            hardware_config,
            saved_output,
        )
        current_count = _get_session_count(client, page_id)
        _update_participant_properties(
            client,
            page_id,
            current_count + 1 if upsert == "created" else max(1, current_count),
            result_payload,
            config_data,
        )
        pid_short = str(result_payload.get("participant_id") or "?")[:8]
        print(f"[NOTION] Session {upsert} for participant {pid_short}…")
        result = {"ok": True, "session_id": session_id, "upsert": upsert}
        if study_config_updates:
            result["study_config_updates"] = study_config_updates
        return result
    except Exception as error:
        print(f"[NOTION] Upload failed: {error}")
        permanent, message = classify_notion_error(error)
        result = {"ok": False, "error": message}
        if permanent:
            result["permanent"] = True
        if study_config_updates:
            result["study_config_updates"] = study_config_updates
        return result


# Notion answers these when retrying cannot help: fix the key, share the page,
# or correct an ID. Anything else (rate limits, 5xx, network) is retried.
_PERMANENT_NOTION_CODES = {
    "unauthorized": "Notion rejected the API key. Check that it is the integration's secret and still valid.",
    "restricted_resource": "The Notion integration may not open this page or database. In Notion, open the page, "
    "choose ••• > Connections, and add your integration.",
    "object_not_found": "Notion cannot find the page or database. Check the ID and that the page is shared with "
    "your integration (••• > Connections).",
    "validation_error": "Notion rejected the request: {detail}",
    "invalid_request_url": "Notion rejected the request: {detail}",
}


def classify_notion_error(error: Exception) -> tuple[bool, str]:
    """Return (permanent, operator-facing message) for an upload failure."""
    code = str(getattr(error, "code", "") or "").lower()
    code = code.rsplit(".", 1)[-1]  # an APIErrorCode enum prints as APIErrorCode.Unauthorized
    for known, message in _PERMANENT_NOTION_CODES.items():
        if code == known or code == known.replace("_", ""):
            return True, message.format(detail=str(error))
    text = str(error)
    if "parent_page_id is required" in text:
        return True, (
            "No Notion page is set for this study. Enter the Parent Page ID (or a Database ID) "
            "in the study's Notion settings."
        )
    return False, text


def test_connection(
    *,
    api_key: str,
    timeout_seconds: int = 10,
    parent_page_id: str = "",
    database_id: str = "",
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
        return {"ok": False, "checks": [{"name": "Package", "ok": False, "message": "The notion-client package could not be installed."}]}

    from notion_client import Client, APIErrorCode, APIResponseError

    checks: list[dict[str, Any]] = []

    # 1. API-Key validieren (eigene Bot-Info abrufen)
    try:
        client = Client(auth=api_key.strip(), timeout_ms=timeout_seconds * 1000)
        me = client.users.me()
        bot_name = me.get("name") or me.get("bot", {}).get("owner", {}).get("user", {}).get("name") or "Integration"
        checks.append({"name": "API Key", "ok": True, "message": f'Connected as "{bot_name}"'})
    except APIResponseError as error:
        msg = (
            "Notion rejected the API key. Check that it is the integration's secret and still valid."
            if error.code == APIErrorCode.Unauthorized
            else str(error)
        )
        checks.append({"name": "API Key", "ok": False, "message": msg})
        return {"ok": False, "checks": checks}
    except Exception as error:
        checks.append({"name": "API Key", "ok": False, "message": f"Connection failed: {error}"})
        return {"ok": False, "checks": checks}

    # 2. The most common real problem: the page is not shared with the integration.
    for label, raw_id, retrieve in (
        ("Database", database_id, lambda value: client.databases.retrieve(database_id=value)),
        ("Parent page", parent_page_id, lambda value: client.pages.retrieve(page_id=value)),
    ):
        target = _strip_dashes(str(raw_id or "").strip())
        if not target:
            continue
        try:
            retrieve(target)
            checks.append({"name": label, "ok": True, "message": f"{label} is reachable for the integration."})
        except Exception as error:
            _permanent, message = classify_notion_error(error)
            checks.append({"name": label, "ok": False, "message": message})
        break

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


def _ensure_database(
    client: Any,
    study_settings: dict[str, Any],
    config_data: dict[str, Any],
    updates: dict[str, str],
) -> str:
    db_id = study_settings.get("notion_database_id", "")
    if db_id:
        normalized_db_id = _strip_dashes(db_id)
        _ensure_participant_metadata_properties(client, normalized_db_id, study_settings, config_data, updates)
        return normalized_db_id

    parent_page_id = _strip_dashes(study_settings.get("notion_parent_page_id", ""))
    if not parent_page_id:
        raise RuntimeError("Notion parent_page_id is required in study_settings to auto-create a Notion database.")

    # A database of this name under the parent page is reused, so a lost
    # response or a second study on the same page never duplicates it.
    existing_id = _find_child_database(client, parent_page_id, PARTICIPANTS_DATABASE_TITLE)
    if existing_id:
        study_settings["notion_database_id"] = existing_id
        updates["database_id"] = existing_id
        _ensure_participant_metadata_properties(client, existing_id, study_settings, config_data, updates)
        print(f"[NOTION] Reusing database: {existing_id}")
        return existing_id

    db_args = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": PARTICIPANTS_DATABASE_TITLE}}],
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
    updates["database_id"] = new_id

    if hasattr(client, "data_sources"):
        data_sources = db.get("data_sources", [])
        if data_sources:
            study_settings["notion_data_source_id"] = data_sources[0]["id"]
            updates["data_source_id"] = data_sources[0]["id"]

    print(f"[NOTION] Auto-created database: {new_id}")
    return new_id


def _ensure_participant_metadata_properties(
    client: Any,
    db_id: str,
    study_settings: dict[str, Any],
    config_data: dict[str, Any],
    updates: dict[str, str],
) -> None:
    desired_schema = _build_participant_metadata_schema(config_data)
    if not desired_schema:
        return

    if hasattr(client, "data_sources"):
        target_id = _get_data_source_id(client, db_id, study_settings, config_data, updates)
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


def _get_data_source_id(
    client: Any,
    db_id: str,
    study_settings: dict[str, Any],
    config_data: dict[str, Any],
    updates: dict[str, str],
) -> str:
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
            updates["data_source_id"] = ds_id
            return ds_id
    except Exception as e:
        print(f"[NOTION] Could not retrieve data source: {e}")

    return db_id

def _find_or_create_participant(
    client: Any,
    db_id: str,
    result_payload: dict[str, Any],
    study_settings: dict[str, Any],
    config_data: dict[str, Any],
    updates: dict[str, str],
) -> str:
    participant_id = str(result_payload.get("participant_id") or "unknown")
    session_date = _session_date_iso(result_payload)

    ds_id = _get_data_source_id(client, db_id, study_settings, config_data, updates)

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


PARTICIPANTS_DATABASE_TITLE = "StudyRunner Participants"
SESSIONS_DATABASE_TITLE = "StudyRunner Sessions"
NOTION_CHILDREN_LIMIT = 100
ANSWER_TABLE_HEADER = ["#", "Karte", "Frage", "Antwort", "Dauer (s)"]
BIOSIGNAL_TABLE_HEADER = [
    "Karte", "Sensor / Stream", "Kanal", "Mittel / Modus", "Min", "Max", "Std", "Abdeckung", "Max. Lücke (s)",
]


def _find_child_database(client: Any, parent_page_id: str, title: str) -> str:
    """Id of a database titled ``title`` directly under the parent page, or ''."""
    cursor = None
    while True:
        kwargs: dict[str, Any] = {"block_id": parent_page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.blocks.children.list(**kwargs)
        for block in response.get("results") or []:
            if block.get("type") != "child_database":
                continue
            if str((block.get("child_database") or {}).get("title") or "").strip() == title:
                return _strip_dashes(str(block.get("id") or ""))
        cursor = response.get("next_cursor") if response.get("has_more") else None
        if not cursor:
            return ""


def _data_source_of(client: Any, db_id: str) -> str:
    if not hasattr(client, "data_sources"):
        return db_id
    try:
        data_sources = client.databases.retrieve(database_id=db_id).get("data_sources") or []
    except Exception as error:
        print(f"[NOTION] Could not retrieve data source: {error}")
        return db_id
    return str(data_sources[0]["id"]) if data_sources else db_id


def _sessions_parent_page(client: Any, participants_db_id: str, study_settings: dict[str, Any]) -> str:
    parent = _strip_dashes(study_settings.get("notion_parent_page_id", ""))
    if parent:
        return parent
    # Only a database id was configured: put the sessions next to it.
    database = client.databases.retrieve(database_id=participants_db_id)
    parent = (database.get("parent") or {}).get("page_id") or ""
    if not parent:
        raise RuntimeError(
            "Notion parent_page_id is required in study_settings to create the sessions database."
        )
    return _strip_dashes(str(parent))


def _ensure_sessions_database(
    client: Any,
    participants_db_id: str,
    study_settings: dict[str, Any],
    updates: dict[str, str],
) -> str:
    """One row per session, related to its participant row."""
    db_id = _strip_dashes(study_settings.get("notion_sessions_database_id", ""))
    if db_id:
        return db_id
    parent_page_id = _sessions_parent_page(client, participants_db_id, study_settings)
    db_id = _find_child_database(client, parent_page_id, SESSIONS_DATABASE_TITLE)
    if not db_id:
        schema: dict[str, Any] = {
            "Session": {"title": {}},
            "Session ID": {"rich_text": {}},
            "Participant ID": {"rich_text": {}},
            "Study": {"rich_text": {}},
            "Start": {"date": {}},
            "Ende": {"date": {}},
            "Dauer (min)": {"number": {"format": "number"}},
            "Karten": {"number": {"format": "number"}},
            "Beantwortet": {"number": {"format": "number"}},
            "Übersprungen": {"number": {"format": "number"}},
            "Sensoren": {"multi_select": {}},
        }
        if hasattr(client, "data_sources"):
            relation = {
                "data_source_id": _data_source_of(client, participants_db_id),
                "type": "dual_property",
                "dual_property": {},
            }
        else:
            relation = {"database_id": participants_db_id, "type": "dual_property", "dual_property": {}}
        db_args: dict[str, Any] = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": SESSIONS_DATABASE_TITLE}}],
        }
        try:
            database = _create_database(client, db_args, {**schema, "Participant": {"relation": relation}})
        except Exception as error:
            # The relation is a convenience; the Participant ID column already
            # links every session. Never lose the upload over it.
            print(f"[NOTION] Sessions database without relation ({error}).")
            database = _create_database(client, db_args, schema)
        db_id = _strip_dashes(str(database["id"]))
        print(f"[NOTION] Auto-created sessions database: {db_id}")
    study_settings["notion_sessions_database_id"] = db_id
    updates["sessions_database_id"] = db_id
    return db_id


def _create_database(client: Any, db_args: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    args = dict(db_args)
    if hasattr(client, "data_sources"):
        args["initial_data_source"] = {"properties": schema}
    else:
        args["properties"] = schema
    return client.databases.create(**args)


def _query_database(
    client: Any,
    db_id: str,
    filter_: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rows matching ``filter_`` and the parent object for a new row."""
    if hasattr(client, "data_sources"):
        source_id = _data_source_of(client, db_id)
        results = client.data_sources.query(data_source_id=source_id, filter=filter_)
        return results.get("results") or [], {"type": "data_source_id", "data_source_id": source_id}
    results = client.databases.query(database_id=db_id, filter=filter_)
    return results.get("results") or [], {"database_id": db_id}


def _upsert_session_page(
    client: Any,
    sessions_db_id: str,
    participant_page_id: str,
    result_payload: dict[str, Any],
    hardware_config: dict[str, Any],
    saved_output: dict[str, Any],
) -> str:
    """Write one session as a database row whose page holds real tables.

    The Session ID column is the idempotency key, and the commit marker is the
    last block: a row with its marker is complete ("unchanged"); a row without
    it was cut short and is replaced.
    """
    session_id = str(result_payload.get("session_id") or "")
    commit_marker = f"study-runner-session-commit:{session_id}"
    rows, parent = _query_database(
        client, sessions_db_id, {"property": "Session ID", "rich_text": {"equals": session_id}},
    )
    replaced = False
    for row in rows:
        if _page_contains_text(client, str(row.get("id") or ""), commit_marker):
            return "unchanged"
        client.pages.update(page_id=row["id"], archived=True)
        replaced = True

    properties = _session_properties(result_payload, saved_output, participant_page_id)
    try:
        page = client.pages.create(parent=parent, properties=properties)
    except Exception:
        # A sessions database that was created without the relation column.
        properties.pop("Participant", None)
        page = client.pages.create(parent=parent, properties=properties)

    blocks = _session_page_blocks(result_payload, hardware_config, saved_output)
    blocks.append(_paragraph(commit_marker))
    for start in range(0, len(blocks), NOTION_CHILDREN_LIMIT):
        client.blocks.children.append(block_id=page["id"], children=blocks[start:start + NOTION_CHILDREN_LIMIT])
    return "updated" if replaced else "created"


def _session_properties(
    result_payload: dict[str, Any],
    saved_output: dict[str, Any],
    participant_page_id: str,
) -> dict[str, Any]:
    participant_id = str(result_payload.get("participant_id") or "unknown")
    ts_start = str(result_payload.get("timestamp_start") or "")
    ts_end = str(result_payload.get("timestamp_end") or "")
    details = [detail for detail in result_payload.get("answer_details") or [] if isinstance(detail, dict)]
    answerable = [
        detail for detail in details
        if detail.get("question_type") not in {"stimulus", "info", "finish", "participant-id"}
    ]
    skipped = sum(1 for detail in answerable if detail.get("skipped"))

    def text(value: str) -> dict[str, Any]:
        return {"rich_text": [{"type": "text", "text": {"content": _truncate(value)}}]}

    title = f"{participant_id[:8]} · {_session_date_iso(result_payload)}"
    properties: dict[str, Any] = {
        "Session": {"title": [{"type": "text", "text": {"content": title}}]},
        "Session ID": text(str(result_payload.get("session_id") or "")),
        "Participant ID": text(participant_id),
        "Study": text(str(result_payload.get("study_id") or "—")),
        "Karten": {"number": len(details)},
        "Beantwortet": {"number": len(answerable) - skipped},
        "Übersprungen": {"number": skipped},
        "Sensoren": {"multi_select": [{"name": name[:100]} for name in _sensor_names(saved_output)]},
        "Participant": {"relation": [{"id": participant_page_id}]},
    }
    if ts_start:
        properties["Start"] = {"date": {"start": ts_start}}
    if ts_end:
        properties["Ende"] = {"date": {"start": ts_end}}
    minutes = _duration_minutes(ts_start, ts_end)
    if minutes != "?":
        properties["Dauer (min)"] = {"number": int(minutes)}
    return properties


def _sensor_names(saved_output: dict[str, Any]) -> list[str]:
    summary = saved_output.get("card_summary")
    cards = summary.get("cards") if isinstance(summary, dict) else None
    names: set[str] = set()
    for card in cards or []:
        streams = card.get("streams") if isinstance(card, dict) else None
        for stream in (streams or {}).values():
            if isinstance(stream, dict) and stream.get("plugin_key"):
                names.add(str(stream["plugin_key"]))
    return sorted(names)


def _session_page_blocks(
    result_payload: dict[str, Any],
    hardware_config: dict[str, Any],
    saved_output: dict[str, Any],
) -> list[dict[str, Any]]:
    ts_start = str(result_payload.get("timestamp_start") or "")
    ts_end = str(result_payload.get("timestamp_end") or "")
    blocks: list[dict[str, Any]] = [
        _paragraph(
            f"Studie: {result_payload.get('study_id') or '—'} · "
            f"{ts_start[:16]} → {ts_end[:16]} ({_duration_minutes(ts_start, ts_end)} min)"
        ),
        _heading("Antworten"),
    ]
    answer_rows = _answer_table_rows(result_payload)
    blocks.extend(_tables(ANSWER_TABLE_HEADER, answer_rows) if answer_rows else [_paragraph("(keine Antworten)")])

    blocks.append(_heading("Biosignale pro Karte"))
    summary = saved_output.get("card_summary")
    if isinstance(summary, dict) and isinstance(summary.get("cards"), list):
        bio_rows = _biosignal_table_rows(summary, result_payload)
        if bio_rows:
            blocks.extend(_tables(BIOSIGNAL_TABLE_HEADER, bio_rows))
        else:
            blocks.append(_paragraph("(keine Sensoren aktiv)"))
    else:
        canonical = _is_canonical_finalized_output(result_payload, saved_output)
        lines = _format_biosignals(hardware_config, saved_output, canonical_output=canonical)
        blocks.extend(_bullet(line) for line in (lines or ["(keine Sensoren aktiv)"]))
    return blocks


def _card_labels(result_payload: dict[str, Any]) -> dict[int, str]:
    labels: dict[int, str] = {}
    for detail in result_payload.get("answer_details") or []:
        if not isinstance(detail, dict) or not isinstance(detail.get("question_index"), int):
            continue
        index = detail["question_index"]
        number = detail.get("question_number") or index + 1
        labels[index] = f"Q{number} · {detail.get('question_type') or 'card'}"
    return labels


def _answer_table_rows(result_payload: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for detail in result_payload.get("answer_details") or []:
        if not isinstance(detail, dict):
            continue
        question_type = str(detail.get("question_type") or "")
        if question_type == "stimulus":
            answer = "(Stimulus)"
        elif detail.get("skipped"):
            answer = "— (übersprungen)"
        else:
            answer = _format_answer_value(detail.get("answer"))
        seconds = detail.get("interval_seconds", detail.get("seconds_since_previous_answer"))
        if not isinstance(seconds, (int, float)):
            seconds = _seconds_between(detail.get("shown_at"), detail.get("answered_at"))
        rows.append([
            str(detail.get("question_number") or ""),
            question_type,
            str(detail.get("question_prompt") or "").replace("\n", " ").strip(),
            answer,
            f"{seconds:.1f}" if isinstance(seconds, (int, float)) else "",
        ])
    if rows:
        return rows
    # Results without per-question details still list their raw answers.
    return [
        [str(key), "", "", _format_answer_value(value), ""]
        for key, value in sorted((result_payload.get("answers") or {}).items())
        if value is not None
    ]


def _biosignal_table_rows(summary: dict[str, Any], result_payload: dict[str, Any]) -> list[list[str]]:
    """Any plugin's streams from finalized card-summary.json; no plugin is named here."""
    labels = _card_labels(result_payload)
    rows: list[list[str]] = []
    for card in summary.get("cards") or []:
        if not isinstance(card, dict):
            continue
        index = card.get("question_index")
        if isinstance(index, int):
            card_label = labels.get(index, f"Q{index + 1}")
        else:
            card_label = str(card.get("card_id") or "Karte")
        streams = card.get("streams") if isinstance(card.get("streams"), dict) else {}
        for stream_key, stream in streams.items():
            if not isinstance(stream, dict):
                continue
            source = f"{stream.get('plugin_key') or 'plugin'} / {stream_key}"
            coverage = _fmt_percent(stream.get("coverage"))
            gap = _fmt_metric(stream.get("max_gap_seconds"))
            channels = stream.get("channels") if isinstance(stream.get("channels"), dict) else {}
            if not channels:
                rows.append([card_label, source, "", "", "", "", "", coverage, gap])
                continue
            for channel_name, channel in channels.items():
                if not isinstance(channel, dict):
                    continue
                if channel.get("kind") == "categorical":
                    rows.append([
                        card_label, source, str(channel_name), str(channel.get("mode") or "n/a"),
                        "", "", "", coverage, gap,
                    ])
                else:
                    rows.append([
                        card_label, source, str(channel_name),
                        _fmt_metric(channel.get("mean")), _fmt_metric(channel.get("min")),
                        _fmt_metric(channel.get("max")), _fmt_metric(channel.get("stddev")),
                        coverage, gap,
                    ])
    return rows


def _tables(header: list[str], rows: list[list[str]]) -> list[dict[str, Any]]:
    """Notion table blocks; one table holds at most 100 rows, header included."""
    per_table = NOTION_CHILDREN_LIMIT - 1
    return [_table(header, rows[start:start + per_table]) for start in range(0, len(rows), per_table)]


def _table(header: list[str], rows: list[list[str]]) -> dict[str, Any]:
    def row(cells: list[str]) -> dict[str, Any]:
        return {
            "object": "block",
            "type": "table_row",
            "table_row": {"cells": [[{"type": "text", "text": {"content": _truncate(cell)}}] for cell in cells]},
        }

    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": len(header),
            "has_column_header": True,
            "has_row_header": False,
            "children": [row(header), *(row(cells) for cells in rows)],
        },
    }


def _page_contains_text(client: Any, page_id: str, marker: str) -> bool:
    if not page_id:
        return False
    cursor = None
    while True:
        kwargs: dict[str, Any] = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.blocks.children.list(**kwargs)
        for block in response.get("results") or []:
            block_type = str(block.get("type") or "")
            rich_text = (block.get(block_type) or {}).get("rich_text") or []
            if marker in _rich_text_plain(rich_text):
                return True
        cursor = response.get("next_cursor") if response.get("has_more") else None
        if not cursor:
            return False


def _rich_text_plain(items: list[dict[str, Any]]) -> str:
    return "".join(
        str(item.get("plain_text") or (item.get("text") or {}).get("content") or "")
        for item in items
        if isinstance(item, dict)
    )


def _format_biosignals(
    hardware_config: dict[str, Any],
    saved_output: dict[str, Any],
    *,
    canonical_output: bool = False,
) -> list[str]:
    """Bullet lines for results from before canonical card summaries existed.

    Canonical card summaries render as a table (_biosignal_table_rows).
    """
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


def _fmt_percent(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value * 100:.0f} %"
    return "n/a"


def _seconds_between(start: Any, end: Any) -> float | None:
    try:
        import datetime
        t0 = datetime.datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        t1 = datetime.datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return (t1 - t0).total_seconds()
    except (TypeError, ValueError):
        return None


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
