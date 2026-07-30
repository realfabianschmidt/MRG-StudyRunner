"""Open one validated Study Runner results folder on the server computer."""
from __future__ import annotations

import os
from pathlib import Path
import platform
import subprocess

from .results_service import sanitize_identifier_for_filename


class FolderOpenError(RuntimeError):
    """Plain-language error safe to return to the operator."""


def resolve_results_folder(data_dir: Path, study_id: str, participant_id: str) -> Path:
    normalized_study = str(study_id or "").strip()
    normalized_participant = str(participant_id or "").strip()
    if not normalized_study or sanitize_identifier_for_filename(normalized_study) != normalized_study:
        raise FolderOpenError("A valid study_id is required.")
    if not normalized_participant or sanitize_identifier_for_filename(normalized_participant) != normalized_participant:
        raise FolderOpenError("A valid participant_id is required.")

    root = Path(data_dir).resolve()
    target = (root / normalized_study / normalized_participant).resolve()
    if not target.is_relative_to(root) or not target.is_dir():
        raise FolderOpenError("The results folder was not found on this computer.")
    return target


def open_results_folder(data_dir: Path, study_id: str, participant_id: str) -> dict[str, str | bool]:
    target = resolve_results_folder(data_dir, study_id, participant_id)
    system = platform.system().lower()
    try:
        if system == "windows":
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif system == "darwin":
            subprocess.Popen(
                ["open", str(target)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        else:
            raise FolderOpenError("Opening result folders is supported on Windows and macOS only.")
    except OSError as error:
        raise FolderOpenError(f"Could not open the results folder: {error}") from error
    return {"ok": True, "path": str(target)}
