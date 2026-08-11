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
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


def atomic_write_json(
    path: Path,
    payload: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    trailing_newline: bool = False,
) -> None:
    """Write ``payload`` as JSON to ``path`` via a same-directory temp file."""
    text = json.dumps(payload, indent=indent, ensure_ascii=ensure_ascii)
    if trailing_newline:
        text += "\n"
    encoded = text.encode("utf-8")
    atomic_write_bytes(path, encoded)


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Write bytes via a flushed same-directory file and atomic replace."""
    path = Path(path)
    with atomic_path_lock(path):
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


@contextmanager
def atomic_path_lock(path: Path) -> Iterator[None]:
    """Serialize an in-process read/modify/write transaction for one path.

    ``atomic_write_*`` acquires this lock itself.  Callers that have to read a
    JSON document and then write a derived value may hold the same re-entrant
    lock around the complete transaction, preventing concurrent Flask request
    threads from silently dropping each other's changes.
    """

    key = _lock_key(path)
    with _LOCKS_GUARD:
        lock = _PATH_LOCKS.setdefault(key, threading.RLock())
    with lock:
        yield


def _lock_key(path: Path) -> str:
    normalized = str(Path(path).resolve(strict=False))
    return os.path.normcase(normalized)


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
