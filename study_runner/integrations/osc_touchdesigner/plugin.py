from __future__ import annotations

from typing import Any

from ..plugin_api import IntegrationContext, IntegrationPlugin


def _config_section(context: IntegrationContext) -> dict[str, Any]:
    section = context.hardware_config.get("osc", {})
    return section if isinstance(section, dict) else {}


def _initialize(context: IntegrationContext) -> None:
    config = _config_section(context)
    if not config.get("enabled"):
        return
    from . import adapter

    adapter.initialize(
        host=config.get("host", "127.0.0.1"),
        port=config.get("port", 9000),
        address_start=config.get("address_start", "/study/start"),
        address_stop=config.get("address_stop", "/study/stop"),
        auto_install=config.get("auto_install", True),
    )


def _status(context: IntegrationContext) -> dict[str, Any]:
    config = _config_section(context)
    enabled = bool(config.get("enabled", False))
    return {
        "status": "enabled" if enabled else "disabled",
        "runtime_enabled": enabled,
        "host": config.get("host", "127.0.0.1"),
        "port": config.get("port", 9000),
        "address_start": config.get("address_start", "/study/start"),
        "address_stop": config.get("address_stop", "/study/stop"),
        "device_label": "OSC / TouchDesigner",
    }


def _trial_start(context: IntegrationContext, options: dict[str, Any]) -> None:
    if not options.get("send_signal", True):
        return
    from . import adapter

    adapter.send_start()


def _trial_stop(context: IntegrationContext, options: dict[str, Any]) -> None:
    if not options.get("send_signal", True):
        return
    from . import adapter

    adapter.send_stop()


PLUGIN = IntegrationPlugin(
    key="osc",
    label="OSC / TouchDesigner",
    category="output",
    config_key="osc",
    initialize=_initialize,
    get_status=_status,
    on_trial_start=_trial_start,
    on_trial_stop=_trial_stop,
)
