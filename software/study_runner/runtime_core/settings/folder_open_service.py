"""Open one validated Study Runner results folder on the server computer."""
from __future__ import annotations

import os
from pathlib import Path
import platform
import subprocess


class FolderOpenError(RuntimeError):
    """Plain-language error safe to return to the operator."""


def resolve_session_folder(data_dir: Path, session_path: str) -> Path:
    """Resolve one canonical v3 session without accepting traversal.

    ``session_path`` comes from durable finalization state or the admin
    session index, but this boundary still treats it as untrusted. It must
    name the exact v3 layout below ``DATA_DIR`` (``study/participants/<id>/
    sessions/<folder>``) rather than a study- or participant-wide directory --
    there is no flat legacy layout to resolve any more.
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


def open_session_folder(data_dir: Path, session_path: str) -> dict[str, str | bool]:
    """Open the exact canonical v3 session folder on the server computer."""

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
