from __future__ import annotations

import json
from pathlib import Path
import platform
import shutil
import stat
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile

import tkinter as tk
from tkinter import filedialog, messagebox, ttk


RELEASE_API_URL = "https://api.github.com/repos/realfabianschmidt/MRG-StudyRunner/releases/latest"
APP_NAME = "Study Runner"


def platform_asset_name() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x86_64"
    if system == "windows":
        return "study-runner-server-windows-x86_64.zip"
    if system == "darwin":
        return f"study-runner-server-macos-{arch}.zip"
    if system == "linux":
        return "study-runner-server-linux-x86_64.zip"
    raise RuntimeError(f"Unsupported platform: {platform.system()}")


def read_latest_release() -> dict:
    request = urllib.request.Request(RELEASE_API_URL, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def find_release_asset(release: dict, asset_name: str) -> dict:
    for asset in release.get("assets", []):
        if asset.get("name") == asset_name:
            return asset
    raise RuntimeError(f"Release asset not found: {asset_name}")


def download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"Accept": "application/octet-stream"})
    with urllib.request.urlopen(request, timeout=120) as response:
        with destination.open("wb") as output:
            shutil.copyfileobj(response, output)


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


def install_latest_release(install_dir: Path, log) -> Path:
    install_dir.mkdir(parents=True, exist_ok=True)
    asset_name = platform_asset_name()
    log(f"Fetching latest release metadata for {asset_name}...")
    release = read_latest_release()
    asset = find_release_asset(release, asset_name)

    downloads_dir = install_dir / "downloads"
    zip_path = downloads_dir / asset_name
    log(f"Downloading {asset_name}...")
    download_file(asset["browser_download_url"], zip_path)

    tmp_root = Path(tempfile.mkdtemp(prefix="study-runner-install-", dir=str(install_dir)))
    extract_zip_safe(zip_path, tmp_root)

    current_dir = install_dir / "current"
    backup_dir = install_dir / f"backup-{time.strftime('%Y%m%d-%H%M%S')}"
    if current_dir.exists():
        current_dir.rename(backup_dir)
        log(f"Previous install moved to {backup_dir}")
    tmp_root.rename(current_dir)
    log(f"Installed to {current_dir}")
    return current_dir


def find_server_executable(install_dir: Path) -> Path | None:
    candidates = ["study-runner-server.exe", "study-runner-server"]
    for name in candidates:
        for path in install_dir.rglob(name):
            if path.is_file():
                return path
    return None


def create_launcher(install_dir: Path, data_dir: Path, log) -> Path:
    executable = find_server_executable(install_dir)
    if executable is None:
        raise RuntimeError(f"No study-runner-server executable found under {install_dir}")

    data_dir.mkdir(parents=True, exist_ok=True)
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        raise RuntimeError(f"Desktop folder not found: {desktop}")

    if platform.system().lower() == "windows":
        launcher = desktop / "Study Runner.cmd"
        launcher.write_text(
            "@echo off\n"
            f'set "STUDY_RUNNER_DATA_DIR={data_dir}"\n'
            f'cd /d "{executable.parent}"\n'
            f'start "" "{executable}"\n',
            encoding="utf-8",
        )
    else:
        launcher = desktop / "Study Runner.command"
        launcher.write_text(
            "#!/bin/zsh\n"
            f'export STUDY_RUNNER_DATA_DIR="{data_dir}"\n'
            f'cd "{executable.parent}"\n'
            f'"{executable}"\n',
            encoding="utf-8",
        )
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    log(f"Launcher created: {launcher}")
    return launcher


def export_diagnostics(install_dir: Path, data_dir: Path, log) -> Path:
    destination = filedialog.asksaveasfilename(
        title="Save diagnostics",
        defaultextension=".txt",
        filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
    )
    if not destination:
        raise RuntimeError("Diagnostics export cancelled.")

    executable = find_server_executable(install_dir)
    lines = [
        f"{APP_NAME} Manager Diagnostics",
        f"Created: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Platform: {platform.platform()}",
        f"Python: {sys.version.split()[0]} ({sys.executable})",
        f"Install folder: {install_dir}",
        f"Data folder: {data_dir}",
        f"Server executable: {executable or 'not found'}",
        f"Data folder exists: {data_dir.exists()}",
        f"Settings folder exists: {(data_dir / 'settings').exists()}",
        f"Studies folder exists: {(data_dir / 'studies').exists()}",
        f"Saved results folder exists: {(data_dir / 'saved_results').exists()}",
    ]
    output_path = Path(destination)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"Diagnostics exported: {output_path}")
    return output_path


class ManagerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Study Runner Manager")
        self.geometry("780x520")
        self.minsize(720, 460)

        default_base = Path.home() / "StudyRunner"
        self.install_var = tk.StringVar(value=str(default_base / "app"))
        self.data_var = tk.StringVar(value=str(default_base / "data"))

        self._build_ui()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=16)
        root.pack(fill=tk.BOTH, expand=True)

        ttk.Label(root, text="Study Runner Manager", font=("", 16, "bold")).pack(anchor=tk.W)
        ttk.Label(
            root,
            text="Small install, update and diagnostics helper. User data stays in the selected data folder.",
        ).pack(anchor=tk.W, pady=(4, 16))

        self._path_row(root, "Install folder", self.install_var, self.choose_install_dir)
        self._path_row(root, "Data folder", self.data_var, self.choose_data_dir)

        actions = ttk.Frame(root)
        actions.pack(fill=tk.X, pady=(12, 12))
        ttk.Button(actions, text="Install / update latest release", command=self.install_release).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(actions, text="Create desktop launcher", command=self.create_desktop_launcher).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(actions, text="Export diagnostics", command=self.save_diagnostics).pack(side=tk.LEFT)

        self.log_box = tk.Text(root, height=15, wrap=tk.WORD)
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
        self._run_background(lambda: install_latest_release(self.install_dir, self.log))

    def create_desktop_launcher(self) -> None:
        self._run_background(lambda: create_launcher(self.install_dir, self.data_dir, self.log))

    def save_diagnostics(self) -> None:
        try:
            export_diagnostics(self.install_dir, self.data_dir, self.log)
        except Exception as error:
            messagebox.showerror(APP_NAME, str(error))

    @property
    def install_dir(self) -> Path:
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
