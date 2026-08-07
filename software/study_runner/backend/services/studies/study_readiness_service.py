"""Is the loaded study actually able to run and deliver its results?

The gap this closes: a study imported from another computer carries
`notion_enabled: true` and its upload targets, but credentials live
backend-local and do not travel. The session would run fine and the upload
would only fail *afterwards*, quietly into the retry queue - discovered long
after the participant went home.

Deliberately a pure function over stored configuration, with no live hardware
state. Two reasons: it must give the same answer before anything is started,
and "connected right now" changes second by second, which belongs on the
dashboard rather than in a gate that blocks the Play button.

Only conditions that would *certainly* fail are reported. A missing Nextcloud
password is not one of them - public shares legitimately have none.
"""
from __future__ import annotations

import os
import platform as host_platform
import re
import sys
from typing import Any

from ..settings.runtime_config import is_https_enabled
from .study_plugin_config import normalize_study_settings_plugins
from .study_secrets_service import list_study_credential_state, resolve_plugin_secret
from ..recording.study_sensor_runtime import STUDY_SENSOR_KEYS, normalize_study_sensors

# Which left-hand panel of the study settings shell fixes each blocker, so the
# UI can send the operator straight there instead of making them hunt. Only
# the sensor-side codes are listed: a destination blocker's panel is its own
# plugin_key, declared once at the call site below rather than listed twice.
BLOCKER_PANELS = {
    "plugin_unavailable": "sensors",
    "sensor_machine_disabled": "sensors",
    "browser_source_requires_https": "sensors",
    "recording_worker_unavailable": "sensors",
    "plugin_mode_unsupported": "sensors",
}


def check_study_readiness(
    study_config: dict[str, Any],
    hardware_config: dict[str, Any],
    local_secrets: dict[str, Any],
    *,
    https_active: bool | None = None,
    recording_preflight: dict[str, Any] | None = None,
    platform_target: str | None = None,
) -> dict[str, Any]:
    """Report what would stop this study from delivering a complete result."""
    study_settings = study_config.get("study_settings") or {}
    study_id = str(study_config.get("study_id") or "")
    hardware_config = hardware_config or {}
    local_secrets = local_secrets or {}
    if https_active is None:
        https_active = is_https_enabled()
    platform_target = str(platform_target or _current_platform_target()).strip().lower()

    blockers: list[dict[str, Any]] = []

    def add(code: str, *, blocking: bool = False, panel: str | None = None, **extra: Any) -> None:
        blockers.append(
            {
                "code": code,
                "panel": panel or BLOCKER_PANELS.get(code, "sensors"),
                "blocking": bool(blocking),
                **extra,
            }
        )

    from study_runner.plugin_framework.registry import (
        get_plugin,
        get_plugin_catalog,
        get_plugin_manifests,
    )

    manifests = get_plugin_manifests()
    # A study saved before API v3 carries flat fields (`<plugin>_enabled`, ...)
    # instead of `study_settings.plugins.<key>`. The real caller already
    # migrates this via validate_and_normalize_config before readiness is ever
    # checked; doing it again here too means every check below reads one
    # shape, so a new destination plugin needs no legacy-field awareness here.
    study_settings = normalize_study_settings_plugins(study_settings, manifests=manifests)
    selected_plugins = study_settings.get("plugins") or {}

    invalid_by_key = {
        entry.plugin_key: list(entry.errors)
        for entry in get_plugin_catalog().invalid_entries
        if entry.plugin_key
    }
    for plugin_key, selection in selected_plugins.items():
        if not isinstance(selection, dict):
            continue
        if not selection.get("enabled") or not selection.get("required"):
            continue
        if get_plugin(plugin_key) is not None:
            continue
        add(
            "plugin_unavailable",
            blocking=True,
            plugin=plugin_key,
            details=invalid_by_key.get(plugin_key, ["Plugin is not installed or valid."]),
        )

    # Every plugin that declared `readiness_requirements` is checked the same
    # way - no destination is special-cased, so a new one gets this for free
    # by declaring the same capability its predecessors do.
    for plugin_key, manifest in manifests.items():
        requirements = (manifest.get("capability_config") or {}).get("readiness_requirements")
        if not requirements:
            continue
        selection = selected_plugins.get(plugin_key)
        if not isinstance(selection, dict) or not selection.get("enabled"):
            continue
        settings = selection.get("settings")
        settings = settings if isinstance(settings, dict) else {}

        if requirements.get("requires_secret") and not resolve_plugin_secret(
            plugin_key, hardware_config, local_secrets, study_id
        ):
            add(f"{plugin_key}.credential_missing", panel=plugin_key, destination=plugin_key)

        required_settings = requirements.get("requires_settings") or []
        if required_settings and not any(
            str(settings.get(field) or "").strip() for field in required_settings
        ):
            add(f"{plugin_key}.setting_missing", panel=plugin_key, destination=plugin_key)

        if requirements.get("requires_machine_enabled"):
            config_key = str(manifest.get("config_key") or plugin_key)
            machine_section = hardware_config.get(config_key)
            if isinstance(machine_section, dict) and not machine_section.get("enabled"):
                # Some destinations refuse to build a client when off
                # machine-side, so the study's own switch alone is not enough.
                add(f"{plugin_key}.machine_disabled", panel=plugin_key, destination=plugin_key)

    sensors = normalize_study_sensors(study_settings)
    for sensor_key in STUDY_SENSOR_KEYS:
        if not sensors.get(sensor_key):
            continue
        manifest = manifests.get(sensor_key) or {}
        config_key = str(manifest.get("config_key") or sensor_key)
        section = hardware_config.get(config_key)
        section = section if isinstance(section, dict) else {}
        selection = selected_plugins.get(sensor_key) if isinstance(selected_plugins, dict) else None
        required = bool(selection.get("required", True)) if isinstance(selection, dict) else True
        if not section.get("enabled"):
            add("sensor_machine_disabled", blocking=required, sensor=sensor_key)

        readiness_contract = (
            (manifest.get("capability_config") or {}).get("readiness") or {}
        )
        platform_modes = readiness_contract.get("platform_modes")
        if isinstance(platform_modes, dict) and platform_modes:
            mode_setting = str(readiness_contract.get("mode_setting") or "").strip()
            default_mode = str(readiness_contract.get("default_mode") or "").strip()
            configured_mode = str(section.get(mode_setting) or default_mode).strip()
            supported_modes = platform_modes.get(platform_target)
            if not isinstance(supported_modes, list):
                supported_modes = platform_modes.get("default", [])
            if configured_mode and configured_mode not in supported_modes:
                add(
                    "plugin_mode_unsupported",
                    blocking=required,
                    plugin=sensor_key,
                    sensor=sensor_key,
                    mode=configured_mode,
                    platform=platform_target,
                    supported_modes=list(supported_modes),
                    details=[
                        f"Mode {configured_mode!r} is unavailable on {platform_target}; "
                        f"choose one of: {', '.join(supported_modes)}."
                    ],
                )

        transport = (
            (manifest.get("capability_config") or {})
            .get("acquisition_transport", {})
            .get("transport")
        )
        if transport == "browser_https" and not https_active:
            # Browser acquisition cannot start outside a secure context. The
            # rule comes from the manifest, not from a camera/sensor key.
            add(
                "browser_source_requires_https",
                blocking=required,
                sensor=sensor_key,
                transport=transport,
            )

    if isinstance(recording_preflight, dict) and not recording_preflight.get("ready", True):
        add(
            "recording_worker_unavailable",
            blocking=bool(recording_preflight.get("required_plugins")),
            plugins=list(recording_preflight.get("selected_plugins") or []),
            details=[str(recording_preflight.get("reason") or "Native XDF worker is unavailable.")],
        )

    return {
        "ready": not blockers,
        "start_blocked": any(blocker.get("blocking") for blocker in blockers),
        "study_id": study_id,
        "blockers": blockers,
        # Which panels carry a problem, so the shell can mark its nav entries.
        "panels": sorted({blocker["panel"] for blocker in blockers}),
    }


def describe_credentials(
    study_config: dict[str, Any],
    hardware_config: dict[str, Any],
    local_secrets: dict[str, Any],
) -> dict[str, Any]:
    """Credential scope per destination, for the same report - never a value."""
    study_id = str(study_config.get("study_id") or "")
    return list_study_credential_state(hardware_config, local_secrets, study_id)


def _current_platform_target() -> str:
    if sys.platform == "darwin":
        system = "macos"
    elif os.name == "nt" or sys.platform.startswith("win"):
        system = "windows"
    elif sys.platform.startswith("linux"):
        system = "linux"
    else:
        system = re.sub(r"[^a-z0-9]+", "_", sys.platform.casefold()).strip("_") or "unknown"

    machine = host_platform.machine().strip().casefold()
    architecture = {
        "amd64": "x64",
        "x86_64": "x64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }.get(machine, re.sub(r"[^a-z0-9]+", "_", machine).strip("_") or "unknown")
    return f"{system}-{architecture}"
