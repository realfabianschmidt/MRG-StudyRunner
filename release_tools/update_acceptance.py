#!/usr/bin/env python3
"""Release acceptance: update an installed release to a synthetic next version.

Runs the installed release's own terminal updater (``tools/update-windows.cmd``
or ``tools/update-macos.sh``) against a local HTTP server that serves a copy of
the release archive with the next patch version. Download, SHA-256 check,
staging, program-file swap, content merge and install script are the real
update path; only the source differs (``STUDY_RUNNER_SOURCE_RELEASE_URL``).

Afterwards it checks that the new version is active, that studies, settings and
results are byte-for-byte unchanged, and that the old version was kept as a
backup. ``--disable-windows-long-paths`` turns the LongPathsEnabled policy off
before the update runs, like on a stock lab PC.

Standard library only.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import zipfile

RELEASE_INFO_NAME = "study-runner-release.json"
METADATA_NAME = "study-runner-source-release.json"
VERSION_FILE = Path("software") / "study_runner" / "version.py"
MARKERS = (
    Path("software") / "study_content" / "studies" / "update-acceptance-marker.txt",
    Path("software") / "saved_results" / "update-acceptance" / "result.json",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--install-root", required=True, type=Path, help="the installed release to update")
    parser.add_argument("--archive", required=True, type=Path, help="this platform's release archive")
    parser.add_argument("--disable-windows-long-paths", action="store_true")
    args = parser.parse_args(argv)

    install_root = args.install_root.resolve()
    current = json.loads((install_root / RELEASE_INFO_NAME).read_text(encoding="utf-8"))["version"]
    next_version = bump_patch(current)
    user_data = write_markers(install_root) | snapshot(install_root / "software" / "study_content")

    with tempfile.TemporaryDirectory(prefix="sr-upd-") as temporary:
        serve_dir = Path(temporary) / "serve"
        serve_dir.mkdir()
        archive = build_next_archive(args.archive.resolve(), Path(temporary) / "work", serve_dir, next_version)
        write_metadata(install_root, serve_dir, archive, next_version)
        if args.disable_windows_long_paths:
            disable_windows_long_paths()
        server = start_server(serve_dir)
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/{METADATA_NAME}"
            print(f"Updating {current} -> {next_version} from {url}", flush=True)
            run_updater(install_root, url)
        finally:
            server.shutdown()

    failures = verify(install_root, current, next_version, user_data)
    if failures:
        for failure in failures:
            print(f"FAILED: {failure}", file=sys.stderr)
        return 1
    print(f"Update {current} -> {next_version} verified: new version active, user data unchanged, backup kept.")
    return 0


def bump_patch(version: str) -> str:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        raise SystemExit(f"Unexpected release version: {version!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return f"{major}.{minor}.{patch + 1}"


def write_markers(install_root: Path) -> dict[str, str]:
    """User data the update must not touch, with its hashes."""
    hashes: dict[str, str] = {}
    for relative in MARKERS:
        path = install_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"operator data {relative.as_posix()}\n", encoding="utf-8")
        hashes[relative.as_posix()] = sha256(path)
    return hashes


def snapshot(folder: Path) -> dict[str, str]:
    root = folder.parents[1]
    return {path.relative_to(root).as_posix(): sha256(path) for path in sorted(folder.rglob("*")) if path.is_file()}


def build_next_archive(archive: Path, work: Path, serve_dir: Path, version: str) -> Path:
    """A copy of the release archive that claims ``version`` everywhere the updater looks."""
    work.mkdir()
    extract(archive, work)
    (old_root,) = [entry for entry in work.iterdir() if entry.is_dir()]
    new_root = old_root.with_name(f"MRG-StudyRunner-{version}")
    old_root.rename(new_root)
    info_path = new_root / RELEASE_INFO_NAME
    info = json.loads(info_path.read_text(encoding="utf-8"))
    info["version"] = version
    info_path.write_text(json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (new_root / VERSION_FILE).write_text(
        f'"""Canonical Study Runner application version."""\n\n__version__ = "{version}"\n', encoding="utf-8"
    )
    target = serve_dir / archive.name
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as handle:
            for path in sorted(new_root.rglob("*")):
                handle.write(path, path.relative_to(work).as_posix())
    else:
        with tarfile.open(target, "w:gz") as handle:
            handle.add(new_root, arcname=new_root.name)
    return target


def extract(archive: Path, destination: Path) -> None:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as handle:
            handle.extractall(destination)
    else:
        with tarfile.open(archive, "r:gz") as handle:
            handle.extractall(destination, filter="data")


def write_metadata(install_root: Path, serve_dir: Path, archive: Path, version: str) -> None:
    info = json.loads((install_root / RELEASE_INFO_NAME).read_text(encoding="utf-8"))
    metadata = {
        "version": version,
        "tag": f"app-v{version}",
        "repository": info.get("repository") or "",
        "artifacts": {archive.name: {"sha256": sha256(archive), "size": archive.stat().st_size}},
    }
    (serve_dir / METADATA_NAME).write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def disable_windows_long_paths() -> None:
    if os.name != "nt":
        return
    subprocess.run(
        ["reg", "add", r"HKLM\SYSTEM\CurrentControlSet\Control\FileSystem", "/v", "LongPathsEnabled",
         "/t", "REG_DWORD", "/d", "0", "/f"],
        check=True,
    )
    print("Windows long paths disabled for the update (as on a stock PC).", flush=True)


def start_server(serve_dir: Path) -> http.server.ThreadingHTTPServer:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(serve_dir))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def run_updater(install_root: Path, metadata_url: str) -> None:
    env = {**os.environ, "STUDY_RUNNER_SOURCE_RELEASE_URL": metadata_url}
    if os.name == "nt":
        cmd_exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
        command = [cmd_exe, "/d", "/c", str(install_root / "tools" / "update-windows.cmd")]
    else:
        command = ["bash", str(install_root / "tools" / "update-macos.sh")]
    result = subprocess.run(command, cwd=str(install_root), env=env)
    if result.returncode != 0:
        raise SystemExit(f"The updater exited with code {result.returncode}.")


def verify(install_root: Path, old: str, new: str, user_data: dict[str, str]) -> list[str]:
    failures: list[str] = []
    info = json.loads((install_root / RELEASE_INFO_NAME).read_text(encoding="utf-8"))
    if info.get("version") != new:
        failures.append(f"{RELEASE_INFO_NAME} says {info.get('version')!r}, expected {new}")
    if f'"{new}"' not in (install_root / VERSION_FILE).read_text(encoding="utf-8"):
        failures.append(f"{VERSION_FILE.as_posix()} is not {new}")
    for relative, digest in user_data.items():
        path = install_root / relative
        if not path.is_file():
            failures.append(f"user file vanished: {relative}")
        elif sha256(path) != digest:
            failures.append(f"user file changed: {relative}")
    backups = sorted((install_root / ".tools" / "update-backup").glob(f"{old}-*"))
    if not backups or not (backups[-1] / "software" / "server.py").is_file():
        failures.append(f"no backup of {old} in .tools/update-backup")
    if not (install_root / "software" / "saved_results" / "Demo_Completed_Study").is_dir():
        failures.append("the demo result is gone")
    if not (install_root / ".venv").is_dir():
        failures.append(".venv is gone")
    return failures


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
