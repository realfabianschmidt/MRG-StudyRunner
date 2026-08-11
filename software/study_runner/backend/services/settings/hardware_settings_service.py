from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, TypeVar

from study_runner.plugin_framework.registry import iter_plugins
from study_runner.plugin_framework.registry import set_plugin_enabled as _write_enabled_flag
from study_runner.shared.atomic_io import atomic_path_lock, atomic_write_json


class HardwareRevisionConflict(RuntimeError):
    """A machine-settings editor is based on an older on-disk document."""


HardwareUpdateResult = TypeVar("HardwareUpdateResult")


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
    """Crash-safely replace hardware settings without a shared temp name."""
    atomic_write_json(
        config_path,
        config_data,
        ensure_ascii=True,
        trailing_newline=True,
    )


def load_hardware_config(config_path: Path) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Hardware settings in {path} must be a JSON object.")
    return payload


def hardware_config_revision(config_data: dict[str, Any]) -> str:
    """Return a content revision without exposing any stored setting value."""

    encoded = json.dumps(
        config_data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def update_hardware_config(
    config_path: Path,
    updater: Callable[[dict[str, Any]], HardwareUpdateResult],
    *,
    expected_revision: str | None = None,
) -> tuple[dict[str, Any], HardwareUpdateResult, str]:
    """Serialize a complete machine-settings read/modify/write transaction.

    Targeted plugin changes are always based on the latest on-disk document.
    Whole-document editors may additionally supply ``expected_revision`` and
    are rejected instead of silently overwriting a newer save.
    """

    path = Path(config_path)
    with atomic_path_lock(path):
        current = load_hardware_config(path)
        current_revision = hardware_config_revision(current)
        if expected_revision is not None and expected_revision != current_revision:
            raise HardwareRevisionConflict(
                "Hardware settings changed after this page was loaded. Reload and retry."
            )
        working = deepcopy(current)
        result = updater(working)
        save_hardware_config(path, working)
        return deepcopy(working), result, hardware_config_revision(working)


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
