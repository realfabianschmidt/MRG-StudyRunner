from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .core.installer import (
    install_release,
    list_available_releases,
    list_installed_versions,
    remove_installation,
    repair_installation,
)
from .core.participant_registry import (
    ParticipantRegistry,
    anonymize_participant,
    find_participant_in_results,
    generate_participant_id,
    list_participants_for_study,
)

DEFAULT_DATA_DIR = Path(os.environ.get("STUDY_RUNNER_DATA_DIR", Path.home() / ".study-runner-admin"))


def load_admin_state(install_root: Path | str) -> dict[str, Any]:
    root = Path(install_root).expanduser().resolve()
    state_path = root / "study-runner-admin-state.json"
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def generate_participant_id_for_study(study_id: str | None = None, prefix: str = "P") -> str:
    return generate_participant_id(prefix=prefix)


def list_participants(study_id: str, data_dir: Path | str | None = None) -> list[dict[str, Any]]:
    return list_participants_for_study(study_id=study_id, data_dir=Path(data_dir) if data_dir is not None else None)


def find_participant(participant_id: str, data_dir: Path | str | None = None) -> dict[str, Any]:
    return find_participant_in_results(participant_id=participant_id, data_dir=Path(data_dir) if data_dir is not None else None)


def delete_participant(
    participant_id: str,
    *,
    study_id: str | None = None,
    data_dir: Path | str | None = None,
    reason: str = "user requested deletion",
    archive_first: bool = False,
) -> dict[str, Any]:
    registry = ParticipantRegistry(data_dir=Path(data_dir) if data_dir is not None else None)
    return registry.delete_participant(
        participant_id=participant_id,
        study_id=study_id,
        reason=reason,
        archive_first=archive_first,
    )


def anonymize_participant_record(
    participant_id: str,
    *,
    study_id: str | None = None,
    data_dir: Path | str | None = None,
    reason: str = "consent withdrawn",
) -> dict[str, Any]:
    return anonymize_participant(
        participant_id=participant_id,
        study_id=study_id,
        data_dir=Path(data_dir) if data_dir is not None else None,
        reason=reason,
    )


__all__ = [
    "DEFAULT_DATA_DIR",
    "load_admin_state",
    "generate_participant_id_for_study",
    "list_participants",
    "find_participant",
    "delete_participant",
    "anonymize_participant_record",
    "install_release",
    "list_available_releases",
    "list_installed_versions",
    "repair_installation",
    "remove_installation",
]
