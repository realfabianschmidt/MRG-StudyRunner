from __future__ import annotations

import json
import shutil
import tempfile
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _normalize_data_dir(data_dir: Path | None) -> Path:
    if data_dir is None:
        return Path.home() / ".study-runner-admin"
    return data_dir.expanduser().resolve()


def _run_archive_zip(output_path: Path, root_dir: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(root_dir.rglob("*")):
            rel = item.relative_to(root_dir)
            archive.write(item, arcname=str(rel))


class ParticipantRegistry:
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = _normalize_data_dir(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.registry_file = self.data_dir / "participant-registry.json"

    def _read_registry(self) -> dict[str, Any]:
        if not self.registry_file.exists():
            return {"participants": []}
        try:
            return json.loads(self.registry_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"participants": []}

    def _write_registry(self, payload: dict[str, Any]) -> None:
        self.registry_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def register(self, participant_id: str, study_id: str | None = None, **metadata: Any) -> dict[str, Any]:
        registry = self._read_registry()
        record = {
            "participant_id": participant_id,
            "study_id": study_id,
            "metadata": metadata,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        registry["participants"] = [
            item for item in registry.get("participants", []) if item.get("participant_id") != participant_id
        ]
        registry["participants"].append(record)
        self._write_registry(registry)
        return record

    def delete_participant(
        self,
        participant_id: str,
        study_id: str | None = None,
        reason: str = "user requested deletion",
        archive_first: bool = False,
    ) -> dict[str, Any]:
        matches = find_participant_in_results(participant_id, self.data_dir)
        archive_path = None
        if archive_first and matches.get("matches"):
            archive_path = self.data_dir / "archive" / f"{participant_id}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.zip"
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="study-runner-admin-archive-") as tmp_dir:
                staging = Path(tmp_dir)
                for match in matches["matches"]:
                    study_root = Path(match["study_path"])
                    if study_root.exists():
                        target = staging / study_root.name
                        shutil.copytree(study_root, target, dirs_exist_ok=True)
                _run_archive_zip(archive_path, staging)

        deleted = []
        for match in matches.get("matches", []):
            study_root = Path(match["study_path"])
            if study_root.exists():
                shutil.rmtree(study_root)
                deleted.append(match["study_path"])

        registry = self._read_registry()
        registry["participants"] = [
            item for item in registry.get("participants", []) if item.get("participant_id") != participant_id
        ]
        self._write_registry(registry)

        return {
            "status": "deleted",
            "participant_id": participant_id,
            "study_id": study_id,
            "reason": reason,
            "archive_path": str(archive_path) if archive_path else None,
            "deleted_paths": deleted,
            "matches": matches,
        }


def generate_participant_id(study_id: str | None = None, prefix: str = "P") -> str:
    suffix = uuid.uuid4().hex[:12]
    return f"{prefix}-{suffix}"


def find_participant_in_results(participant_id: str, data_dir: Path | None = None) -> dict[str, Any]:
    base = _normalize_data_dir(data_dir)
    matches: list[dict[str, Any]] = []
    if not base.exists():
        return {"participant_id": participant_id, "matches": [], "study_count": 0}

    for participants_root in sorted(base.rglob("participants"), key=lambda path: str(path)):
        if not participants_root.is_dir():
            continue
        target = participants_root / participant_id
        if not target.exists():
            continue
        sessions: list[dict[str, Any]] = []
        session_root = target / "sessions"
        if session_root.exists():
            for session_dir in sorted(session_root.iterdir(), key=lambda p: p.name):
                if not session_dir.is_dir():
                    continue
                sessions.append(
                    {
                        "session_folder": session_dir.name,
                        "session_path": str(session_dir),
                        "files": sorted(p.name for p in session_dir.rglob("*") if p.is_file()),
                    }
                )
        study_id = participants_root.parent.name if participants_root.parent.name else "unknown"
        matches.append(
            {
                "study_id": study_id,
                "study_path": str(participants_root.parent),
                "participant_id": participant_id,
                "session_count": len(sessions),
                "sessions": sessions,
            }
        )

    return {"participant_id": participant_id, "study_count": len(matches), "matches": matches}


def list_participants_for_study(study_id: str, data_dir: Path | None = None) -> list[dict[str, Any]]:
    base = _normalize_data_dir(data_dir)
    if not base.exists():
        return []

    results_root = base / "saved_results"
    participants_root = results_root / study_id / "participants"
    if not participants_root.exists():
        return []

    rows: list[dict[str, Any]] = []
    for participant_dir in sorted(participants_root.iterdir(), key=lambda p: p.name):
        if not participant_dir.is_dir():
            continue
        sessions_root = participant_dir / "sessions"
        sessions = []
        if sessions_root.exists():
            sessions = [
                {"folder": item.name, "path": str(item)}
                for item in sorted(sessions_root.iterdir(), key=lambda p: p.name)
                if item.is_dir()
            ]
        rows.append({"participant_id": participant_dir.name, "study_id": study_id, "sessions": sessions})
    return rows
