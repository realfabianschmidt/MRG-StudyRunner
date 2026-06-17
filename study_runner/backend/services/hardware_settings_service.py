from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from study_runner.integrations.registry import iter_plugins, set_plugin_enabled


def save_hardware_config(config_path: Path, config_data: dict[str, Any]) -> None:
    """Write hardware integration settings through a temporary file."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = config_path.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(config_data, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(config_path)


def known_integration_keys() -> tuple[str, ...]:
    return tuple(plugin.key for plugin in iter_plugins() if plugin.can_toggle)


def set_integration_enabled(
    config_data: dict[str, Any],
    integration_key: str,
    enabled: bool,
) -> dict[str, Any]:
    """Set one registered integration's enabled flag and return the updated config."""
    try:
        return set_plugin_enabled(config_data, integration_key, enabled)
    except ValueError as error:
        known_keys = ", ".join(sorted(known_integration_keys()))
        raise ValueError(f"{error} Known toggleable integrations: {known_keys}.") from error
