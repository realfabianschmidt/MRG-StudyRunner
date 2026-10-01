#!/usr/bin/env python3
"""Keep every plugin's version honest, so a session can cite it.

Each plugin's version lives in its ``manifest.json`` (MAJOR.MINOR.PATCH). This
tool records, per plugin, that version together with a fingerprint of the
plugin's files and of its data contract in
``software/study_runner/plugins/plugin_versions.lock.json``:

    python tools/plugin_versions.py --check    # fails when a plugin changed without a new version
    python tools/plugin_versions.py --update   # after raising a version: record it
    python tools/plugin_versions.py --list     # all plugins and versions

The rule (CONTRIBUTING.md section 11):

    MAJOR  the recorded data changes meaning (stream, channel, unit, rate,
           backup projection, answer format)
    MINOR  a new feature that leaves existing data unchanged
    PATCH  a fix without effect on the recorded data

``--update`` refuses a plugin whose files changed while its version did not, and
a plugin whose data contract changed without a MAJOR step.

Which files count: in a git checkout, the plugin folder's files that git tracks
or would track (ignored logs, caches and runtime data never count). Text line
endings are normalised, so a Windows and a Linux checkout give the same
fingerprint. Outside git -- an extracted release archive, which may leave out
export-ignored reference files -- only the versions are compared.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_ROOT = REPO_ROOT / "software" / "study_runner" / "plugins"
LOCK_FILE = PLUGINS_ROOT / "plugin_versions.lock.json"
LOCK_SCHEMA = "study-runner/plugin-versions-lock/v1"
CATEGORIES = ("sensors", "cards", "destinations", "outputs")
# The parts of a manifest that define what a plugin records or answers. A
# change here changes the data, so it needs a MAJOR version step.
CONTRACT_FIELDS = ("streams",)
CONTRACT_CAPABILITIES = ("recording_source", "backup_projection", "card_contract")
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
NEVER_COUNTED = ("__pycache__/", "/logs/", "/runtime/", "/recordings/")


class PluginVersionError(RuntimeError):
    """A plugin's recorded version no longer describes its files."""


# ------------------------------------------------------------------ discovery

def plugin_directories(plugins_root: Path = PLUGINS_ROOT) -> dict[str, Path]:
    """``plugin_key`` -> folder, for every plugin discovery would load."""
    found: dict[str, Path] = {}
    for category in CATEGORIES:
        root = plugins_root / category
        if not root.is_dir():
            continue
        for folder in sorted(root.iterdir()):
            if not folder.is_dir() or folder.name.startswith((".", "_")) or (folder / ".pluginignore").is_file():
                continue
            manifest = _read_manifest(folder)
            if manifest is not None:
                found[str(manifest["plugin_key"])] = folder
    return found


def _read_manifest(folder: Path) -> dict[str, Any] | None:
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) and manifest.get("plugin_key") else None


# --------------------------------------------------------------- fingerprints

def is_git_checkout(repo_root: Path = REPO_ROOT) -> bool:
    """True only when ``repo_root`` itself is a git work tree's top level.

    An extracted release archive inside some other repository must not borrow
    that repository's idea of which files exist.
    """
    return _is_git_top_level(str(Path(repo_root).resolve()))


@functools.lru_cache(maxsize=8)
def _is_git_top_level(repo_root: str) -> bool:
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=str(repo_root), capture_output=True, check=True,
        ).stdout.decode("utf-8").strip()
    except (OSError, subprocess.CalledProcessError):
        return False
    return bool(top) and Path(top).resolve() == Path(repo_root)


def plugin_files(folder: Path, repo_root: Path = REPO_ROOT) -> list[Path] | None:
    """The plugin's own files, or ``None`` outside a git checkout."""
    if not is_git_checkout(repo_root):
        return None
    try:
        listed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", str(folder)],
            cwd=str(repo_root), capture_output=True, check=True,
        ).stdout.decode("utf-8")
    except (OSError, subprocess.CalledProcessError):
        return None
    files = []
    for name in sorted({item for item in listed.split("\0") if item}):
        path = repo_root / name
        relative = "/" + path.relative_to(folder).as_posix()
        if path.is_file() and not any(part in relative for part in NEVER_COUNTED) and not relative.endswith(".pyc"):
            files.append(path)
    return files


def content_sha256(folder: Path, files: Iterable[Path]) -> str:
    """One fingerprint over paths and contents; text line endings normalised."""
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(folder).as_posix()):
        data = path.read_bytes()
        if b"\0" not in data:  # text, as git decides it: CRLF and LF are the same file
            data = data.replace(b"\r\n", b"\n")
        digest.update(path.relative_to(folder).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def contract_sha256(manifest: dict[str, Any]) -> str:
    capabilities = manifest.get("capabilities") or {}
    contract = {
        **{field: manifest.get(field) for field in CONTRACT_FIELDS},
        **{name: capabilities.get(name) for name in CONTRACT_CAPABILITIES if isinstance(capabilities, dict)},
    }
    return hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def current_state(plugins_root: Path = PLUGINS_ROOT, repo_root: Path = REPO_ROOT) -> dict[str, dict[str, Any]]:
    """What the plugins look like now; ``content_sha256`` is None outside git."""
    state = {}
    for key, folder in plugin_directories(plugins_root).items():
        manifest = _read_manifest(folder) or {}
        files = plugin_files(folder, repo_root)
        state[key] = {
            "version": str(manifest.get("version") or ""),
            "path": folder.relative_to(repo_root).as_posix(),
            "content_sha256": content_sha256(folder, files) if files is not None else None,
            "contract_sha256": contract_sha256(manifest),
        }
    return state


# ----------------------------------------------------------- check / update

def semver(version: str) -> tuple[int, int, int]:
    match = SEMVER.fullmatch(str(version))
    if not match:
        raise PluginVersionError(f"{version!r} is not MAJOR.MINOR.PATCH")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def read_lock(lock_file: Path = LOCK_FILE) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(lock_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    plugins = payload.get("plugins") if isinstance(payload, dict) else None
    return plugins if isinstance(plugins, dict) else {}


def problems(current: dict[str, dict[str, Any]], lock: dict[str, dict[str, Any]]) -> list[str]:
    """Every way the lock no longer describes the plugins, with what to do."""
    found: list[str] = []
    for key, now in sorted(current.items()):
        manifest = f"{now['path']}/manifest.json"
        try:
            semver(now["version"])
        except PluginVersionError as error:
            found.append(f"{key}: version {error} in {manifest}.")
            continue
        recorded = lock.get(key)
        if recorded is None:
            found.append(f"{key}: new plugin. Record it with: python tools/plugin_versions.py --update")
            continue
        missing_step = _step_problems(key, now, recorded, manifest)
        if missing_step:
            found.extend(missing_step)
            continue
        content_differs = now["content_sha256"] is not None and now["content_sha256"] != recorded.get("content_sha256")
        if (
            now["version"] != recorded.get("version")
            or now["contract_sha256"] != recorded.get("contract_sha256")
            or content_differs
        ):
            found.append(
                f"{key}: version {now['version']} is not recorded yet. "
                "Run: python tools/plugin_versions.py --update"
            )
    for key in sorted(set(lock) - set(current)):
        found.append(f"{key}: in the lock but no longer installed. Run: python tools/plugin_versions.py --update")
    return found


def _step_problems(key: str, now: dict[str, Any], recorded: dict[str, Any], manifest: str) -> list[str]:
    """A changed plugin needs a higher version; a changed data contract a higher MAJOR."""
    old, new = semver(recorded.get("version") or "0.0.0"), semver(now["version"])
    if new < old:
        return [f"{key}: version went down from {recorded.get('version')} to {now['version']} in {manifest}."]
    contract_changed = now["contract_sha256"] != recorded.get("contract_sha256")
    if contract_changed and new[0] == old[0]:
        return [
            f"{key}: its data contract (streams, recording, backup or card contract) changed, which changes the "
            f"recorded data. Raise the MAJOR version in {manifest} (now {now['version']}), then run "
            "python tools/plugin_versions.py --update"
        ]
    content_changed = now["content_sha256"] is not None and now["content_sha256"] != recorded.get("content_sha256")
    if content_changed and new == old:
        return [
            f"{key}: its files changed since version {now['version']} was recorded. Raise the version in "
            f"{manifest} (MAJOR: recorded data changes meaning, MINOR: new compatible feature, PATCH: fix "
            "without effect on data - see CONTRIBUTING.md section 11), then run python tools/plugin_versions.py --update"
        ]
    return []


def update(lock_file: Path = LOCK_FILE, plugins_root: Path = PLUGINS_ROOT, repo_root: Path = REPO_ROOT) -> list[str]:
    """Record the current versions; refuse when a version step is missing."""
    current = current_state(plugins_root, repo_root)
    if any(now["content_sha256"] is None for now in current.values()):
        raise PluginVersionError("--update needs a git checkout (it fingerprints the files git tracks).")
    lock = read_lock(lock_file)
    refused = [
        message
        for key, now in sorted(current.items())
        if key in lock
        for message in _step_problems(key, now, lock[key], f"{now['path']}/manifest.json")
    ]
    if refused:
        raise PluginVersionError("\n".join(refused))
    payload = {
        "schema": LOCK_SCHEMA,
        "note": "Written by tools/plugin_versions.py --update; see CONTRIBUTING.md section 11.",
        "plugins": {key: current[key] for key in sorted(current)},
    }
    lock_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    changed = [
        f"{key} {lock.get(key, {}).get('version', '(new)')} -> {now['version']}"
        for key, now in sorted(current.items())
        if lock.get(key) != now
    ]
    return changed + [f"{key} removed" for key in sorted(set(lock) - set(current))]


def check(lock_file: Path = LOCK_FILE, plugins_root: Path = PLUGINS_ROOT, repo_root: Path = REPO_ROOT) -> list[str]:
    return problems(current_state(plugins_root, repo_root), read_lock(lock_file))


def installed_versions(plugins_root: Path = PLUGINS_ROOT) -> dict[str, str]:
    """``plugin_key`` -> version, straight from the manifests."""
    return {
        key: str((_read_manifest(folder) or {}).get("version") or "")
        for key, folder in sorted(plugin_directories(plugins_root).items())
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="fail when a plugin changed without a new version")
    action.add_argument("--update", action="store_true", help="record the current plugin versions")
    action.add_argument("--list", action="store_true", help="list all plugins with their versions")
    args = parser.parse_args(argv)
    if args.list:
        for key, version in installed_versions().items():
            print(f"{key:<22} {version}")
        return 0
    if args.update:
        try:
            changes = update()
        except PluginVersionError as error:
            print(f"Not recorded:\n{error}", file=sys.stderr)
            return 1
        print("\n".join(changes) if changes else "Nothing changed.")
        return 0
    found = check()
    if found:
        print("Plugin versions are out of date:\n  - " + "\n  - ".join(found), file=sys.stderr)
        return 1
    print("Every plugin's version describes its files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
