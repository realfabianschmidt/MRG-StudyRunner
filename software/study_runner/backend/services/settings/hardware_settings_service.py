from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from study_runner.plugin_framework.registry import iter_plugins
from study_runner.plugin_framework.registry import set_plugin_enabled as _write_enabled_flag


# Up to 0.5.0 the plugin folder was called `integrations`, and machine settings
# store paths into it verbatim: a script to launch, a folder to log into, a
# directory of model weights. Renaming the folder would leave every one of those
# pointing nowhere on an operator's existing install, so they are rewritten when
# the file is read.
_MOVED_PLUGIN_PATHS = (
    ("study_runner/integrations/", "study_runner/plugins/"),
    ("study_runner\\integrations\\", "study_runner\\plugins\\"),
)


def migrate_moved_plugin_paths(value: Any) -> tuple[Any, int]:
    """Repoint stored plugin paths at the renamed folder.

    Returns the migrated value and how many strings changed, so a caller can
    decide whether the file is worth rewriting. Walks the whole structure
    because the paths sit at different depths per plugin and some are inside a
    per-platform mapping.
    """
    if isinstance(value, str):
        migrated = value
        for old, new in _MOVED_PLUGIN_PATHS:
            migrated = migrated.replace(old, new)
        return migrated, int(migrated != value)

    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        changes = 0
        for key, item in value.items():
            result[key], changed = migrate_moved_plugin_paths(item)
            changes += changed
        return result, changes

    if isinstance(value, list):
        migrated_items = [migrate_moved_plugin_paths(item) for item in value]
        return [item for item, _ in migrated_items], sum(changed for _, changed in migrated_items)

    return value, 0


def save_hardware_config(config_path: Path, config_data: dict[str, Any]) -> None:
    """Write hardware integration settings through a temporary file."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = config_path.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(config_data, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(config_path)


def known_plugin_keys() -> tuple[str, ...]:
    return tuple(plugin.key for plugin in iter_plugins() if plugin.can_toggle)


def set_plugin_enabled(
    config_data: dict[str, Any],
    plugin_key: str,
    enabled: bool,
) -> dict[str, Any]:
    """Set one registered plugin's enabled flag and return the updated config.

    Wraps the framework call only to name the plugins an operator could have
    meant, since the key usually arrives from a request.
    """
    try:
        return _write_enabled_flag(config_data, plugin_key, enabled)
    except ValueError as error:
        known_keys = ", ".join(sorted(known_plugin_keys()))
        raise ValueError(f"{error} Known toggleable plugins: {known_keys}.") from error
