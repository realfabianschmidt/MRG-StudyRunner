from __future__ import annotations

from copy import deepcopy
from typing import Any


STUDY_SENSOR_KEYS = ("brainbit", "mini_radar", "camera_emotion")
SESSION_OVERRIDE_KEYS = (*STUDY_SENSOR_KEYS, "lsl", "labrecorder")
DEFAULT_STUDY_SENSORS = {
    "brainbit": True,
    "mini_radar": True,
    "camera_emotion": False,
}


def normalize_study_sensors(study_settings: dict[str, Any] | None) -> dict[str, bool]:
    settings = study_settings if isinstance(study_settings, dict) else {}
    sensors_enabled = _normalize_sensor_bool(settings.get("sensors_enabled", True))
    if not sensors_enabled:
        return {key: False for key in STUDY_SENSOR_KEYS}

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

    for key in ("lsl", "labrecorder"):
        if key not in overrides:
            continue
        section = effective_config.get(key)
        if not isinstance(section, dict):
            section = {}
            effective_config[key] = section
        section["enabled"] = bool(overrides[key])
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
