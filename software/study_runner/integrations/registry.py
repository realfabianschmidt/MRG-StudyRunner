from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .adapter_utils import config_section
from .brainbit import PLUGIN as BRAINBIT_PLUGIN
from .labrecorder_xdf import PLUGIN as LABRECORDER_PLUGIN
from .local_emotion_worker import PLUGIN as EMOTION_WORKER_PLUGIN
from .lsl_markers import PLUGIN as LSL_PLUGIN
from .mr60_mini_radar import PLUGIN as MINI_RADAR_PLUGIN
from .notion_upload import PLUGIN as NOTION_PLUGIN
from .osc_touchdesigner import PLUGIN as OSC_PLUGIN
from .plugin_api import IntegrationContext, IntegrationPlugin
from .tablet_camera_emotion import PLUGIN as CAMERA_EMOTION_PLUGIN


def build_context(
    *,
    base_dir: Path,
    data_dir: Path,
    hardware_config: dict[str, Any],
    local_secrets: dict[str, Any],
    local_secrets_file: Path,
) -> IntegrationContext:
    return IntegrationContext(
        base_dir=Path(base_dir),
        data_dir=Path(data_dir),
        hardware_config=hardware_config,
        local_secrets=local_secrets,
        local_secrets_file=Path(local_secrets_file),
    )


def iter_plugins() -> tuple[IntegrationPlugin, ...]:
    return PLUGINS


def get_plugin(key: str) -> IntegrationPlugin | None:
    return PLUGINS_BY_KEY.get(str(key or "").strip())


def get_plugin_config_key(key: str) -> str | None:
    plugin = get_plugin(key)
    return plugin.config_key if plugin else None


def get_plugin_manifests() -> dict[str, dict[str, Any]]:
    raw_catalog = _load_manifest_catalog()
    raw_plugins = raw_catalog.get("plugins") if isinstance(raw_catalog.get("plugins"), dict) else {}
    return {
        plugin.key: _standardize_manifest(plugin, raw_plugins.get(plugin.key) if isinstance(raw_plugins, dict) else {})
        for plugin in PLUGINS
    }


def get_plugin_manifest(key: str) -> dict[str, Any]:
    plugin = _require_plugin(key)
    return get_plugin_manifests()[plugin.key]


def get_sample_metadata_model() -> list[str]:
    raw_catalog = _load_manifest_catalog()
    fields = raw_catalog.get("sample_metadata_model")
    if isinstance(fields, list) and all(isinstance(field, str) and field for field in fields):
        return list(fields)
    return list(DEFAULT_SAMPLE_METADATA_MODEL)


def initialize_plugins(context: IntegrationContext) -> None:
    for plugin in PLUGINS:
        initialize_plugin(plugin.key, context)


def initialize_plugin(key: str, context: IntegrationContext) -> None:
    plugin = _require_plugin(key)
    if plugin.initialize is None:
        return
    try:
        plugin.initialize(context)
    except Exception as error:
        print(f"[INTEGRATION] {plugin.key} initialization failed: {error}")


def get_integration_statuses(context: IntegrationContext) -> dict[str, dict[str, Any]]:
    return {plugin.key: get_plugin_status(plugin.key, context) for plugin in PLUGINS}


def get_plugin_status(key: str, context: IntegrationContext) -> dict[str, Any]:
    plugin = _require_plugin(key)
    try:
        raw_status = plugin.get_status(context) if plugin.get_status else {}
    except Exception as error:
        raw_status = {"status": "failed", "last_message": f"Status failed: {error}"}
    return _standardize_status(plugin, context, raw_status or {})


def set_plugin_enabled(config_data: dict[str, Any], key: str, enabled: bool) -> dict[str, Any]:
    plugin = _require_plugin(key)
    if not plugin.can_toggle:
        raise ValueError(f"Integration '{key}' cannot be toggled directly.")

    section = config_data.setdefault(plugin.config_key, {})
    if not isinstance(section, dict):
        section = {}
        config_data[plugin.config_key] = section
    section["enabled"] = bool(enabled)
    return config_data


def apply_enabled_runtime(key: str, enabled: bool, context: IntegrationContext) -> None:
    plugin = _require_plugin(key)
    if enabled:
        initialize_plugin(key, context)
        return

    if plugin.stop:
        plugin.stop(context)
    if plugin.initialize and key in {"mini_radar", "camera_emotion", "notion"}:
        initialize_plugin(key, context)


def run_runtime_action(key: str, action: str, context: IntegrationContext) -> dict[str, Any]:
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
        raise ValueError(f"Integration '{key}' does not support {normalized}.")

    result = handler(context)
    return {
        "ok": True,
        "integration": key,
        "action": normalized,
        "result": result,
        "status": get_plugin_status(key, context),
    }


def run_trial_start(options: dict[str, Any], context: IntegrationContext) -> None:
    for plugin in PLUGINS:
        if plugin.on_trial_start is None or not _is_config_enabled(context, plugin):
            continue
        try:
            plugin.on_trial_start(context, options)
        except Exception as error:
            print(f"[INTEGRATION] {plugin.key} trial start failed: {error}")


def run_trial_stop(options: dict[str, Any], context: IntegrationContext) -> None:
    for plugin in PLUGINS:
        if plugin.on_trial_stop is None or not _is_config_enabled(context, plugin):
            continue
        try:
            plugin.on_trial_stop(context, options)
        except Exception as error:
            print(f"[INTEGRATION] {plugin.key} trial stop failed: {error}")


def run_trial_marker(options: dict[str, Any], context: IntegrationContext) -> None:
    for plugin in PLUGINS:
        if plugin.on_trial_marker is None or not _is_config_enabled(context, plugin):
            continue
        try:
            plugin.on_trial_marker(context, options)
        except Exception as error:
            print(f"[INTEGRATION] {plugin.key} trial marker failed: {error}")


def build_interval_summary(
    context: IntegrationContext,
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
    context: IntegrationContext,
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
    plugin: IntegrationPlugin,
    context: IntegrationContext,
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


def _load_manifest_catalog() -> dict[str, Any]:
    global _MANIFEST_CATALOG_CACHE
    if _MANIFEST_CATALOG_CACHE is not None:
        return _MANIFEST_CATALOG_CACHE
    try:
        payload = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"[INTEGRATION] Could not read plugin manifests: {error}")
        _MANIFEST_CATALOG_CACHE = {}
        return _MANIFEST_CATALOG_CACHE
    _MANIFEST_CATALOG_CACHE = payload if isinstance(payload, dict) else {}
    return _MANIFEST_CATALOG_CACHE


def _standardize_manifest(plugin: IntegrationPlugin, raw_manifest: Any) -> dict[str, Any]:
    manifest = raw_manifest if isinstance(raw_manifest, dict) else {}
    streams = manifest.get("streams") if isinstance(manifest.get("streams"), list) else []
    backpressure = manifest.get("backpressure") if isinstance(manifest.get("backpressure"), dict) else {}
    runtime_settings = manifest.get("runtime_settings") if isinstance(manifest.get("runtime_settings"), dict) else {}
    expected_data_rate = manifest.get("expected_data_rate") if isinstance(manifest.get("expected_data_rate"), dict) else {}

    return {
        "plugin_key": plugin.key,
        "config_key": plugin.config_key,
        "capabilities": _string_list(manifest.get("capabilities")) or _default_capabilities(plugin),
        "streams": [dict(stream) for stream in streams if isinstance(stream, dict)],
        "poll_interval_ms": _positive_int(manifest.get("poll_interval_ms"), DEFAULT_POLL_INTERVAL_MS),
        "request_timeout_ms": _positive_int(manifest.get("request_timeout_ms"), DEFAULT_REQUEST_TIMEOUT_MS),
        "clock_domain": str(manifest.get("clock_domain") or _default_clock_domain(plugin)),
        "expected_data_rate": dict(expected_data_rate),
        "backpressure": {
            "max_in_flight": _positive_int(backpressure.get("max_in_flight"), 1),
            "drop_policy": str(backpressure.get("drop_policy") or "latest_status_wins"),
        },
        "runtime_settings": dict(runtime_settings),
    }


def _default_capabilities(plugin: IntegrationPlugin) -> list[str]:
    capabilities = ["status_poll"]
    if plugin.can_start or plugin.can_stop or plugin.can_restart:
        capabilities.append("runtime_control")
    if plugin.has_recording:
        capabilities.append("recording")
    if plugin.has_lsl:
        capabilities.append("lsl_stream")
    if plugin.get_interval_summary:
        capabilities.append("interval_summary")
    if plugin.export_interval_samples:
        capabilities.append("sidecar_export")
    return capabilities


def _default_clock_domain(plugin: IntegrationPlugin) -> str:
    return "lsl" if plugin.has_lsl else "server"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _positive_int(value: Any, fallback: int) -> int:
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        return fallback
    return candidate if candidate > 0 else fallback


def _default_status_message(configured_enabled: bool) -> str:
    return "Integration is enabled." if configured_enabled else "Integration is disabled in hardware settings."


def _require_plugin(key: str) -> IntegrationPlugin:
    plugin = get_plugin(key)
    if plugin is None:
        known = ", ".join(sorted(PLUGINS_BY_KEY))
        raise ValueError(f"Unknown integration '{key}'. Expected one of: {known}.")
    return plugin


def _is_config_enabled(context: IntegrationContext, plugin: IntegrationPlugin) -> bool:
    return bool(config_section(context, plugin.config_key).get("enabled", False))


def _empty_interval_summary() -> dict[str, dict[str, Any]]:
    return {
        "brainbit": {"available": False},
        "mini_radar": {"available": False},
        "camera_emotion": {"available": False},
    }


PLUGINS: tuple[IntegrationPlugin, ...] = (
    BRAINBIT_PLUGIN,
    MINI_RADAR_PLUGIN,
    CAMERA_EMOTION_PLUGIN,
    EMOTION_WORKER_PLUGIN,
    LSL_PLUGIN,
    OSC_PLUGIN,
    LABRECORDER_PLUGIN,
    NOTION_PLUGIN,
)

PLUGINS_BY_KEY = {plugin.key: plugin for plugin in PLUGINS}
MANIFEST_FILE = Path(__file__).with_name("plugin_manifests.json")
DEFAULT_POLL_INTERVAL_MS = 2000
DEFAULT_REQUEST_TIMEOUT_MS = 1000
DEFAULT_SAMPLE_METADATA_MODEL = (
    "source_epoch_ms",
    "server_received_epoch_ms",
    "processing_epoch_ms",
    "sequence_number",
    "latency_ms",
    "clock_domain",
    "drop_count",
)
_MANIFEST_CATALOG_CACHE: dict[str, Any] | None = None
