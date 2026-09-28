"""Small, state-free helpers shared by plugin adapters."""
from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from study_runner.contracts.plugin_api import PluginContext


def timestamp(epoch: float | None = None) -> str:
    """Format a local wall-clock timestamp in the plugins' wire format."""
    instant = time.time() if epoch is None else float(epoch)
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(instant))


def set_state(
    state: dict[str, Any],
    lock: Any,
    values: dict[str, Any],
) -> None:
    """Update one adapter state dictionary under its owning lock."""
    with lock:
        state.update(values)
        state["updated_at"] = timestamp()


def config_section(context: PluginContext, *keys: str) -> dict[str, Any]:
    """Return the first non-empty dictionary config section for ``keys``."""
    for key in keys:
        section = context.hardware_config.get(key)
        if isinstance(section, dict) and section:
            return section
    return {}


def plugin_runtime_dir(context: PluginContext, plugin_key: str, *parts: str) -> Path:
    """Where a plugin keeps what it writes while running (logs, caches, state).

    Next to the saved results (``<storage>/runtime/<plugin_key>/...``), never in
    the program files: an update replaces the program files, this folder stays.
    """
    data_dir = Path(context.data_dir).expanduser().resolve()
    storage_root = data_dir.parent if data_dir.name == "saved_results" else data_dir
    return storage_root.joinpath("runtime", plugin_key, *parts)


def runtime_path_setting(context: PluginContext, configured: Any, plugin_key: str, *parts: str) -> Path:
    """A configured runtime folder, or the plugin's runtime folder.

    A setting that points into the program files (older settings files pinned
    ``study_runner/plugins/...``) is redirected too, because an update would
    move everything written there into its backup.
    """
    default = plugin_runtime_dir(context, plugin_key, *parts)
    resolved = context.resolve_project_path(context.resolve_platform_value(configured)) if configured else None
    if not resolved or is_program_file_path(context, resolved):
        return default
    return Path(resolved)


def is_program_file_path(context: PluginContext, candidate: Any) -> bool:
    """True inside the program files (``software/study_runner``, or the whole bundle of a packaged build)."""
    from study_runner.shared.runtime_mode import is_frozen

    program_root = Path(context.base_dir) if is_frozen() else Path(context.base_dir) / "study_runner"
    try:
        Path(candidate).resolve().relative_to(program_root.resolve())
    except (ValueError, OSError):
        return False
    return True
