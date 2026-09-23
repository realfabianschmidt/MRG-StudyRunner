#!/usr/bin/env python3
"""Update Study Runner from the terminal (release archive or git clone).

Run through ``tools/update-macos.sh`` or ``tools\\update-windows.cmd`` while
Study Runner is stopped. Release archives: download the latest release, check
its SHA-256, replace the program files (studies, results, settings, credentials
and ``.venv`` stay untouched; the old version is kept in
``.tools/update-backup``), install, done. Git clones: ``git pull --ff-only``
and install. If installing the new version fails, the old one is restored.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys

INSTALL_ROOT = Path(__file__).resolve().parents[1]
SOFTWARE_ROOT = INSTALL_ROOT / "software"
if str(SOFTWARE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOFTWARE_ROOT))

from study_runner.runtime_core.settings import update_service  # noqa: E402
from study_runner.updates import archive_update  # noqa: E402
from study_runner.version import __version__  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only report whether an update is available")
    parser.add_argument("--port", type=int, default=int(os.getenv("STUDY_RUNNER_PORT") or 3000))
    args = parser.parse_args(argv)
    try:
        return run(check_only=args.check, port=args.port)
    except (update_service.UpdateError, archive_update.ArchiveUpdateError, RuntimeError) as error:
        print(f"\nUpdate failed: {error}", file=sys.stderr)
        return 1


def run(*, check_only: bool, port: int) -> int:
    kind = archive_update.install_kind(INSTALL_ROOT)
    metadata = update_service.fetch_source_release_metadata(update_service.get_source_release_url())
    latest = str(metadata.get("version") or "")
    print(f"Installed: {__version__}   Latest release: {latest or '?'}")
    if update_service.compare_versions(latest, __version__) <= 0:
        print("Study Runner is up to date.")
        return 0
    if check_only:
        print("An update is available. Run this command without --check to install it.")
        return 0
    if _server_is_running(port):
        raise RuntimeError(
            f"Study Runner is still running (port {port}). Stop it with Ctrl+C in its window, then run this again."
        )
    if kind == "git":
        print("Git clone: pulling the latest release ...")
        _run(["git", "pull", "--ff-only"])
        _run_install_script()
    elif kind == "archive":
        _update_archive_install(metadata, latest)
    else:
        raise RuntimeError(
            "This folder is neither a release archive nor a git clone. Download the latest release archive instead."
        )
    start = "bash tools/start-macos.sh" if os.name != "nt" else r".\tools\start-windows.cmd"
    print(f"\nStudy Runner {latest} is installed. Start it with:\n  {start}")
    return 0


def _update_archive_install(metadata: dict, version: str) -> None:
    import requests

    name = archive_update.archive_name_for_platform()
    artifact = (metadata.get("artifacts") or {}).get(name) or {}
    expected = str(artifact.get("sha256") or "")
    if len(expected) != 64:
        raise RuntimeError(f"The release metadata has no checksum for {name}.")
    repository = str(metadata.get("repository") or "realfabianschmidt/MRG-StudyRunner")
    tag = str(metadata.get("tag") or f"app-v{version}")
    url = f"https://github.com/{repository}/releases/download/{tag}/{name}"
    staging = INSTALL_ROOT / ".tools" / "update-staging"
    staging.mkdir(parents=True, exist_ok=True)
    download = staging / name
    print(f"Downloading {url} ...")
    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        with download.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                handle.write(chunk)
    print("Checking the download ...")
    try:
        release_root = archive_update.stage_archive(download, expected, staging, version)
    finally:
        download.unlink(missing_ok=True)

    previous = archive_update.read_installed_version(INSTALL_ROOT) or __version__
    backup = INSTALL_ROOT / ".tools" / "update-backup" / f"{previous}-{archive_update.timestamp()}"
    print("Replacing the program files (your studies, results and settings stay) ...")
    journal = archive_update.swap_program_files(INSTALL_ROOT, release_root, backup)
    try:
        archive_update.merge_new_content(release_root, INSTALL_ROOT)
        _run_install_script()
    except Exception:
        print(f"Installing the new version failed; restoring {previous} ...", file=sys.stderr)
        archive_update.rollback(INSTALL_ROOT, backup, journal)
        raise
    print(f"The previous version is kept in {backup} (safe to delete later).")


def _run_install_script() -> None:
    if os.name == "nt":
        cmd_exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
        _run([cmd_exe, "/d", "/c", str(INSTALL_ROOT / "tools" / "install-windows.cmd")])
    else:
        _run(["/bin/bash", str(INSTALL_ROOT / "tools" / "install-macos.sh")])


def _run(command: list[str]) -> None:
    result = subprocess.run(command, cwd=str(INSTALL_ROOT))
    if result.returncode != 0:
        raise RuntimeError(f"{Path(command[0]).name} exited with code {result.returncode}")


def _server_is_running(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
