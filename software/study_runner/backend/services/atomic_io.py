"""Crash-safe JSON writes for study data.

Every file that holds participant results must be written atomically:
the file on disk is always either the previous complete version or the
new complete version, never a half-written one. This mirrors the
temp-file + os.replace pattern already used by the updater staging and
the DeepFace model download.
"""
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, payload: Any, *, indent: int = 2) -> None:
    """Write ``payload`` as JSON to ``path`` via a same-directory temp file."""
    encoded = json.dumps(payload, indent=indent, ensure_ascii=False).encode("utf-8")
    atomic_write_bytes(path, encoded)


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Write bytes via a flushed same-directory file and atomic replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as file_handle:
            file_handle.write(payload)
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(temp_path, path)
        _fsync_parent_directory(path.parent)
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def _fsync_parent_directory(directory: Path) -> None:
    """Persist the directory entry after replace on platforms that support it."""

    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        directory_fd = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
