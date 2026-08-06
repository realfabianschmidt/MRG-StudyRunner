"""Internal plugin wrapper for clock-diagnostics LSL events."""
from __future__ import annotations

from typing import Any

from study_runner.plugin_framework.plugin_api import PluginContext, Plugin


def _initialize(context: PluginContext) -> None:
    from . import adapter

    adapter.initialize()


def _status(context: PluginContext) -> dict[str, Any]:
    from . import adapter

    return adapter.status()


def _stop(context: PluginContext) -> None:
    from . import adapter

    adapter.stop()


def _emit(context: PluginContext, options: dict[str, Any]) -> None:
    from . import adapter

    adapter.emit(options)


PLUGIN = Plugin(
    key="clock_diagnostics",
    label="Clock diagnostics",
    category="sync",
    config_key="clock_diagnostics",
    can_toggle=False,
    has_lsl=True,
    has_recording=True,
    initialize=_initialize,
    get_status=_status,
    stop=_stop,
    on_trial_start=_emit,
    on_trial_stop=_emit,
    on_trial_marker=_emit,
)

