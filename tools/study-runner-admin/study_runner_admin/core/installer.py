from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any


def generate_participant_id(study_id: str | None = None, prefix: str = "P") -> str:
    suffix = uuid.uuid4().hex[:12]
    return f"{prefix}-{suffix}"


def _safe_data_dir(data_dir: Path | None) -> Path:
    if data_dir is None:
        return Path.home() / ".study-runner-admin"
    return data_dir.expanduser().resolve()


def _study_dir_for(data_dir: Path | None, study_id: str | None) -> Path | None:
    base = _safe_data_dir(data_dir)
    if not base.exists():
        return None
    if study_id is None:
        return base
    return base / "saved_results" / study_id


def find_participant_in_results(participant_id: str, data_dir: Path | None = None) -> dict[str, Any]:
    base = _safe_data_dir(data_dir)
    matches: list[dict[str, Any]] = []
    if not base.exists():
        return {"participant_id": participant_id, "study_ids": [], "sessions": []}

    for study_path in sorted(base.rglob("participants")):
        participant_root = study_path / participant_id
        if not participant_root.exists():
            continue
        sessions = []
        for session_dir in sorted(participant_root.glob("sessions/*")):
            session_meta = {"session_dir": str(session_dir), "files": sorted(p.name for p in session_dir.rglob("*"))}
            sessions.append(session_meta)
        matches.append({
            "study_id": str(study_path.parent.name),
            "participant_id": participant_id,
            "session_count": len(sessions),
            "sessions": sessions,
        })

    return {"participant_id": participant_id, "study_ids": [m["study_id"] for m in matches], "sessions": matches}


def list_participants_for_study(study_id: str, data_dir: Path | None = None) -> list[dict[str, Any]]:
    base = _safe_data_dir(data_dir)
    study_dir = base / "saved_results" / study_id
    if not study_dir.exists():
        return []

    participants: list[dict[str, Any]] = []
    participants_root = study_dir / "participants"
    if not participants_root.exists():
        return participants

    for participant_dir in sorted(participants_root.iterdir()):
        if not participant_dir.is_dir():
            continue
        sessions = [
            str(session_dir.name)
            for session_dir in sorted((participant_dir / "sessions").glob("*"))
            if session_dir.is_dir()
        ]
        participants.append({
            "participant_id": participant_dir.name,
            "study_id": study_id,
            "sessions": sessions,
        })
    return participants


class ParticipantRegistry:
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = _safe_data_dir(data_dir)
        self.registry_file = self.data_dir / "participant-registry.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)

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
            "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }
        registry["participants"] = [item for item in registry.get("participants", []) if item.get("participant_id") != participant_id]
        registry["participants"].append(record)
        self._write_registry(registry)
        return record

    def delete_participant(self, participant_id: str, study_id: str | None = None, reason: str = "user requested deletion", archive_first: bool = False) -> dict[str, Any]:
        result = find_participant_in_results(participant_id, self.data_dir)
        archive_path = None
        if archive_first:
            archive_path = self.data_dir / f"{participant_id}-archive.zip"
            shutil.make_archive(str(self.data_dir / participant_id), "zip", str(self.data_dir / "saved_results"))

        for item in result.get("sessions", []):
            for entry in item.get("sessions", []):
                folder = Path(entry["session_dir"])
                if folder.exists():
                    shutil.rmtree(folder)

        registry = self._read_registry()
        registry["participants"] = [item for item in registry.get("participants", []) if item.get("participant_id") != participant_id]
        self._write_registry(registry)

        return {
            "status": "deleted",
            "participant_id": participant_id,
            "study_id": study_id,
            "reason": reason,
            "archive_path": str(archive_path) if archive_path else None,
            "deleted_sessions": result.get("sessions", []),
        }


def generate_participant_id(study_id: str | None = None, prefix: str = "P") -> str:
    return ParticipantRegistry().register(participant_id=f"{prefix}-{uuid.uuid4().hex[:12]}", study_id=study_id)["participant_id"]


def list_participants_for_study(study_id: str, data_dir: Path | None = None) -> list[dict[str, Any]]:
    return ParticipantRegistry(data_dir=data_dir)._read_registry().get("participants", [])
