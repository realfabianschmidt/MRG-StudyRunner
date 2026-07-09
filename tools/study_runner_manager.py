from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
import urllib.request
import webbrowser
import zipfile

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


APP_NAME = "Study Runner"
REPO_URL = "https://github.com/realfabianschmidt/MRG-StudyRunner.git"
MANIFEST_URL = (
    "https://github.com/realfabianschmidt/MRG-StudyRunner/releases/latest/download/"
    "study-runner-python-latest.json"
)
UPDATER_SCHEMA_VERSION = 1
SERVER_PORT = 3000

REPO_ROOT = Path(__file__).resolve().parents[1]
SOFTWARE_ROOT = REPO_ROOT / "software"
if SOFTWARE_ROOT.exists():
    sys.path.insert(0, str(SOFTWARE_ROOT))

try:
    from study_runner.update_keys import TRUSTED_UPDATE_PUBLIC_KEYS
except Exception:
    TRUSTED_UPDATE_PUBLIC_KEYS = []


@dataclass(frozen=True)
class InstallPaths:
    root: Path
    versions: Path
    downloads: Path
    staging: Path
    state_file: Path


@dataclass(frozen=True)
class InstallResult:
    version: str
    install_dir: Path
    executable: Path
    data_dir: Path


def detect_platform_key() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x86_64"
    if system == "windows":
        os_key = "windows"
    elif system == "darwin":
        os_key = "macos"
    elif system == "linux":
        os_key = "linux"
    else:
        raise RuntimeError(f"Unsupported platform: {platform.system()}")
    return f"{os_key}-{arch}"


def manager_asset_name() -> str:
    platform_key = detect_platform_key()
    return f"study-runner-manager-{platform_key}.zip"


def server_asset_name() -> str:
    platform_key = detect_platform_key()
    return f"study-runner-server-{platform_key}.zip"


def install_paths(install_root: Path) -> InstallPaths:
    root = install_root.expanduser().resolve()
    return InstallPaths(
        root=root,
        versions=root / "versions",
        downloads=root / "downloads",
        staging=root / ".staging",
        state_file=root / "install-state.json",
    )


def fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_manifest(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("Update manifest must be a JSON object.")
    version = str(payload.get("version") or "").strip()
    assets = payload.get("assets")
    if not version:
        raise RuntimeError("Update manifest has no version.")
    if not isinstance(assets, dict) or not assets:
        raise RuntimeError("Update manifest has no platform assets.")
    return {
        "version": version,
        "notes_url": str(payload.get("notes_url") or ""),
        "minimum_updater_version": int(payload.get("minimum_updater_version") or 1),
        "assets": assets,
    }


def select_platform_asset(manifest: dict[str, Any], platform_key: str | None = None) -> dict[str, Any]:
    key = platform_key or detect_platform_key()
    asset = manifest.get("assets", {}).get(key)
    if not isinstance(asset, dict):
        raise RuntimeError(f"No signed Study Runner release asset is available for {key}.")
    normalized = {
        "url": str(asset.get("url") or "").strip(),
        "sha256": str(asset.get("sha256") or "").strip().lower(),
        "signature": str(asset.get("signature") or "").strip(),
        "size": int(asset.get("size") or 0),
        "file_name": str(asset.get("file_name") or "").strip() or server_asset_name(),
    }
    if not normalized["url"].startswith(("https://", "http://")):
        raise RuntimeError("Release asset has no valid download URL.")
    if len(normalized["sha256"]) != 64:
        raise RuntimeError("Release asset has no valid SHA-256 hash.")
    if not normalized["signature"]:
        raise RuntimeError("Release asset has no signature.")
    return normalized


def canonical_asset_payload(version: str, platform_key: str, asset: dict[str, Any]) -> bytes:
    payload = {
        "schema": UPDATER_SCHEMA_VERSION,
        "version": str(version),
        "platform": str(platform_key),
        "url": str(asset.get("url") or ""),
        "sha256": str(asset.get("sha256") or "").lower(),
        "size": int(asset.get("size") or 0),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_public_keys() -> list[Ed25519PublicKey]:
    raw_values = []
    env_key = os.getenv("STUDY_RUNNER_UPDATE_PUBLIC_KEY", "").strip()
    if env_key:
        raw_values.append(env_key)
    raw_values.extend(str(value).strip() for value in TRUSTED_UPDATE_PUBLIC_KEYS if str(value).strip())

    keys = [_load_public_key(raw_value) for raw_value in raw_values]
    if not keys:
        raise RuntimeError("No trusted Study Runner updater public key is available in this manager build.")
    return keys


def verify_asset_signature(version: str, platform_key: str, asset: dict[str, Any]) -> None:
    signature = base64.b64decode(str(asset.get("signature") or ""), validate=True)
    payload = canonical_asset_payload(version, platform_key, asset)
    for public_key in load_public_keys():
        try:
            public_key.verify(signature, payload)
            return
        except InvalidSignature:
            continue
    raise RuntimeError("Release asset signature could not be verified.")


def _load_public_key(raw_value: str) -> Ed25519PublicKey:
    if "BEGIN PUBLIC KEY" in raw_value:
        key = serialization.load_pem_public_key(raw_value.encode("utf-8"))
        if not isinstance(key, Ed25519PublicKey):
            raise RuntimeError("Configured updater public key is not an Ed25519 key.")
        return key
    key_bytes = base64.b64decode(raw_value, validate=True)
    if len(key_bytes) != 32:
        raise RuntimeError("Configured updater public key must be a 32-byte Ed25519 key.")
    return Ed25519PublicKey.from_public_bytes(key_bytes)


def download_file(url: str, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    request = urllib.request.Request(url, headers={"Accept": "application/octet-stream"})
    with urllib.request.urlopen(request, timeout=180) as response:
        with destination.open("wb") as output:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                output.write(chunk)
                hasher.update(chunk)
    return hasher.hexdigest()


def extract_zip_safe(zip_path: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            try:
                target.relative_to(destination_resolved)
            except ValueError:
                raise RuntimeError(f"Archive contains an unsafe path: {member.filename}")
        archive.extractall(destination)


def install_or_update_release(install_root: Path, data_dir: Path, log, repair: bool = False) -> InstallResult:
    paths = install_paths(install_root)
    paths.versions.mkdir(parents=True, exist_ok=True)
    paths.downloads.mkdir(parents=True, exist_ok=True)
    data_dir = data_dir.expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    log("Reading signed Study Runner release manifest...")
    manifest = normalize_manifest(fetch_json(MANIFEST_URL))
    if manifest["minimum_updater_version"] > UPDATER_SCHEMA_VERSION:
        raise RuntimeError("This Study Runner release requires a newer Install & Repair Wizard.")
    platform_key = detect_platform_key()
    asset = select_platform_asset(manifest, platform_key)
    verify_asset_signature(manifest["version"], platform_key, asset)

    version_dir = paths.versions / manifest["version"]
    if version_dir.exists() and not repair:
        executable = find_server_executable(version_dir)
        if executable:
            state = write_install_state(paths, InstallResult(manifest["version"], version_dir, executable, data_dir))
            log(f"Latest version already installed: {state['version']}")
            return InstallResult(manifest["version"], version_dir, executable, data_dir)

    zip_path = paths.downloads / asset["file_name"]
    log(f"Downloading {asset['file_name']}...")
    actual_sha256 = download_file(asset["url"], zip_path)
    if actual_sha256 != asset["sha256"]:
        raise RuntimeError("Downloaded release ZIP did not match the signed SHA-256 hash.")

    if repair and version_dir.exists():
        ensure_within(paths.versions, version_dir)
        shutil.rmtree(version_dir)

    paths.staging.mkdir(parents=True, exist_ok=True)
    tmp_root = Path(tempfile.mkdtemp(prefix=f"{manifest['version']}-", dir=str(paths.staging)))
    try:
        extract_zip_safe(zip_path, tmp_root)
        if version_dir.exists():
            ensure_within(paths.versions, version_dir)
            shutil.rmtree(version_dir)
        tmp_root.rename(version_dir)
    finally:
        if tmp_root.exists():
            shutil.rmtree(tmp_root, ignore_errors=True)

    executable = find_server_executable(version_dir)
    if executable is None:
        raise RuntimeError(f"Installed release has no study-runner-server executable: {version_dir}")

    result = InstallResult(manifest["version"], version_dir, executable, data_dir)
    write_install_state(paths, result)
    log(f"Installed Study Runner {manifest['version']} to {version_dir}")
    return result


def ensure_within(parent: Path, child: Path) -> None:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError as error:
        raise RuntimeError(f"Refusing to modify path outside {parent}: {child}") from error


def write_install_state(paths: InstallPaths, result: InstallResult) -> dict[str, str]:
    state = {
        "version": result.version,
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "install_dir": str(result.install_dir),
        "executable": str(result.executable),
        "data_dir": str(result.data_dir),
    }
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def read_install_state(install_root: Path) -> dict[str, Any]:
    state_file = install_paths(install_root).state_file
    if not state_file.exists():
        return {}
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def find_active_install(install_root: Path) -> tuple[Path, Path | None]:
    state = read_install_state(install_root)
    install_dir = Path(str(state.get("install_dir") or "")) if state.get("install_dir") else None
    executable = Path(str(state.get("executable") or "")) if state.get("executable") else None
    if install_dir and executable and executable.exists():
        return install_dir, executable

    versions = install_paths(install_root).versions
    if versions.exists():
        for candidate in sorted(versions.iterdir(), reverse=True):
            executable = find_server_executable(candidate)
            if executable:
                return candidate, executable
    return install_root, find_server_executable(install_root)


def find_server_executable(root: Path) -> Path | None:
    candidates = ["study-runner-server.exe", "study-runner-server"]
    for name in candidates:
        for path in root.rglob(name):
            if path.is_file():
                return path
    return None


def desktop_dir() -> Path:
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        raise RuntimeError(f"Desktop folder not found: {desktop}")
    return desktop


def create_launcher(install_root: Path, data_dir: Path, log) -> Path:
    _, executable = find_active_install(install_root)
    if executable is None:
        raise RuntimeError("No installed study-runner-server executable found. Install Study Runner first.")

    data_dir = data_dir.expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    if platform.system().lower() == "windows":
        launcher = desktop_dir() / "Study Runner.cmd"
        launcher.write_text(
            "@echo off\n"
            f'set "STUDY_RUNNER_DATA_DIR={data_dir}"\n'
            f'cd /d "{executable.parent}"\n'
            f'start "" "{executable}"\n',
            encoding="utf-8",
        )
    else:
        launcher = desktop_dir() / "Study Runner.command"
        launcher.write_text(
            "#!/bin/zsh\n"
            f'export STUDY_RUNNER_DATA_DIR="{data_dir}"\n'
            f'cd "{executable.parent}"\n'
            f'"{executable}"\n',
            encoding="utf-8",
        )
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log(f"Desktop launcher ready: {launcher}")
    return launcher


def start_installed_server(install_root: Path, data_dir: Path, log) -> None:
    _, executable = find_active_install(install_root)
    if executable is None:
        raise RuntimeError("No installed study-runner-server executable found. Install Study Runner first.")
    env = os.environ.copy()
    env["STUDY_RUNNER_DATA_DIR"] = str(data_dir.expanduser().resolve())
    subprocess.Popen([str(executable)], cwd=str(executable.parent), env=env)
    log("Study Runner server started. Opening Admin page...")
    webbrowser.open(f"https://localhost:{SERVER_PORT}/admin")


def install_development_build(install_root: Path, data_dir: Path, log) -> None:
    python_executable = shutil.which("python") or shutil.which("python3")
    git_executable = shutil.which("git")
    if not python_executable or not git_executable:
        raise RuntimeError("Development install needs Git and Python available on PATH.")

    dev_root = install_root.expanduser().resolve() / "development"
    source_dir = dev_root / "MRG-StudyRunner"
    if source_dir.exists():
        log("Updating development checkout with git pull --ff-only...")
        subprocess.run([git_executable, "pull", "--ff-only"], cwd=source_dir, check=True)
    else:
        dev_root.mkdir(parents=True, exist_ok=True)
        log("Cloning development checkout from main...")
        subprocess.run([git_executable, "clone", "--branch", "main", REPO_URL, str(source_dir)], check=True)

    venv_dir = source_dir / ".venv"
    if not venv_dir.exists():
        log("Creating Python virtual environment...")
        subprocess.run([python_executable, "-m", "venv", str(venv_dir)], check=True)

    venv_python = venv_dir / ("Scripts/python.exe" if platform.system().lower() == "windows" else "bin/python")
    log("Installing development dependencies...")
    subprocess.run([str(venv_python), "-m", "pip", "install", "-r", "software/requirements.txt"], cwd=source_dir, check=True)
    create_source_launcher(source_dir, venv_python, data_dir, log)


def create_source_launcher(source_dir: Path, python_executable: Path, data_dir: Path, log) -> Path:
    data_dir = data_dir.expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    server_dir = source_dir / "software"
    server_file = server_dir / "server.py"
    if platform.system().lower() == "windows":
        launcher = desktop_dir() / "Study Runner Development.cmd"
        launcher.write_text(
            "@echo off\n"
            f'set "STUDY_RUNNER_DATA_DIR={data_dir}"\n'
            f'cd /d "{server_dir}"\n'
            f'start "" "{python_executable}" "{server_file}"\n',
            encoding="utf-8",
        )
    else:
        launcher = desktop_dir() / "Study Runner Development.command"
        launcher.write_text(
            "#!/bin/zsh\n"
            f'export STUDY_RUNNER_DATA_DIR="{data_dir}"\n'
            f'cd "{server_dir}"\n'
            f'"{python_executable}" "{server_file}"\n',
            encoding="utf-8",
        )
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log(f"Development launcher ready: {launcher}")
    return launcher


def build_diagnostics_text(install_root: Path, data_dir: Path) -> str:
    install_dir, executable = find_active_install(install_root)
    state = read_install_state(install_root)
    lines = [
        f"{APP_NAME} Install & Repair Wizard Diagnostics",
        f"Created: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Platform: {platform.platform()}",
        f"Python: {sys.version.split()[0]} ({sys.executable})",
        f"Install root: {install_root.expanduser().resolve()}",
        f"Active install dir: {install_dir}",
        f"Data folder: {data_dir.expanduser().resolve()}",
        f"Installed version: {state.get('version') or 'unknown'}",
        f"Server executable: {executable or 'not found'}",
        f"Data folder exists: {data_dir.expanduser().exists()}",
        f"Settings folder exists: {(data_dir.expanduser() / 'settings').exists()}",
        f"Studies folder exists: {(data_dir.expanduser() / 'studies').exists()}",
        f"Saved results folder exists: {(data_dir.expanduser() / 'saved_results').exists()}",
    ]
    return "\n".join(lines) + "\n"


def export_diagnostics(install_root: Path, data_dir: Path, log) -> Path:
    destination = filedialog.asksaveasfilename(
        title="Save diagnostics",
        defaultextension=".txt",
        filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
    )
    if not destination:
        raise RuntimeError("Diagnostics export cancelled.")
    output_path = Path(destination)
    output_path.write_text(build_diagnostics_text(install_root, data_dir), encoding="utf-8")
    log(f"Diagnostics exported: {output_path}")
    return output_path


class ManagerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Study Runner Install & Repair Wizard")
        self.geometry("860x600")
        self.minsize(780, 520)

        default_base = Path.home() / "StudyRunner"
        self.install_var = tk.StringVar(value=str(default_base / "app"))
        self.data_var = tk.StringVar(value=str(default_base / "data"))

        self._build_ui()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=16)
        root.pack(fill=tk.BOTH, expand=True)

        ttk.Label(root, text="Study Runner Install & Repair Wizard", font=("", 16, "bold")).pack(anchor=tk.W)
        ttk.Label(
            root,
            text="Installs the latest stable GitHub Release. Data stays in a separate folder and is never deleted by repair.",
        ).pack(anchor=tk.W, pady=(4, 16))

        self._path_row(root, "Install folder", self.install_var, self.choose_install_dir)
        self._path_row(root, "Data folder", self.data_var, self.choose_data_dir)

        actions = ttk.LabelFrame(root, text="Stable release")
        actions.pack(fill=tk.X, pady=(12, 8))
        ttk.Button(actions, text="Install / Update Study Runner", command=self.install_release).pack(side=tk.LEFT, padx=8, pady=8)
        ttk.Button(actions, text="Repair existing installation", command=self.repair_release).pack(side=tk.LEFT, padx=8, pady=8)
        ttk.Button(actions, text="Create desktop launcher", command=self.create_desktop_launcher).pack(side=tk.LEFT, padx=8, pady=8)
        ttk.Button(actions, text="Start Study Runner", command=self.start_server).pack(side=tk.LEFT, padx=8, pady=8)

        support = ttk.LabelFrame(root, text="Support")
        support.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(support, text="Export diagnostics", command=self.save_diagnostics).pack(side=tk.LEFT, padx=8, pady=8)

        advanced = ttk.LabelFrame(root, text="Advanced")
        advanced.pack(fill=tk.X, pady=(0, 12))
        ttk.Button(advanced, text="Install development build from GitHub main", command=self.install_development).pack(side=tk.LEFT, padx=8, pady=8)
        ttk.Label(advanced, text="Development builds need local Git + Python and are not the lab default.").pack(side=tk.LEFT, padx=8)

        self.log_box = tk.Text(root, height=16, wrap=tk.WORD)
        self.log_box.pack(fill=tk.BOTH, expand=True)
        self.log("Ready.")

    def _path_row(self, parent, label: str, variable: tk.StringVar, command) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=4)
        ttk.Label(row, text=label, width=16).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=variable).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Button(row, text="Choose", command=command).pack(side=tk.LEFT)

    def choose_install_dir(self) -> None:
        self._choose_dir(self.install_var)

    def choose_data_dir(self) -> None:
        self._choose_dir(self.data_var)

    def _choose_dir(self, variable: tk.StringVar) -> None:
        value = filedialog.askdirectory(initialdir=variable.get() or str(Path.home()))
        if value:
            variable.set(value)

    def install_release(self) -> None:
        self._run_background(lambda: self._install_or_repair(repair=False))

    def repair_release(self) -> None:
        self._run_background(lambda: self._install_or_repair(repair=True))

    def _install_or_repair(self, repair: bool) -> None:
        result = install_or_update_release(self.install_root, self.data_dir, self.log, repair=repair)
        create_launcher(self.install_root, self.data_dir, self.log)
        action = "Repaired" if repair else "Installed"
        self.log(f"{action} Study Runner {result.version}.")

    def create_desktop_launcher(self) -> None:
        self._run_background(lambda: create_launcher(self.install_root, self.data_dir, self.log))

    def start_server(self) -> None:
        self._run_background(lambda: start_installed_server(self.install_root, self.data_dir, self.log))

    def install_development(self) -> None:
        if not messagebox.askyesno(
            APP_NAME,
            "Development builds use GitHub main and require local Git + Python. Continue?",
        ):
            return
        self._run_background(lambda: install_development_build(self.install_root, self.data_dir, self.log))

    def save_diagnostics(self) -> None:
        try:
            export_diagnostics(self.install_root, self.data_dir, self.log)
        except Exception as error:
            messagebox.showerror(APP_NAME, str(error))

    @property
    def install_root(self) -> Path:
        return Path(self.install_var.get()).expanduser().resolve()

    @property
    def data_dir(self) -> Path:
        return Path(self.data_var.get()).expanduser().resolve()

    def _run_background(self, action) -> None:
        def runner() -> None:
            try:
                action()
                self.log("Done.")
            except Exception as error:
                message = str(error)
                self.log(f"ERROR: {message}")
                self.after(0, lambda text=message: messagebox.showerror(APP_NAME, text))

        threading.Thread(target=runner, daemon=True).start()

    def log(self, message: str) -> None:
        def append() -> None:
            self.log_box.insert(tk.END, f"{time.strftime('%H:%M:%S')}  {message}\n")
            self.log_box.see(tk.END)

        self.after(0, append)


def main() -> int:
    app = ManagerApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
