"""Example output plugin -- Study Runner plugin SDK template.

Rename `example_output` everywhere (this file's PLUGIN.key/config_key and
the manifest's plugin_key/config_key) before shipping;
`tools/plugin_sdk.py new outputs <your_key>` does this substitution for
you. `initialize`/`get_status` are the whole mandatory contract for the
"output" category -- see `study_runner/plugins/outputs/osc_touchdesigner/`
for a complete, real implementation once you outgrow this template.

Actuators (the same for every plugin): declaring trial `start` and `stop`
in the manifest's `runtime.trial_events` makes this plugin an actuator.
Every stimulus card then lists it under "Control actuators" automatically,
and only the cards that select it call `on_trial_start` / `on_trial_stop`.
Keep no per-card on/off option of your own: the card's selection decides.
Delete both hooks and the two trial events if the plugin drives nothing.
"""
from __future__ import annotations

import os
from typing import Any

from study_runner.contracts.plugin_api import Plugin, PluginContext


def _initialize(context: PluginContext) -> None:
    # Called once, in the child subprocess, before any other RPC. Raise here
    # (with a clear message) to fail startup; do not block on slow or
    # network-dependent setup here if it can be deferred to the first
    # status poll instead.
    del context  # unused in this minimal example


def _status(context: PluginContext) -> dict[str, Any]:
    return {
        "status": "ok",
        "pid": os.getpid(),
        "data_dir": str(context.data_dir),
    }


def _trial_start(context: PluginContext, options: dict[str, Any]) -> None:
    # A stimulus card that selected this plugin begins its active phase.
    # `options` carries the card's identity (stimulus_id, question_index,
    # event_id, source_epoch_ms); start the actuator here.
    del context, options  # unused in this minimal example


def _trial_stop(context: PluginContext, options: dict[str, Any]) -> None:
    # The same card ends: when its time is up, or - with overtime - when the
    # participant leaves it. Stop the actuator here.
    del context, options  # unused in this minimal example


PLUGIN = Plugin(
    key="example_output",
    label="Example output",
    category="output",
    config_key="example_output",
    initialize=_initialize,
    get_status=_status,
    on_trial_start=_trial_start,
    on_trial_stop=_trial_stop,
)
