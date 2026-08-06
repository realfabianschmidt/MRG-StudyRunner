"""Editable machine-level plugin settings, driven by the manifests.

Two rules make this safe:

1. **`hardware_settings.json` is the only source of truth for values.** The
   manifest is schema, and a fallback only where a key is absent from disk.
   Manifest defaults are never written back - otherwise the first save of an
   unrelated field would silently change behaviour to whatever the manifest
   happened to claim. (Those two disagreed in several places before v2.)

2. **Only paths the operator actually changed are written**, deep-merged into
   the existing config. `POST /api/hardware-config` replaces the whole
   document; doing that per-panel would wipe every sibling key.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from study_runner.plugin_framework.registry import get_plugin, iter_plugins

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


class PluginSettingsError(ValueError):
    """Raised for an unknown plugin, unknown path, or a value out of range."""


def get_at(section: dict[str, Any], path: str) -> Any:
    """Read a dotted path, returning None when any step is missing."""
    current: Any = section
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def set_at(section: dict[str, Any], path: str, value: Any) -> None:
    """Write a dotted path, creating intermediate dicts as needed."""
    parts = path.split(".")
    current = section
    for part in parts[:-1]:
        nested = current.get(part)
        if not isinstance(nested, dict):
            nested = {}
            current[part] = nested
        current = nested
    current[parts[-1]] = value


def _manifest_settings(plugin_key: str) -> dict[str, dict[str, Any]]:
    plugin = get_plugin(plugin_key)
    if plugin is None:
        raise PluginSettingsError(f"Unknown plugin: {plugin_key}")
    from study_runner.plugin_framework.registry import get_plugin_manifest

    manifest = get_plugin_manifest(plugin_key) or {}
    settings = manifest.get("runtime_settings")
    return settings if isinstance(settings, dict) else {}


def effective_value(hardware_config: dict[str, Any], config_key: str, field: dict[str, Any]) -> Any:
    """Disk wins; the manifest default only fills a key that is absent."""
    section = hardware_config.get(config_key)
    if isinstance(section, dict):
        stored = get_at(section, str(field.get("path") or ""))
        if stored is not None:
            return stored
    return field.get("default")


def build_plugin_settings_schema(hardware_config: dict[str, Any]) -> dict[str, Any]:
    """Schema plus current effective values, so the UI never merges them itself."""
    hardware_config = hardware_config or {}
    plugins: dict[str, Any] = {}
    for plugin in iter_plugins():
        try:
            settings = _manifest_settings(plugin.key)
        except PluginSettingsError:
            continue
        if not settings:
            continue
        fields = []
        for name, field in settings.items():
            if field.get("scope") not in (None, "machine"):
                continue
            fields.append({
                "name": name,
                "type": field.get("type", "string"),
                "path": field.get("path", name),
                "unit": field.get("unit", ""),
                "options": field.get("options", []),
                "minimum": field.get("minimum"),
                "maximum": field.get("maximum"),
                "label_key": field.get("label_key", ""),
                "hint_key": field.get("hint_key", ""),
                "apply": field.get("apply", "restart"),
                "value": effective_value(hardware_config, plugin.config_key, field),
                "default": field.get("default"),
            })
        if fields:
            plugins[plugin.key] = {
                "label": plugin.label,
                "config_key": plugin.config_key,
                "fields": fields,
            }
    return plugins


def _coerce(field: dict[str, Any], raw: Any) -> Any:
    field_type = field.get("type", "string")
    if field_type == "boolean":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in TRUE_VALUES:
            return True
        if text in FALSE_VALUES:
            return False
        raise PluginSettingsError(f"{field.get('path')} must be true or false.")

    if field_type == "number":
        try:
            number = float(raw)
        except (TypeError, ValueError) as error:
            raise PluginSettingsError(f"{field.get('path')} must be a number.") from error
        minimum, maximum = field.get("minimum"), field.get("maximum")
        if minimum is not None and number < minimum:
            raise PluginSettingsError(f"{field.get('path')} must be at least {minimum}.")
        if maximum is not None and number > maximum:
            raise PluginSettingsError(f"{field.get('path')} must be at most {maximum}.")
        # Keep ints as ints so the config file stays readable.
        return int(number) if float(number).is_integer() and isinstance(field.get("default"), int) else number

    if field_type == "choice":
        text = str(raw)
        options = field.get("options") or []
        if options and text not in options:
            raise PluginSettingsError(f"{field.get('path')} must be one of: {', '.join(options)}.")
        return text

    return str(raw)


def apply_plugin_settings(
    hardware_config: dict[str, Any],
    plugin_key: str,
    updates: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Return (new config, restart_required). Never mutates the input."""
    settings = _manifest_settings(plugin_key)
    if not settings:
        raise PluginSettingsError(f"{plugin_key} has no editable settings.")
    plugin = get_plugin(plugin_key)
    config_key = plugin.config_key

    updated = deepcopy(hardware_config or {})
    section = updated.get(config_key)
    if not isinstance(section, dict):
        section = {}
        updated[config_key] = section

    restart_required = False
    for name, raw in updates.items():
        field = settings.get(name)
        if field is None or field.get("scope") not in (None, "machine"):
            raise PluginSettingsError(f"Unknown setting for {plugin_key}: {name}")
        set_at(section, str(field.get("path") or name), _coerce(field, raw))
        if field.get("apply", "restart") == "restart":
            restart_required = True

    return updated, restart_required
