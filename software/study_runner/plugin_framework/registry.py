from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .adapter_utils import config_section
from .plugin_catalog import (
    DEFAULT_PLUGINS_DIRECTORY,
    PluginCatalog,
    discover_plugin_catalog,
    validate_admin_action_payload,
)
from .plugin_api import PluginContext, Plugin


def build_context(
    *,
    base_dir: Path,
    data_dir: Path,
    hardware_config: dict[str, Any],
    local_secrets: dict[str, Any],
    local_secrets_file: Path,
    runtime_locked: bool = False,
    persist_hardware_config=None,
) -> PluginContext:
    return PluginContext(
        base_dir=Path(base_dir),
        data_dir=Path(data_dir),
        hardware_config=hardware_config,
        local_secrets=local_secrets,
        local_secrets_file=Path(local_secrets_file),
        runtime_locked=runtime_locked,
        persist_hardware_config=persist_hardware_config,
    )


def iter_plugins() -> tuple[Plugin, ...]:
    return PLUGINS


def get_plugin(key: str) -> Plugin | None:
    return PLUGINS_BY_KEY.get(str(key or "").strip())


def get_plugin_config_key(key: str) -> str | None:
    plugin = get_plugin(key)
    return plugin.config_key if plugin else None


def get_plugin_manifests() -> dict[str, dict[str, Any]]:
    return _PLUGIN_CATALOG.manifests


def get_plugin_manifest(key: str) -> dict[str, Any]:
    plugin = _require_plugin(key)
    return get_plugin_manifests()[plugin.key]


def get_sample_metadata_model() -> list[str]:
    return list(DEFAULT_SAMPLE_METADATA_MODEL)


def get_plugin_catalog() -> PluginCatalog:
    return _PLUGIN_CATALOG


def get_plugin_catalog_payload() -> dict[str, Any]:
    return _PLUGIN_CATALOG.public_payload()


def get_plugins_with_capability(capability: str) -> tuple[Plugin, ...]:
    name = str(capability or "").strip()
    manifests = get_plugin_manifests()
    return tuple(
        plugin
        for plugin in PLUGINS
        if name in set((manifests.get(plugin.key) or {}).get("capabilities") or [])
    )


def get_backup_projection_specs(
    active_plugin_keys: set[str] | tuple[str, ...] | list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return worker-ready projections declared by active sensor plugins."""

    selected = set(active_plugin_keys) if active_plugin_keys is not None else None
    specs: list[dict[str, Any]] = []
    for plugin_key, manifest in get_plugin_manifests().items():
        if selected is not None and plugin_key not in selected:
            continue
        projection = (manifest.get("capability_config") or {}).get("backup_projection")
        if not isinstance(projection, dict):
            continue
        specs.append(
            {
                "plugin_key": plugin_key,
                "rate_hz": projection["rate_hz"],
                "channels": [dict(channel) for channel in projection["channels"]],
                "stale_after_ms": projection.get("stale_after_ms"),
            }
        )
    return specs


def initialize_plugins(context: PluginContext) -> None:
    for plugin in PLUGINS:
        initialize_plugin(plugin.key, context)


def initialize_plugin(key: str, context: PluginContext) -> None:
    plugin = _require_plugin(key)
    if plugin.initialize is None:
        return
    try:
        plugin.initialize(context)
    except Exception as error:
        print(f"[INTEGRATION] {plugin.key} initialization failed: {error}")


def get_plugin_statuses(context: PluginContext) -> dict[str, dict[str, Any]]:
    return {plugin.key: get_plugin_status(plugin.key, context) for plugin in PLUGINS}


def get_plugin_status(key: str, context: PluginContext) -> dict[str, Any]:
    plugin = _require_plugin(key)
    try:
        raw_status = plugin.get_status(context) if plugin.get_status else {}
    except Exception as error:
        raw_status = {"status": "failed", "last_message": f"Status failed: {error}"}
    return _standardize_status(plugin, context, raw_status or {})


def set_plugin_enabled(config_data: dict[str, Any], key: str, enabled: bool) -> dict[str, Any]:
    plugin = _require_plugin(key)
    if not plugin.can_toggle:
        raise ValueError(f"Plugin '{key}' cannot be toggled directly.")

    section = config_data.setdefault(plugin.config_key, {})
    if not isinstance(section, dict):
        section = {}
        config_data[plugin.config_key] = section
    section["enabled"] = bool(enabled)
    return config_data


def apply_enabled_runtime(key: str, enabled: bool, context: PluginContext) -> None:
    plugin = _require_plugin(key)
    if enabled:
        initialize_plugin(key, context)
        return

    if plugin.stop:
        plugin.stop(context)
    lifecycle = get_plugin_manifest(key).get("lifecycle") or {}
    if plugin.initialize and lifecycle.get("reinitialize_on_disable") is True:
        initialize_plugin(key, context)


def run_runtime_action(key: str, action: str, context: PluginContext) -> dict[str, Any]:
    plugin = _require_plugin(key)
    normalized = str(action or "").strip().lower()
    action_map = {
        "start": (plugin.can_start, plugin.start),
        "stop": (plugin.can_stop, plugin.stop),
        "restart": (plugin.can_restart, plugin.restart),
    }
    if normalized not in action_map:
        raise ValueError("Action must be start, stop, or restart.")

    supported, handler = action_map[normalized]
    if not supported or handler is None:
        raise ValueError(f"Plugin '{key}' does not support {normalized}.")

    result = handler(context)
    return {
        "ok": True,
        "plugin": key,
        "action": normalized,
        "result": result,
        "status": get_plugin_status(key, context),
    }


def run_trial_start(
    options: dict[str, Any],
    context: PluginContext,
    prior_outcomes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    return _run_trial_callbacks("start", "on_trial_start", options, context, prior_outcomes)


def run_trial_stop(
    options: dict[str, Any],
    context: PluginContext,
    prior_outcomes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    return _run_trial_callbacks("stop", "on_trial_stop", options, context, prior_outcomes)


def run_trial_marker(
    options: dict[str, Any],
    context: PluginContext,
    prior_outcomes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    return _run_trial_callbacks("marker", "on_trial_marker", options, context, prior_outcomes)


def _run_trial_callbacks(
    event_label: str,
    handler_name: str,
    options: dict[str, Any],
    context: PluginContext,
    prior_outcomes: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """Dispatch every enabled plugin and report every outcome.

    A failed plugin must remain visible to the durable trial-event journal.
    Successful components from a prior attempt are retained and skipped so a
    retry repairs only the failed component instead of duplicating markers or
    commands that already reached hardware.
    """

    outcomes = dict(prior_outcomes or {})
    for plugin in PLUGINS:
        handler = getattr(plugin, handler_name)
        if handler is None or not _is_config_enabled(context, plugin):
            continue
        component = f"plugin.{plugin.key}"
        previous = outcomes.get(component)
        if isinstance(previous, dict) and previous.get("ok") is True:
            continue
        try:
            handler(context, options)
        except Exception as error:
            outcomes[component] = {
                "ok": False,
                "plugin": plugin.key,
                "event": event_label,
                "error": str(error),
            }
            print(f"[INTEGRATION] {plugin.key} trial {event_label} failed: {error}")
        else:
            outcomes[component] = {
                "ok": True,
                "plugin": plugin.key,
                "event": event_label,
            }
    return outcomes


def build_interval_summary(
    context: PluginContext,
    start_epoch: float,
    end_epoch: float,
) -> dict[str, dict[str, Any]]:
    summary = _empty_interval_summary()
    for plugin in PLUGINS:
        if plugin.get_interval_summary is None or not _is_config_enabled(context, plugin):
            continue
        try:
            # "enabled" separates "sensor off" from "sensor on but no data",
            # so missing data can be flagged as a real gap in the results.
            summary[plugin.key] = {**plugin.get_interval_summary(context, start_epoch, end_epoch), "enabled": True}
        except Exception:
            summary[plugin.key] = {"available": False, "enabled": True}
    return summary


def export_interval_sidecars(
    context: PluginContext,
    start_epoch: float,
    end_epoch: float,
) -> list[dict[str, Any]]:
    exports: list[dict[str, Any]] = []
    for plugin in PLUGINS:
        if plugin.export_interval_samples is None or not plugin.sidecar_sensor:
            continue
        if not _is_config_enabled(context, plugin):
            continue
        try:
            samples = plugin.export_interval_samples(context, start_epoch, end_epoch)
        except Exception as error:
            print(f"[DATA] Could not export {plugin.key} sidecar samples: {error}")
            continue
        if not samples:
            continue
        exports.append(
            {
                "plugin_key": plugin.key,
                "sensor": plugin.sidecar_sensor,
                "filename_suffix": plugin.sidecar_filename_suffix or plugin.key,
                "output_key": plugin.sidecar_output_key or f"{plugin.key}_file",
                "samples": samples,
            }
        )
    return exports


def _standardize_status(
    plugin: Plugin,
    context: PluginContext,
    raw_status: dict[str, Any],
) -> dict[str, Any]:
    config = config_section(context, plugin.config_key)
    configured_enabled = bool(raw_status.get("configured_enabled", config.get("enabled", False)))
    runtime_enabled = bool(raw_status.get("runtime_enabled", raw_status.get("enabled", configured_enabled)))
    status_value = str(raw_status.get("status") or ("enabled" if runtime_enabled else "disabled"))

    payload = dict(raw_status)
    payload.update(
        {
            "key": plugin.key,
            "label": plugin.label,
            "category": plugin.category,
            "config_key": plugin.config_key,
            "configured_enabled": configured_enabled,
            "runtime_enabled": runtime_enabled,
            "enabled": configured_enabled,
            "status": status_value,
            "last_message": raw_status.get("last_message") or _default_status_message(configured_enabled),
            "last_activity_at": raw_status.get("last_activity_at"),
            "device_label": raw_status.get("device_label") or plugin.label,
            "can_start": plugin.can_start,
            "can_stop": plugin.can_stop,
            "can_restart": plugin.can_restart,
            "can_toggle": plugin.can_toggle,
            "has_lsl": plugin.has_lsl,
            "has_recording": plugin.has_recording,
        }
    )

    if plugin.has_lsl and "lsl_enabled" not in payload:
        payload["lsl_enabled"] = bool((config_section(context, plugin.config_key).get("lsl") or {}).get("enabled", False))
    return payload


def reset_manifest_cache() -> None:
    """Reload shipped manifests and plugin objects. Used by focused tests."""
    reload_plugin_catalog()


def reload_plugin_catalog() -> PluginCatalog:
    global _PLUGIN_CATALOG, PLUGINS, PLUGINS_BY_KEY
    _PLUGIN_CATALOG = discover_plugin_catalog()
    PLUGINS = _PLUGIN_CATALOG.plugins
    PLUGINS_BY_KEY = {plugin.key: plugin for plugin in PLUGINS}
    _report_invalid_plugins(_PLUGIN_CATALOG)
    return _PLUGIN_CATALOG


def _default_status_message(configured_enabled: bool) -> str:
    return "Plugin is enabled." if configured_enabled else "Plugin is disabled in hardware settings."


def _require_plugin(key: str) -> Plugin:
    plugin = get_plugin(key)
    if plugin is None:
        known = ", ".join(sorted(PLUGINS_BY_KEY))
        raise ValueError(f"Unknown plugin '{key}'. Expected one of: {known}.")
    return plugin


def _is_config_enabled(context: PluginContext, plugin: Plugin) -> bool:
    manifest = get_plugin_manifests().get(plugin.key) or {}
    capabilities = set(manifest.get("capabilities") or [])
    if "recording_source" in capabilities and "study_sensor" not in capabilities:
        # Marker and clock-diagnostic sources are internal mandatory session
        # infrastructure and deliberately have no machine/UI enable switch.
        return True
    return bool(config_section(context, plugin.config_key).get("enabled", False))


def _empty_interval_summary() -> dict[str, dict[str, Any]]:
    return {
        plugin.key: {"available": False}
        for plugin in PLUGINS
        if plugin.get_interval_summary is not None
    }


def run_admin_action(
    key: str,
    action: str,
    context: PluginContext,
    payload: Any = None,
) -> dict[str, Any]:
    """Run one manifest-declared plugin action through the generic boundary."""

    plugin = _require_plugin(key)
    normalized = str(action or "").strip()
    action_config = (
        (get_plugin_manifest(key).get("capability_config") or {})
        .get("admin_actions", {})
    )
    declared = next(
        (
            item
            for item in action_config.get("actions", [])
            if isinstance(item, dict) and str(item.get("key") or "") == normalized
        ),
        None,
    )
    if declared is None:
        raise ValueError(f"Plugin '{key}' does not declare admin action '{normalized}'.")
    if plugin.run_admin_action is None:
        raise ValueError(f"Plugin '{key}' has no admin action handler.")

    validated_payload = validate_admin_action_payload(
        declared,
        {} if payload is None else payload,
    )
    result = plugin.run_admin_action(context, normalized, validated_payload)
    response = {
        "ok": True,
        "plugin_key": key,
        "action_key": normalized,
        "plugin": key,
        "action": normalized,
        "result": result,
        "status": get_plugin_status(key, context),
    }
    if isinstance(result, dict) and result.get("study_controlled") is True:
        response["study_controlled"] = True
    return response


def run_participant_action(
    key: str,
    action: str,
    context: PluginContext,
    payload: Any = None,
) -> dict[str, Any]:
    """Run one manifest-declared participant lifecycle action."""

    plugin = _require_plugin(key)
    normalized = str(action or "").strip()
    declared = (
        (get_plugin_manifest(key).get("capability_config") or {})
        .get("participant_actions", {})
        .get("actions", [])
    )
    if normalized not in declared:
        raise ValueError(
            f"Plugin '{key}' does not declare participant action '{normalized}'."
        )
    if plugin.run_participant_action is None:
        raise ValueError(f"Plugin '{key}' has no participant action handler.")
    normalized_payload = {} if payload is None else payload
    if not isinstance(normalized_payload, dict):
        raise ValueError("participant action payload must be a JSON object")

    result = plugin.run_participant_action(context, normalized, normalized_payload)
    return {
        "ok": True,
        "plugin_key": key,
        "action_key": normalized,
        "result": result,
        "status": get_plugin_status(key, context),
    }


def ingest_participant_payload(
    key: str,
    ingest_key: str,
    context: PluginContext,
    payload: Any,
) -> dict[str, Any]:
    """Dispatch participant data only to a manifest-declared plugin input."""

    plugin = _require_plugin(key)
    normalized = str(ingest_key or "").strip()
    declared = (
        (get_plugin_manifest(key).get("capability_config") or {})
        .get("participant_ingest", {})
        .get("inputs", [])
    )
    if normalized not in declared:
        raise ValueError(
            f"Plugin '{key}' does not declare participant ingest '{normalized}'."
        )
    if plugin.ingest_participant is None:
        raise ValueError(f"Plugin '{key}' has no participant ingest handler.")
    if not isinstance(payload, dict):
        raise ValueError("participant ingest payload must be a JSON object")

    _validate_participant_transport_metadata(get_plugin_manifest(key), payload)

    result = plugin.ingest_participant(context, normalized, payload)
    accepted = bool(result.get("accepted", True)) if isinstance(result, dict) else True
    return {
        "ok": accepted,
        "plugin_key": key,
        "ingest_key": normalized,
        "result": result,
    }


def _validate_participant_transport_metadata(
    manifest: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """Enforce source-quality fields declared for browser HTTPS acquisition."""

    transport = (
        (manifest.get("capability_config") or {}).get("acquisition_transport") or {}
    )
    if transport.get("transport") != "browser_https":
        return

    if transport.get("sequence_required") is True:
        sequence = payload.get("sequence_number")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError(
                "browser participant ingest requires a non-negative integer sequence_number"
            )

    if transport.get("source_timestamp_required") is True:
        fields = transport.get("source_timestamp_fields") or [
            "source_epoch_ms",
            "source_timestamp",
        ]
        has_source_time = any(
            not isinstance(payload.get(field), bool)
            and isinstance(payload.get(field), (int, float))
            and math.isfinite(float(payload[field]))
            for field in fields
        )
        if not has_source_time:
            raise ValueError(
                "browser participant ingest requires a finite source timestamp in one of: "
                + ", ".join(str(field) for field in fields)
            )


def resolve_plugin_ui_asset(key: str, asset_path: str) -> Path:
    """Resolve only an exact, manifest-declared asset below a trusted plugin."""

    manifest = get_plugin_manifest(key)
    ui = manifest.get("ui") or {}
    declared = {
        *((ui.get("extensions") or {}).values()),
        *(ui.get("assets") or []),
    }
    normalized = str(asset_path or "").replace("\\", "/")
    if normalized not in declared:
        raise ValueError(f"Plugin '{key}' does not declare UI asset '{normalized}'.")
    plugin_root = (DEFAULT_PLUGINS_DIRECTORY / str(manifest["directory"])).resolve()
    candidate = (plugin_root / normalized).resolve()
    try:
        candidate.relative_to(plugin_root)
    except ValueError as error:
        raise ValueError("Plugin UI asset escapes its trusted directory.") from error
    if not candidate.is_file():
        raise ValueError(f"Declared UI asset is missing: {normalized}")
    return candidate


DEFAULT_SAMPLE_METADATA_MODEL = (
    "source_epoch_ms",
    "server_received_epoch_ms",
    "processing_epoch_ms",
    "sequence_number",
    "latency_ms",
    "clock_domain",
    "drop_count",
)


def _report_invalid_plugins(catalog: PluginCatalog) -> None:
    for entry in catalog.invalid_entries:
        details = "; ".join(entry.errors)
        print(f"[INTEGRATION] Ignoring invalid plugin folder '{entry.directory}': {details}")


_PLUGIN_CATALOG = discover_plugin_catalog()
PLUGINS: tuple[Plugin, ...] = _PLUGIN_CATALOG.plugins
PLUGINS_BY_KEY = {plugin.key: plugin for plugin in PLUGINS}
_report_invalid_plugins(_PLUGIN_CATALOG)
