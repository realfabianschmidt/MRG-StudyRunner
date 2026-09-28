"""Example sensor plugin -- Study Runner plugin SDK template.

Rename `example_sensor` everywhere (this file's PLUGIN.key/config_key and
the manifest's plugin_key/config_key) before shipping; `tools/plugin_sdk.py
new sensors <your_key>` does this substitution for you.

The synthetic LSL source in `tools/synthetic_lsl_source.py` can stand in for
real hardware while developing: it reads this manifest's own declared stream
contract and pushes plausible samples, so the recording pipeline sees real
LSL/XDF data without a physical device attached.

`initialize`/`get_status` are the only handlers `study_sensor` +
`recording_source` require; everything else (start/stop, admin actions,
credentials) is opt-in per declared capability -- see
docs/developer-guide.md, "Adding A Recording Sensor".

No data between sessions: if you add `start`, clear any sample buffers or
cached readings there, so a new run never sees an earlier participant's data.

Connection pattern (the same for every sensor): report *facts* in a
`connection` block -- phase, device, signal, setup, streaming -- and the core
decides whether the sensor is ready and which button is the next step in the
dashboard (study_runner/plugin_framework/sensor_connection.py). Give admin
actions a `role` (select, scan, measure_signal, initialize) in the manifest
to have the shared panel draw them. Sensors keep streaming between
participants; `on_session_end` is where per-person state (e.g. a
calibration) is reset -- never stop acquisition there.
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
        # Whether acquisition runs right now (drives the dashboard switch).
        "running": configured,
        # Facts only; the core adds `ready` and `next_step`.
        "connection": {
            "phase": "connected" if configured else "off",
            "device": {"id": "example", "label": "Example device"} if configured else None,
            "signal": {"state": "good" if configured else "unknown"},
            "setup": {"state": "not_needed"},
            "streaming": configured,
        },
    }


def _session_end(context: PluginContext, options: dict[str, Any]) -> None:
    # A participant session closed. Acquisition keeps running for the next
    # person; reset only what belonged to this one (nothing in this example).
    del context, options


PLUGIN = Plugin(
    key="example_sensor",
    label="Example sensor",
    category="biosignal",
    config_key="example_sensor",
    has_lsl=True,
    has_recording=True,
    initialize=_initialize,
    get_status=_status,
    on_session_end=_session_end,
)
