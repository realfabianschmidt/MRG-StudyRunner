"""Example output extension -- Study Runner plugin SDK template.

Rename `example_output` everywhere (this file's PLUGIN.key/config_key and
the manifest's plugin_key/config_key) before shipping;
`tools/plugin_sdk.py new outputs <your_key>` does this substitution for
you. `initialize`/`get_status` are the whole mandatory contract for the
"output" category -- see `study_runner/plugins/outputs/osc_touchdesigner/`
for a complete, real implementation once you outgrow this template.
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


PLUGIN = Plugin(
    key="example_output",
    label="Example output",
    category="output",
    config_key="example_output",
    initialize=_initialize,
    get_status=_status,
)
