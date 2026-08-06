"""Small, state-free helpers shared by plugin adapters."""
from __future__ import annotations

import time
from typing import Any

from .plugin_api import PluginContext


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
