"""Open one validated Study Runner results folder on the server computer."""
from __future__ import annotations

import os
from pathlib import Path
import platform
import subprocess

from ..studies.results_service import sanitize_identifier_for_filename


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


def resolve_session_folder(data_dir: Path, session_path: str) -> Path:
    """Resolve one canonical finalization session without accepting traversal.

    ``session_path`` comes from the durable finalization state, but this
    boundary still treats it as untrusted.  It must name the exact v3 layout
    below ``DATA_DIR`` rather than a study- or participant-wide directory.
    """

    normalized = str(session_path or "").strip().replace("\\", "/")
    relative = Path(normalized)
    parts = relative.parts
    if (
        not normalized
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
        or len(parts) != 5
        or parts[1] != "participants"
        or parts[3] != "sessions"
    ):
        raise FolderOpenError("A valid finalization session path is required.")

    root = Path(data_dir).resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.is_dir():
        raise FolderOpenError("The session folder was not found on this computer.")
    return target


def open_results_folder(data_dir: Path, study_id: str, participant_id: str) -> dict[str, str | bool]:
    target = resolve_results_folder(data_dir, study_id, participant_id)
    return _open_folder(target)


def open_session_folder(data_dir: Path, session_path: str) -> dict[str, str | bool]:
    """Open the exact canonical session associated with a finalization job."""

    return _open_folder(resolve_session_folder(data_dir, session_path))


def _open_folder(target: Path) -> dict[str, str | bool]:
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
        elif system == "linux":
            subprocess.Popen(
                ["xdg-open", str(target)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        else:
            raise FolderOpenError("Opening result folders is not supported on this operating system.")
    except OSError as error:
        raise FolderOpenError(f"Could not open the results folder: {error}") from error
    return {"ok": True, "path": str(target)}
