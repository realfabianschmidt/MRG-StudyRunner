"""The operator-chosen data folder: studies, results, settings, credentials,
logos, fonts and the iPad certificate -- everything local, in one folder that
may live outside the program files (another drive, an external disk).

Where the data lives is decided once, before the app starts
(``apply_data_folder_setting``, called by ``software/server.py``):

1. ``STUDY_RUNNER_DATA_DIR`` set by hand (developers, CI) always wins;
2. otherwise ``<install>/data-folder.json``, written from the settings page;
3. otherwise the program folder (``software/study_content`` +
   ``software/saved_results``), as before.

A chosen folder is handed on as ``STUDY_RUNNER_DATA_DIR``, so
``runtime_config.resolve_runtime_paths``, plugin processes and restarts need no
second code path; the folder has exactly the layout that variable always had
(``settings/``, ``studies/``, ``saved_results/``, ``runtime/``, ``updates/``).
An empty folder is set up like a clean install by
``runtime_config.initialize_runtime_storage``.

A chosen folder that is not reachable (disk not connected) stops the start
instead of silently creating a new, empty folder somewhere.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Mapping, MutableMapping

SETTING_FILE_NAME = "data-folder.json"
MARKER_FILE_NAME = "study-runner-data.json"
MARKER_SCHEMA = "study-runner/data-folder/v1"
RECENT_FILE_NAME = "recent-data-folders.json"
RECENT_LIMIT = 5
ENV_DATA_DIR = "STUDY_RUNNER_DATA_DIR"
# Set together with STUDY_RUNNER_DATA_DIR when that value came from the
# setting file, so a restarted server (which inherits the environment) reads
# the setting again instead of keeping the old folder.
ENV_FROM_SETTING = "STUDY_RUNNER_DATA_DIR_FROM_SETTING"


class DataFolderError(RuntimeError):
    """A plain-language reason a folder cannot be used as the data folder."""


# ---------------------------------------------------------------- resolving

def install_root() -> Path:
    """The folder the operator extracted or cloned (the parent of ``software/``)."""
    from study_runner.shared.runtime_mode import get_project_base_dir

    return get_project_base_dir().parent


def setting_file(root: Path | None = None) -> Path:
    return Path(root or install_root()) / SETTING_FILE_NAME


def read_setting(root: Path | None = None) -> Path | None:
    try:
        payload = json.loads(setting_file(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = str(payload.get("path") or "").strip() if isinstance(payload, dict) else ""
    return Path(value) if value else None


def write_setting(path: Path | None, root: Path | None = None) -> None:
    """Point this installation at ``path``; ``None`` returns to the program folder."""
    target = setting_file(root)
    if path is None:
        target.unlink(missing_ok=True)
        return
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"path": str(Path(path))}, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def apply_data_folder_setting(
    root: Path | None = None, environ: MutableMapping[str, str] | None = None
) -> Path | None:
    """Decide the data folder before the app starts; return it (``None`` = program folder).

    Raises ``DataFolderError`` with instructions when the chosen folder is not
    reachable -- nothing is created in that case.
    """
    env = os.environ if environ is None else environ
    manual = str(env.get(ENV_DATA_DIR) or "").strip()
    if manual and env.get(ENV_FROM_SETTING) != "1":
        return Path(manual)
    env.pop(ENV_DATA_DIR, None)
    env.pop(ENV_FROM_SETTING, None)
    chosen = read_setting(root)
    if chosen is None:
        return None
    if not is_data_folder(chosen):
        raise DataFolderError(unavailable_message(chosen, setting_file(root)))
    env[ENV_DATA_DIR] = str(chosen)
    env[ENV_FROM_SETTING] = "1"
    remember(chosen)
    return chosen


def unavailable_message(chosen: Path, setting: Path) -> str:
    return (
        f"The data folder {chosen} is not reachable.\n"
        "Connect the drive it is on and start Study Runner again.\n"
        f"To use the folder inside the program instead, delete {setting}."
    )


# ------------------------------------------------------------------ marker

def is_data_folder(path: Path) -> bool:
    """A Study Runner data folder: has the marker, or the layout of one set up
    through STUDY_RUNNER_DATA_DIR before the marker existed."""
    folder = Path(path)
    if (folder / MARKER_FILE_NAME).is_file():
        return True
    return (folder / "settings").is_dir() and (folder / "saved_results").is_dir()


def write_marker(path: Path, version: str) -> None:
    marker = Path(path) / MARKER_FILE_NAME
    if marker.is_file():
        return
    marker.write_text(json.dumps({
        "schema": MARKER_SCHEMA,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "created_by_version": version,
    }, indent=2) + "\n", encoding="utf-8")


# ------------------------------------------------- choosing a new folder

def check_candidate(path: str | Path, root: Path | None = None) -> str:
    """``"link"`` for an existing data folder, ``"new"`` for an empty or missing
    one; ``DataFolderError`` for anything that must not become the data folder."""
    text = str(path or "").strip()
    if not text:
        raise DataFolderError("Enter or choose a folder.")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        raise DataFolderError("Use a full path, for example D:\\StudyRunnerData or /Volumes/Lab/StudyRunner.")
    resolved = candidate.resolve()
    program = Path(root or install_root()).resolve()
    if resolved == program or program in resolved.parents:
        raise DataFolderError(
            "Choose a folder outside the Study Runner program folder -- an update replaces the program folder."
        )
    if resolved.exists() and not resolved.is_dir():
        raise DataFolderError(f"{resolved} is a file, not a folder.")
    if is_data_folder(resolved):
        return "link"
    if resolved.is_dir() and any(resolved.iterdir()):
        raise DataFolderError(
            f"{resolved} already contains other files. Choose an empty folder, or an existing Study Runner data folder."
        )
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        probe = resolved / ".study-runner-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as error:
        raise DataFolderError(f"Study Runner cannot write to {resolved}: {error}") from error
    return "new"


def prepare_new_folder(target: Path, current: Mapping[str, Any] | None, version: str) -> list[str]:
    """Set up ``target``; with ``current`` (the running app's config) copy the
    current data into it first. Returns the parts that were copied.

    Without a copy the next start fills the folder like a clean install
    (default settings, example studies, the demo result, a new certificate).
    """
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    if current is not None:
        storage_root = Path(current["STORAGE_ROOT"])
        parts = (
            ("settings", Path(current["SETTINGS_DIR"])),
            ("studies", Path(current["SAVED_STUDIES_DIR"])),
            ("saved_results", Path(current["DATA_DIR"])),
            ("runtime", storage_root / "runtime"),
        )
        for name, source in parts:
            if source.is_dir():
                shutil.copytree(source, target / name, dirs_exist_ok=True)
                copied.append(name)
    write_marker(target, version)
    return copied


# ------------------------------------------- remembered folders (per user)

def user_config_dir(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = str(env.get("STUDY_RUNNER_USER_CONFIG_DIR") or "").strip()
    if override:
        return Path(override)
    if os.name == "nt":
        return Path(env.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "StudyRunner"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "StudyRunner"
    return Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "study-runner"


def remembered_folders() -> list[str]:
    """Data folders this user account used before that are reachable now --
    offered for relinking after a reinstall, never used automatically."""
    try:
        payload = json.loads((user_config_dir() / RECENT_FILE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    folders = payload.get("folders") if isinstance(payload, dict) else None
    return [str(item) for item in folders or [] if isinstance(item, str) and is_data_folder(Path(item))]


def remember(path: Path) -> None:
    try:
        folder = user_config_dir()
        try:
            previous = json.loads((folder / RECENT_FILE_NAME).read_text(encoding="utf-8")).get("folders") or []
        except (OSError, ValueError, AttributeError):
            previous = []
        entries = [str(Path(path))] + [item for item in previous if isinstance(item, str) and item != str(Path(path))]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / RECENT_FILE_NAME).write_text(
            json.dumps({"folders": entries[:RECENT_LIMIT]}, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as error:  # a suggestion list must never stop the app
        print(f"[DATA] Could not remember the data folder: {error}")


# ------------------------------------------------------------ folder dialog

def choose_folder_dialog(timeout_seconds: int = 600) -> str | None:
    """Native "choose folder" dialog on this computer; ``None`` when cancelled."""
    if sys.platform == "darwin":
        command = ["osascript", "-e", 'POSIX path of (choose folder with prompt "Study Runner data folder")']
    elif os.name == "nt":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$d.Description = 'Study Runner data folder'; $d.ShowNewFolderButton = $true;"
            "$f = New-Object System.Windows.Forms.Form; $f.TopMost = $true;"
            "if ($d.ShowDialog($f) -eq 'OK') { [Console]::Out.Write($d.SelectedPath) }"
        )
        command = ["powershell", "-NoProfile", "-STA", "-Command", script]
    else:
        raise DataFolderError("Choosing a folder by dialog is not available here; enter the path instead.")
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DataFolderError(f"The folder dialog could not be opened: {error}") from error
    chosen = result.stdout.strip()
    return str(Path(chosen)) if chosen else None
