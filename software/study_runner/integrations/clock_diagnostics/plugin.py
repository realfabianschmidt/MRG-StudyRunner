"""Internal plugin wrapper for clock-diagnostics LSL events."""
from __future__ import annotations

from typing import Any

from ..plugin_api import IntegrationContext, IntegrationPlugin


def _initialize(context: IntegrationContext) -> None:
    from . import adapter

    adapter.initialize()


def _status(context: IntegrationContext) -> dict[str, Any]:
    from . import adapter

    return adapter.status()


def _stop(context: IntegrationContext) -> None:
    from . import adapter

    adapter.stop()


def _emit(context: IntegrationContext, options: dict[str, Any]) -> None:
    from . import adapter

    adapter.emit(options)


PLUGIN = IntegrationPlugin(
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

