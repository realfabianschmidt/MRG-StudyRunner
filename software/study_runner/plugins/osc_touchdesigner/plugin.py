from __future__ import annotations

from typing import Any

from study_runner.plugin_framework.adapter_utils import config_section
from study_runner.plugin_framework.plugin_api import PluginContext, Plugin


def _initialize(context: PluginContext) -> None:
    config = config_section(context, "osc")
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


def _status(context: PluginContext) -> dict[str, Any]:
    config = config_section(context, "osc")
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


def _trial_start(context: PluginContext, options: dict[str, Any]) -> None:
    if not _forward_marker(options):
        return
    from . import adapter

    adapter.send_start()


def _trial_stop(context: PluginContext, options: dict[str, Any]) -> None:
    if not _forward_marker(options):
        return
    from . import adapter

    adapter.send_stop()


def _forward_marker(options: dict[str, Any]) -> bool:
    plugin_actions = options.get("plugin_actions")
    plugin_actions = plugin_actions if isinstance(plugin_actions, dict) else {}
    actions = plugin_actions.get("osc")
    if isinstance(actions, dict) and "forward_marker" in actions:
        return bool(actions["forward_marker"])
    # One-release compatibility for a request from an already-open old UI.
    return bool(options.get("send_signal", True))


PLUGIN = Plugin(
    key="osc",
    label="OSC / TouchDesigner",
    category="output",
    config_key="osc",
    initialize=_initialize,
    get_status=_status,
    on_trial_start=_trial_start,
    on_trial_stop=_trial_stop,
)
