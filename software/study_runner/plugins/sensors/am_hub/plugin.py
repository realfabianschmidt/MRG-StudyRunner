from __future__ import annotations

from typing import Any

from study_runner.plugin_framework.adapter_utils import config_section
from study_runner.contracts.plugin_api import PluginContext, Plugin

# The operator's auto-reconnect switch on the dashboard. None until used:
# then the machine setting decides. Kept for the life of the driver.
_auto_reconnect_choice: bool | None = None


def _initialize(context: PluginContext) -> None:
    config = config_section(context, "am_hub")
    if not config:
        return

    from . import adapter

    lsl_config = config.get("lsl") or {}
    adapter.initialize(
        enabled=config.get("enabled", False),
        base_url=context.resolve_platform_value(config.get("base_url")) or "",
        auto_reconnect=(
            _auto_reconnect_choice if _auto_reconnect_choice is not None
            else config.get("auto_reconnect", True)
        ),
        data_timeout_seconds=config.get("data_timeout_seconds", 5),
        lsl_auto_install=lsl_config.get("auto_install", True),
        lsl_stream_prefix=lsl_config.get("stream_prefix", "AmHub"),
        timestamp_correction=bool(config.get("timestamp_correction", False)),
    )


def _status(context: PluginContext) -> dict[str, Any]:
    from . import adapter

    return adapter.get_status()


def _start(context: PluginContext) -> Any:
    from . import adapter

    if not adapter.is_configured():
        _initialize(context)
    return adapter.start()


def _stop(context: PluginContext) -> Any:
    from . import adapter

    return adapter.stop()


def _restart(context: PluginContext) -> Any:
    from . import adapter

    if not adapter.is_configured():
        _initialize(context)
        return adapter.get_status()
    return adapter.restart()


def _run_admin_action(context: PluginContext, action_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    global _auto_reconnect_choice
    if action_key != "auto_reconnect":
        raise ValueError(f"Unknown AM Hub admin action: {action_key}")
    from . import adapter

    _auto_reconnect_choice = bool(payload.get("enabled"))
    adapter.set_auto_reconnect(_auto_reconnect_choice)
    return {
        "auto_reconnect": _auto_reconnect_choice,
        "last_message": (
            "Auto-reconnect on: the AM Hub connection is restored by itself."
            if _auto_reconnect_choice
            else "Auto-reconnect off: a lost AM Hub connection waits for you."
        ),
    }


def _trial_start(context: PluginContext, options: dict[str, Any]) -> None:
    from . import adapter

    # Only for the hub's actuators, and only when a stimulus card selected
    # this plugin. Sensing is continuous and never follows a card.
    adapter.send_stimulus_command("start", options)


def _trial_stop(context: PluginContext, options: dict[str, Any]) -> None:
    from . import adapter

    adapter.send_stimulus_command("stop", options)


def _interval(context: PluginContext, start_epoch: float, end_epoch: float) -> dict[str, Any]:
    from . import adapter

    return adapter.get_interval_summary(start_epoch, end_epoch)


def _export(context: PluginContext, start_epoch: float, end_epoch: float) -> list[dict[str, Any]]:
    from . import adapter

    return adapter.export_interval_samples(start_epoch, end_epoch)


PLUGIN = Plugin(
    key="am_hub",
    label="AM Hub",
    category="biosignal",
    config_key="am_hub",
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
    run_admin_action=_run_admin_action,
    on_trial_start=_trial_start,
    on_trial_stop=_trial_stop,
    get_interval_summary=_interval,
    export_interval_samples=_export,
)
