"""Example sensor extension -- Study Runner extension SDK template.

Rename `example_sensor` everywhere (this file's PLUGIN.key/config_key and
the manifest's plugin_key/config_key) before shipping; `tools/extension_sdk.py
new sensors <your_key>` does this substitution for you.

The synthetic LSL source in `tools/synthetic_lsl_source.py` can stand in for
real hardware while developing: it reads this manifest's own declared stream
contract and pushes plausible samples, so the recording pipeline sees real
LSL/XDF data without a physical device attached.

`initialize`/`get_status` are the only handlers `study_sensor` +
`recording_source` require; everything else (start/stop, admin actions,
credentials) is opt-in per declared capability -- see
docs/developer-guide.md, "Adding A Recording Sensor".
"""
from __future__ import annotations

from typing import Any

from study_runner.contracts.plugin_api import Plugin, PluginContext


def _initialize(context: PluginContext) -> None:
    # Called once, in the child subprocess, before any other RPC. Raise here
    # (with a clear message) to fail startup; do not block on slow hardware
    # discovery here if it can be deferred to the first status poll instead.
    del context  # unused in this minimal example


def _status(context: PluginContext) -> dict[str, Any]:
    configured = bool(context.hardware_config.get("example_sensor", {}).get("enabled"))
    return {
        "status": "ready" if configured else "disabled",
        "lsl_enabled": configured,
    }


PLUGIN = Plugin(
    key="example_sensor",
    label="Example sensor",
    category="biosignal",
    config_key="example_sensor",
    has_lsl=True,
    has_recording=True,
    initialize=_initialize,
    get_status=_status,
)
