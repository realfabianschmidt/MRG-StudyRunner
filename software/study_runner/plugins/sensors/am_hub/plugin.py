from __future__ import annotations

from typing import Any

from study_runner.plugin_framework.adapter_utils import config_section
from study_runner.contracts.plugin_api import PluginContext, Plugin


def _initialize(context: PluginContext) -> None:
    config = config_section(context, "am_hub")
    if not config:
        return

    from . import adapter

    lsl_config = config.get("lsl") or {}
    adapter.initialize(
        enabled=config.get("enabled", False),
        base_url=context.resolve_platform_value(config.get("base_url")) or "",
        auto_reconnect=config.get("auto_reconnect", True),
        reconnect_delay_seconds=config.get("reconnect_delay_seconds", 3),
        data_timeout_seconds=config.get("data_timeout_seconds", 5),
        lsl_enabled=bool(config.get("enabled", False)),
        lsl_auto_install=lsl_config.get("auto_install", True),
        lsl_stream_prefix=lsl_config.get("stream_prefix", "AmHub"),
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


def _trial_start(context: PluginContext, options: dict[str, Any]) -> None:
    from . import adapter

    # The registry invokes this hook only for an enabled plugin. Canonical LSL
    # acquisition/recording is mandatory for a recording_source and therefore
    # cannot be disabled by a card-level boolean.
    adapter.set_recording(True)


def _trial_stop(context: PluginContext, options: dict[str, Any]) -> None:
    from . import adapter

    adapter.set_recording(False)


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
    on_trial_start=_trial_start,
    on_trial_stop=_trial_stop,
    get_interval_summary=_interval,
    export_interval_samples=_export,
    sidecar_sensor="am_hub",
    sidecar_filename_suffix="am_hub_signals",
    sidecar_output_key="am_hub_file",
)
