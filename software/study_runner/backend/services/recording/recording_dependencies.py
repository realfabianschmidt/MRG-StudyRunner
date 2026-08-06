"""Recording dependency probes and capability-based provider selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from study_runner.plugin_framework.registry import get_plugin_manifests


PINNED_PYLSL_VERSION = "1.18.2"


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


@dataclass(frozen=True)
class InternalProviderResolution:
    plugin_keys: tuple[str, ...]
    marker_plugin_keys: tuple[str, ...]
    clock_plugin_keys: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.errors


def resolve_internal_recording_plugins(
    manifests: Mapping[str, Mapping[str, Any]] | None = None,
) -> InternalProviderResolution:
    """Resolve hidden session providers entirely from declared capabilities."""

    catalog = manifests if manifests is not None else get_plugin_manifests()
    providers: list[str] = []
    marker_plugins: list[str] = []
    clock_plugins: list[str] = []
    for plugin_key, manifest in catalog.items():
        capabilities = set(manifest.get("capabilities") or [])
        internal_role = bool(
            {"internal_recording_provider", "marker_stream", "clock_diagnostics"}
            & capabilities
        )
        if not internal_role:
            continue
        if not {"recording_source", "lsl_stream_provider"}.issubset(capabilities):
            continue
        if "study_sensor" in capabilities:
            continue
        providers.append(str(plugin_key))
        if "marker_stream" in capabilities:
            marker_plugins.append(str(plugin_key))
        if "clock_diagnostics" in capabilities:
            clock_plugins.append(str(plugin_key))

    errors: list[str] = []
    if len(marker_plugins) != 1:
        errors.append(
            "exactly one internal marker_stream recording provider is required "
            f"(found {len(marker_plugins)})"
        )
    if len(clock_plugins) != 1:
        errors.append(
            "exactly one internal clock_diagnostics recording provider is required "
            f"(found {len(clock_plugins)})"
        )
    return InternalProviderResolution(
        plugin_keys=tuple(dict.fromkeys(providers)),
        marker_plugin_keys=tuple(marker_plugins),
        clock_plugin_keys=tuple(clock_plugins),
        errors=tuple(errors),
    )


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
