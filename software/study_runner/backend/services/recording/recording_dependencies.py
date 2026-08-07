"""Recording dependency probes and capability-based provider selection."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from study_runner.plugin_framework.registry import get_plugin_manifests
from study_runner.recording import clock_diagnostics, markers


PINNED_PYLSL_VERSION = "1.18.2"

# The two recording sources every session carries, whether or not the study
# asked for them -- see recording/markers.py and recording/clock_diagnostics.py.
# There is exactly one Python module for each, so unlike a discovered plugin
# there is no "found zero" or "found two" to guard against.
INTERNAL_RECORDING_SOURCE_KEYS = (markers.SOURCE_KEY, clock_diagnostics.SOURCE_KEY)
INTERNAL_RECORDING_MANIFESTS: dict[str, dict[str, Any]] = {
    markers.SOURCE_KEY: markers.MANIFEST,
    clock_diagnostics.SOURCE_KEY: clock_diagnostics.MANIFEST,
}


def get_plugin_manifests_with_internal_sources() -> dict[str, dict[str, Any]]:
    """Every discovered plugin's manifest, plus the two built-in recording sources."""
    return {**get_plugin_manifests(), **INTERNAL_RECORDING_MANIFESTS}


def probe_lsl_dependencies(
    require_pylsl: Callable[[], Any],
    version_info: Callable[[Any], Mapping[str, Any]],
) -> dict[str, Any]:
    """Probe Python and native liblsl without binding the caller's test seam."""

    try:
        pylsl = require_pylsl()
        versions = dict(version_info(pylsl))
    except Exception as error:
        return {
            "ok": False,
            "pylsl_package_version": None,
            "liblsl_library_version": None,
            "version_probe_error": f"{type(error).__name__}: {error}",
            "reason": f"pylsl/liblsl dependency probe failed: {error}",
        }

    problems: list[str] = []
    installed_pylsl = str(versions.get("pylsl_package_version") or "").strip()
    if installed_pylsl != PINNED_PYLSL_VERSION:
        problems.append(
            f"pylsl={installed_pylsl or 'missing'} (expected {PINNED_PYLSL_VERSION})"
        )
    if versions.get("version_probe_error"):
        problems.append(str(versions["version_probe_error"]))
    if versions.get("liblsl_library_version") is None:
        problems.append("native liblsl library_version is unavailable")
    return {
        "ok": not problems,
        **versions,
        "reason": "; ".join(problems) if problems else None,
    }


def selected_recording_plugins(config_data: Mapping[str, Any]) -> tuple[str, ...]:
    """Return selected study-sensor sources; internal sources are added later."""

    settings = config_data.get("study_settings") if isinstance(config_data, Mapping) else {}
    settings = settings if isinstance(settings, Mapping) else {}
    selections = settings.get("plugins")
    selections = selections if isinstance(selections, Mapping) else {}
    manifests = get_plugin_manifests()
    selected: list[str] = []
    for plugin_key, selection in selections.items():
        if not isinstance(selection, Mapping) or not bool(selection.get("enabled")):
            continue
        manifest = manifests.get(str(plugin_key)) or {}
        capabilities = set(manifest.get("capabilities") or [])
        if "study_sensor" in capabilities and "recording_source" in capabilities:
            selected.append(str(plugin_key))
    return tuple(dict.fromkeys(selected))


def required_recording_plugins(config_data: Mapping[str, Any]) -> tuple[str, ...]:
    settings = config_data.get("study_settings") if isinstance(config_data, Mapping) else {}
    settings = settings if isinstance(settings, Mapping) else {}
    selections = settings.get("plugins")
    selections = selections if isinstance(selections, Mapping) else {}
    active = set(selected_recording_plugins(config_data))
    return tuple(
        key
        for key in active
        if isinstance(selections.get(key), Mapping) and bool(selections[key].get("required", True))
    )
