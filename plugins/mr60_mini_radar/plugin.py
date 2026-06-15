from __future__ import annotations

from typing import Any

from ..plugin_api import IntegrationContext, IntegrationPlugin


def _config_section(context: IntegrationContext) -> dict[str, Any]:
    config = context.hardware_config.get("mini_radar") or context.hardware_config.get("radar") or {}
    return config if isinstance(config, dict) else {}


def _initialize(context: IntegrationContext) -> None:
    config = _config_section(context)
    if not config:
        return

    from . import adapter

    lsl_config = config.get("lsl") or {}
    ble_config = config.get("ble") or {}
    adapter.initialize(
        enabled=config.get("enabled", False),
        port=context.resolve_platform_value(config.get("port")) or "",
        baudrate=config.get("baudrate", 115200),
        connection_type=config.get("connection_type", "serial"),
        auto_install=config.get("auto_install", True),
        auto_reconnect=config.get("auto_reconnect", True),
        reconnect_delay=config.get("reconnect_delay", 5),
        data_timeout_seconds=config.get("data_timeout_seconds", 5),
        lsl_enabled=lsl_config.get("enabled", False),
        lsl_auto_install=lsl_config.get("auto_install", True),
        lsl_stream_prefix=lsl_config.get("stream_prefix", "MiniRadar"),
        ble_device_name=ble_config.get("device_name", "MR60_BLE"),
        ble_address=context.resolve_platform_value(ble_config.get("address")) or "",
        ble_scan_timeout_seconds=ble_config.get("scan_timeout_seconds", 5),
        ble_service_uuid=ble_config.get("service_uuid", "9d6f0001-7d2a-4c6b-9f4e-5c2b1f4a6e10"),
        ble_characteristic_uuid=ble_config.get("characteristic_uuid", "9d6f0002-7d2a-4c6b-9f4e-5c2b1f4a6e10"),
        log_dir=context.resolve_project_path(context.resolve_platform_value(config.get("log_dir")) or "saved_results"),
    )


def _status(context: IntegrationContext) -> dict[str, Any]:
    config = _config_section(context)
    from . import adapter

    status = adapter.get_status()
    ble_config = config.get("ble") or {}
    return {
        **status,
        "device_label": status.get("ble_device_name") or status.get("port") or "MR60 BLE",
        "scan_timeout_seconds": float(ble_config.get("scan_timeout_seconds", status.get("scan_timeout_seconds", 5))),
        "scan_mode": "repeated_while_enabled",
    }


def _start(context: IntegrationContext) -> Any:
    from . import adapter

    if not adapter.is_configured():
        _initialize(context)
    return adapter.start()


def _stop(context: IntegrationContext) -> Any:
    from . import adapter

    return adapter.stop()


def _restart(context: IntegrationContext) -> Any:
    from . import adapter

    if not adapter.is_configured():
        _initialize(context)
        return adapter.get_status()
    return adapter.restart()


def _trial_start(context: IntegrationContext, options: dict[str, Any]) -> None:
    from . import adapter

    adapter.set_recording(bool(options.get("mini_radar_recording_enabled", True)))


def _trial_stop(context: IntegrationContext, options: dict[str, Any]) -> None:
    from . import adapter

    adapter.set_recording(False)


def _interval(context: IntegrationContext, start_epoch: float, end_epoch: float) -> dict[str, Any]:
    from . import adapter

    return adapter.get_interval_summary(start_epoch, end_epoch)


def _export(context: IntegrationContext, start_epoch: float, end_epoch: float) -> list[dict[str, Any]]:
    from . import adapter

    return adapter.export_interval_samples(start_epoch, end_epoch)


PLUGIN = IntegrationPlugin(
    key="mini_radar",
    label="MR60 Mini-radar",
    category="biosignal",
    config_key="mini_radar",
    can_start=True,
    can_stop=True,
    can_restart=True,
    has_lsl=True,
    has_recording=True,
    initialize=_initialize,
    get_status=_status,
    start=_start,
    stop=_stop,
    restart=_restart,
    on_trial_start=_trial_start,
    on_trial_stop=_trial_stop,
    get_interval_summary=_interval,
    export_interval_samples=_export,
    sidecar_sensor="mr60",
    sidecar_filename_suffix="mr60_signals",
    sidecar_output_key="mr60_file",
)
