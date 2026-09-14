"""One-way compatibility migrations for delivery state."""
from __future__ import annotations

import datetime as dt
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable


def build_job_metadata(result_payload: dict[str, Any], saved_output: dict[str, Any]) -> dict[str, Any]:
    files = [
        value
        for key, value in saved_output.items()
        if key.endswith("_file") and isinstance(value, str) and value
    ]
    return {
        "answer_count": len(result_payload.get("answer_details") or result_payload.get("answers") or {}),
        "recorded_files": files,
    }


def migrate_legacy_notion_queue(
    path: Path,
    *,
    enqueue: Callable[..., dict[str, Any]],
    clock: Callable[[], float],
) -> dict[str, int | str]:
    """Idempotently move an old Notion JSONL queue into journaled jobs."""

    if not path.exists():
        return {"found": 0, "migrated": 0}
    try:
        entries = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as error:
        return {"found": 0, "migrated": 0, "error": str(error)}
    if any(not isinstance(entry, dict) for entry in entries):
        return {
            "found": len(entries),
            "migrated": 0,
            "error": "Legacy Notion queue contains an invalid entry; the original file was kept.",
        }

    migrated = 0
    for entry in entries:
        canonical = json.dumps(entry, sort_keys=True, ensure_ascii=False).encode("utf-8")
        deterministic_id = f"legacy-notion-{hashlib.sha256(canonical).hexdigest()[:24]}"
        result_payload = entry.get("result_payload") or {}
        saved_output = entry.get("saved_output") or {}
        enqueue(
            kind="notion",
            study_id=str(result_payload.get("study_id") or ""),
            participant_id=str(result_payload.get("participant_id") or ""),
            session_id=str(
                result_payload.get("session_id")
                or Path(str(saved_output.get("json_file") or "")).stem
            ),
            label="Notion",
            payload=_redact_legacy_payload(entry),
            metadata=build_job_metadata(result_payload, saved_output),
            job_id=deterministic_id,
            created_epoch=_parse_epoch(entry.get("queued_at")) or clock(),
        )
        migrated += 1

    try:
        path.unlink()
    except OSError as error:
        return {"found": len(entries), "migrated": migrated, "error": str(error)}
    return {"found": len(entries), "migrated": migrated}


def _parse_epoch(value: Any) -> float | None:
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _redact_legacy_payload(entry: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(entry)
    hardware_config = payload.get("hardware_config")
    if not isinstance(hardware_config, dict):
        return payload
    notion_config = hardware_config.get("notion")
    if isinstance(notion_config, dict):
        notion_config.pop("api_key", None)
    nextcloud_config = hardware_config.get("nextcloud")
    if isinstance(nextcloud_config, dict):
        nextcloud_config.pop("password", None)
    return payload
