from __future__ import annotations

from copy import deepcopy
from typing import Any


STUDY_SENSOR_KEYS = ("brainbit", "mini_radar", "camera_emotion")
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
) -> dict[str, Any]:
    effective_config = deepcopy(hardware_config if isinstance(hardware_config, dict) else {})
    sensors = normalize_study_sensors(study_settings)
    for key, enabled in sensors.items():
        section = effective_config.get(key)
        if not isinstance(section, dict):
            section = {}
            effective_config[key] = section
        section["enabled"] = bool(enabled)
    return effective_config


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
