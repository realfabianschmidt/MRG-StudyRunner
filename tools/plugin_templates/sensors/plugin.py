"""Example sensor plugin -- Study Runner plugin SDK template.

Rename `example_sensor` everywhere (this file's PLUGIN.key/config_key, the
manifest's plugin_key/config_key, adapter.py, driver.py) before shipping;
`tools/plugin_sdk.py new sensors <your_key>` does this substitution for you.

Data (the sensor data contract, the same for every sensor): adapter.py
publishes every real sample through `SensorStreams` -- raw to LSL/XDF, and
from there the core takes the fixed 1 Hz backup and the dashboard's live
view (manifest `backup_projection` and `live_view`). See
docs/developer-guide.md, "Adding A Recording Sensor".

The synthetic LSL source in `tools/synthetic_lsl_source.py` can stand in for
real hardware while developing: it reads this manifest's own declared stream
contract and pushes plausible samples, so the recording pipeline sees real
LSL/XDF data without a physical device attached.

No data between sessions: start() and stop() clear every buffer (the core
also empties the live view), so a new run never sees an earlier
participant's data.

Connection pattern (the same for every sensor): report *facts* in a
`connection` block -- phase, device, signal, setup, streaming -- and the core
decides whether the sensor is ready and which button is the next step in the
dashboard (study_runner/plugin_framework/sensor_connection.py). Give admin
actions a `role` (select, scan, measure_signal, initialize) in the manifest
to have the shared panel draw them. Sensors keep streaming between
participants; `on_session_end` is where per-person state (e.g. a
calibration) is reset -- never stop acquisition there.

Stimulus cards (the same for every sensor): a sensor records continuously
and declares no trial `start`/`stop`; a card only marks its phases with
markers in the recording. Declaring `start` and `stop` would make the plugin
an actuator (see the outputs template).
"""
from __future__ import annotations

from typing import Any

from study_runner.contracts.plugin_api import Plugin, PluginContext


def _config(context: PluginContext) -> dict[str, Any]:
    return context.hardware_config.get("example_sensor", {}) or {}


def _initialize(context: PluginContext) -> None:
    # Called once, in the child subprocess, before any other RPC. Raise here
    # (with a clear message) to fail startup; do not block on slow hardware
    # discovery here if it can be deferred to the first status poll instead.
    from . import adapter

    config = _config(context)
    adapter.initialize(
        enabled=bool(config.get("enabled")),
        lsl_stream_prefix=(config.get("lsl") or {}).get("stream_prefix", "ExampleSensor"),
    )


def _status(context: PluginContext) -> dict[str, Any]:
    from . import adapter

    return adapter.get_status()


def _start(context: PluginContext) -> Any:
    from . import adapter

    return adapter.start()


def _stop(context: PluginContext) -> Any:
    from . import adapter

    return adapter.stop()


def _session_end(context: PluginContext, options: dict[str, Any]) -> None:
    # A participant session closed. Acquisition keeps running for the next
    # person; reset only what belonged to this one (nothing in this example).
    del context, options


PLUGIN = Plugin(
    key="example_sensor",
    label="Example sensor",
    category="biosignal",
    config_key="example_sensor",
    can_start=True,
    can_stop=True,
    has_lsl=True,
    has_recording=True,
    initialize=_initialize,
    get_status=_status,
    start=_start,
    stop=_stop,
    on_session_end=_session_end,
)
