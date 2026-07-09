from __future__ import annotations

from pathlib import Path
import os
import platform
import stat
import subprocess
import sys
from typing import Any


class ShortcutError(Exception):
    """Raised when Study Runner cannot create an operator desktop shortcut."""


def create_desktop_shortcut(app_config: dict[str, Any]) -> dict[str, Any]:
    """Create a simple desktop shortcut for the current Study Runner install."""
    system = platform.system().lower()
    if system == "windows":
        return _create_windows_shortcut(app_config)
    if system == "darwin":
        return _create_macos_shortcut(app_config)
    raise ShortcutError("Desktop shortcut creation is supported on Windows and macOS only.")


def _desktop_dir() -> Path:
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        raise ShortcutError(f"Desktop folder not found: {desktop}")
    return desktop


def _server_launch(app_config: dict[str, Any]) -> tuple[Path, str, Path]:
    base_dir = Path(app_config.get("BASE_DIR") or ".").resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve(), "", Path(sys.executable).resolve().parent

    server_file = base_dir / "server.py"
    if not server_file.exists():
        server_file = base_dir / "study_runner" / "app_server.py"
    return Path(sys.executable).resolve(), str(server_file), base_dir


def _windows_arguments(arguments: str) -> str:
    if not arguments:
        return ""
    return f'"{arguments}"'


def _create_windows_shortcut(app_config: dict[str, Any]) -> dict[str, Any]:
    target, arguments, working_dir = _server_launch(app_config)
    shortcut_path = _desktop_dir() / "Study Runner.lnk"
    icon_path = target
    command = (
        "$shell = New-Object -ComObject WScript.Shell; "
        "$shortcut = $shell.CreateShortcut($env:STUDY_RUNNER_SHORTCUT_PATH); "
        "$shortcut.TargetPath = $env:STUDY_RUNNER_TARGET; "
        "$shortcut.Arguments = $env:STUDY_RUNNER_ARGUMENTS; "
        "$shortcut.WorkingDirectory = $env:STUDY_RUNNER_WORKDIR; "
        "$shortcut.IconLocation = $env:STUDY_RUNNER_ICON; "
        "$shortcut.Description = 'Start the Study Runner Python server'; "
        "$shortcut.Save()"
    )
    env = os.environ.copy()
    env.update(
        {
            "STUDY_RUNNER_SHORTCUT_PATH": str(shortcut_path),
            "STUDY_RUNNER_TARGET": str(target),
            "STUDY_RUNNER_ARGUMENTS": _windows_arguments(arguments),
            "STUDY_RUNNER_WORKDIR": str(working_dir),
            "STUDY_RUNNER_ICON": str(icon_path),
        }
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=str(working_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ShortcutError(detail or "PowerShell could not create the desktop shortcut.")
    return {"ok": True, "platform": "windows", "path": str(shortcut_path)}


def _create_macos_shortcut(app_config: dict[str, Any]) -> dict[str, Any]:
    target, arguments, working_dir = _server_launch(app_config)
    shortcut_path = _desktop_dir() / "Study Runner.command"
    if arguments:
        launch_line = f'cd "{working_dir}"\n"{target}" "{arguments}"\n'
    else:
        launch_line = f'cd "{working_dir}"\n"{target}"\n'
    content = "#!/bin/zsh\n" + launch_line
    shortcut_path.write_text(content, encoding="utf-8")
    shortcut_path.chmod(shortcut_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return {"ok": True, "platform": "macos", "path": str(shortcut_path)}
