"""Update an installation that was extracted from a release archive (no git).

The steps are shared by the in-app updater (``update_service`` + the detached
``installer`` helper) and the terminal updater (``tools/update_study_runner.py``):

1. ``stage_archive``: verify the downloaded archive's SHA-256 and extract it
   safely into ``.tools/update-staging/<version>/`` (server may still run).
2. ``swap_program_files``: with the server stopped, move the old program files
   into ``.tools/update-backup/<version>/`` and the new ones into place. Only
   renames on the same drive: fast, and fully reversible via ``rollback``.
3. ``merge_new_content``: add shipped example content that does not exist yet;
   never overwrite anything the operator has.

User data is never moved: ``software/study_content`` (studies, settings,
credentials, logos, fonts, certificates), ``software/saved_results``, the
generated ``software/.build`` core, ``.venv``, ``.tools`` and ``.git``.

Standard library only: this runs inside the detached helper process too.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import time
from typing import Any
import zipfile

RELEASE_INFO_NAME = "study-runner-release.json"
PRESERVED_TOP_LEVEL = frozenset({".venv", ".tools", ".git", "software"})
PRESERVED_SOFTWARE = frozenset({"study_content", "saved_results", ".build"})
MAX_ARCHIVE_MEMBERS = 20_000


class ArchiveUpdateError(RuntimeError):
    """A plain-language reason the update could not be applied."""


def install_kind(install_root: Path) -> str:
    """``git``, ``archive`` or ``unknown`` for this installation."""
    root = Path(install_root)
    if (root / ".git").exists():
        return "git"
    if (root / RELEASE_INFO_NAME).is_file():
        return "archive"
    return "unknown"


def archive_name_for_platform() -> str:
    return "study-runner-source.zip" if os.name == "nt" else "study-runner-source.tar.gz"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage_archive(archive_path: Path, expected_sha256: str, staging_parent: Path, version: str) -> Path:
    """Verify and extract a release archive; return the extracted release root."""
    actual = sha256_file(archive_path)
    if actual != str(expected_sha256 or "").strip().lower():
        raise ArchiveUpdateError(
            f"The downloaded update is damaged or not the published release (SHA-256 {actual})."
        )
    staging_parent = Path(staging_parent)
    target = staging_parent / version
    temporary = staging_parent / f".{version}.extracting"
    for leftover in (target, temporary):
        if leftover.exists():
            shutil.rmtree(leftover)
    temporary.mkdir(parents=True)
    try:
        _safe_extract(Path(archive_path), temporary)
        roots = [entry for entry in temporary.iterdir()]
        if len(roots) != 1 or not roots[0].is_dir():
            raise ArchiveUpdateError("The update archive must contain exactly one folder.")
        release_root = roots[0]
        if not (release_root / "software" / "server.py").is_file():
            raise ArchiveUpdateError("The update archive is not a Study Runner release.")
        info = _read_json(release_root / RELEASE_INFO_NAME)
        if str(info.get("version") or "") != version:
            raise ArchiveUpdateError(
                f"The update archive is version {info.get('version')!r}, expected {version}."
            )
        temporary.replace(target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target / release_root.name


def swap_program_files(install_root: Path, release_root: Path, backup_root: Path) -> list[dict[str, str]]:
    """Replace program files with the staged release; return a rollback journal."""
    install_root = Path(install_root)
    release_root = Path(release_root)
    backup_root = Path(backup_root)
    backup_root.mkdir(parents=True, exist_ok=True)
    journal: list[dict[str, str]] = []
    try:
        for relative in program_entries(release_root):
            current = install_root / relative
            incoming = release_root / relative
            backup = backup_root / relative
            if current.exists() or current.is_symlink():
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(current, backup)
                journal.append({"action": "backed_up", "path": relative})
            current.parent.mkdir(parents=True, exist_ok=True)
            os.replace(incoming, current)
            journal.append({"action": "installed", "path": relative})
    except OSError as error:
        rollback(install_root, backup_root, journal)
        raise ArchiveUpdateError(
            f"Could not replace the program files ({error}). Nothing was changed; "
            "close programs that use the Study Runner folder and try again."
        ) from error
    return journal


def rollback(install_root: Path, backup_root: Path, journal: list[dict[str, str]]) -> None:
    """Undo ``swap_program_files`` step by step, newest first."""
    for entry in reversed(journal):
        current = Path(install_root) / entry["path"]
        if entry["action"] == "installed" and (current.exists() or current.is_symlink()):
            if current.is_dir() and not current.is_symlink():
                shutil.rmtree(current)
            else:
                current.unlink()
        elif entry["action"] == "backed_up":
            backup = Path(backup_root) / entry["path"]
            if backup.exists() or backup.is_symlink():
                os.replace(backup, current)


def program_entries(release_root: Path) -> list[str]:
    """Relative paths the update replaces: everything except user data."""
    entries: list[str] = []
    for entry in sorted(Path(release_root).iterdir()):
        if entry.name not in PRESERVED_TOP_LEVEL:
            entries.append(entry.name)
    software = Path(release_root) / "software"
    if software.is_dir():
        for entry in sorted(software.iterdir()):
            if entry.name not in PRESERVED_SOFTWARE:
                entries.append(f"software/{entry.name}")
    return entries


def merge_new_content(release_root: Path, install_root: Path) -> list[str]:
    """Add shipped content the install lacks; never overwrite the operator's files.

    New example studies or default settings files are copied only where no
    file exists yet. The curated demo result is copied only when the install
    has no demo folder at all, so a deleted demo is not partially restored.
    """
    added: list[str] = []
    content_source = Path(release_root) / "software" / "study_content"
    content_target = Path(install_root) / "software" / "study_content"
    if content_source.is_dir():
        for source in sorted(content_source.rglob("*")):
            if not source.is_file():
                continue
            target = content_target / source.relative_to(content_source)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            added.append(f"software/study_content/{source.relative_to(content_source).as_posix()}")
    demo_source = Path(release_root) / "software" / "saved_results" / "Demo_Completed_Study"
    demo_target = Path(install_root) / "software" / "saved_results" / "Demo_Completed_Study"
    if demo_source.is_dir() and not demo_target.exists():
        shutil.copytree(demo_source, demo_target)
        added.append("software/saved_results/Demo_Completed_Study")
    return added


def read_installed_version(install_root: Path) -> str:
    info = _read_json(Path(install_root) / RELEASE_INFO_NAME)
    return str(info.get("version") or "")


def _safe_extract(archive_path: Path, destination: Path) -> None:
    destination = destination.resolve()
    if archive_path.name.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            _check_member_count(len(members))
            for member in members:
                _check_member_name(member.filename, destination)
                if ((member.external_attr >> 16) & 0o170000) == 0o120000:
                    raise ArchiveUpdateError("The update archive contains a link, which is not allowed.")
            archive.extractall(destination)
        return
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        _check_member_count(len(members))
        for member in members:
            _check_member_name(member.name, destination)
            if not (member.isfile() or member.isdir()):
                raise ArchiveUpdateError("The update archive contains a link or special file.")
        archive.extractall(destination, filter="data")


def _check_member_count(count: int) -> None:
    if count > MAX_ARCHIVE_MEMBERS:
        raise ArchiveUpdateError("The update archive contains too many files.")


def _check_member_name(name: str, destination: Path) -> None:
    posix = PurePosixPath(name.replace("\\", "/"))
    has_drive = bool(posix.parts) and ":" in posix.parts[0]
    if posix.is_absolute() or ".." in posix.parts or has_drive:
        raise ArchiveUpdateError(f"The update archive contains an unsafe path: {name}")
    target = (destination / name).resolve()
    if target != destination and destination not in target.parents:
        raise ArchiveUpdateError(f"The update archive contains an unsafe path: {name}")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def timestamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
