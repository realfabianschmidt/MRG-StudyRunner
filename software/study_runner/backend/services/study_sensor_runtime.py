from __future__ import annotations

from copy import deepcopy
from typing import Any


def _study_sensor_contract() -> tuple[tuple[str, ...], dict[str, bool]]:
    """Read sensor membership and defaults from plugin capabilities."""
    from study_runner.integrations.registry import get_plugin_manifests

    sensor_keys: list[str] = []
    defaults: dict[str, bool] = {}
    for plugin_key, manifest in get_plugin_manifests().items():
        if "study_sensor" not in set(manifest.get("capabilities") or []):
            continue
        sensor_keys.append(plugin_key)
        config = (manifest.get("capability_config") or {}).get("study_sensor") or {}
        defaults[plugin_key] = bool(config.get("default_enabled", False))
    return tuple(sensor_keys), defaults


STUDY_SENSOR_KEYS, DEFAULT_STUDY_SENSORS = _study_sensor_contract()
SESSION_OVERRIDE_KEYS = STUDY_SENSOR_KEYS


def normalize_study_sensors(study_settings: dict[str, Any] | None) -> dict[str, bool]:
    settings = study_settings if isinstance(study_settings, dict) else {}
    sensors_enabled = _normalize_sensor_bool(settings.get("sensors_enabled", True))
    if not sensors_enabled:
        return {key: False for key in STUDY_SENSOR_KEYS}

    raw_plugins = settings.get("plugins")
    if isinstance(raw_plugins, dict):
        return {
            key: _normalize_sensor_bool(
                (raw_plugins.get(key) or {}).get("enabled", DEFAULT_STUDY_SENSORS[key])
            )
            if isinstance(raw_plugins.get(key), dict)
            else DEFAULT_STUDY_SENSORS[key]
            for key in STUDY_SENSOR_KEYS
        }

    raw_sensors = settings.get("sensors")
    if not isinstance(raw_sensors, dict):
        return dict(DEFAULT_STUDY_SENSORS)

    return {
        key: _normalize_sensor_bool(raw_sensors.get(key, DEFAULT_STUDY_SENSORS[key]))
        for key in STUDY_SENSOR_KEYS
    }


def build_effective_hardware_config(
    hardware_config: dict[str, Any],
    study_settings: dict[str, Any] | None,
    session_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_config = deepcopy(hardware_config if isinstance(hardware_config, dict) else {})
    runtime_state = build_sensor_runtime_state(hardware_config, study_settings, session_overrides)
    overrides = normalize_session_overrides(session_overrides)

    for key, enabled in runtime_state["effective"].items():
        section = effective_config.get(key)
        if not isinstance(section, dict):
            section = {}
            effective_config[key] = section
        section["enabled"] = bool(enabled)

    return effective_config


def build_sensor_runtime_state(
    hardware_config: dict[str, Any] | None,
    study_settings: dict[str, Any] | None,
    session_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = hardware_config if isinstance(hardware_config, dict) else {}
    study = normalize_study_sensors(study_settings)
    overrides = normalize_session_overrides(session_overrides)
    effective = {
        key: bool(overrides[key]) if key in overrides else bool(study[key])
        for key in STUDY_SENSOR_KEYS
    }
    hardware = {key: _section_enabled(config, key) for key in STUDY_SENSOR_KEYS}
    return {
        "study": study,
        "hardware": hardware,
        "overrides": {key: overrides.get(key) for key in STUDY_SENSOR_KEYS},
        "override_active": {key: key in overrides for key in STUDY_SENSOR_KEYS},
        "effective": effective,
    }


def normalize_session_overrides(overrides: dict[str, Any] | None) -> dict[str, bool]:
    if not isinstance(overrides, dict):
        return {}
    normalized: dict[str, bool] = {}
    for key in SESSION_OVERRIDE_KEYS:
        if key in overrides and overrides[key] is not None:
            normalized[key] = _normalize_sensor_bool(overrides[key])
    return normalized


def _section_enabled(config: dict[str, Any], key: str) -> bool:
    section = config.get(key)
    return bool(section.get("enabled", False)) if isinstance(section, dict) else False


def _normalize_sensor_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"0", "false", "no", "off"}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
    return bool(value)
