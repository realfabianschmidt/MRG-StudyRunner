from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from study_runner.plugin_framework.adapter_utils import config_section, runtime_path_setting
from study_runner.plugin_framework.sensor_connection import auto_reconnect_active
from study_runner.contracts.plugin_api import PluginContext, Plugin


DEFAULT_BRAINBIT = {
    "script_path": "study_runner/plugins/sensors/brainbit/brainbit_realtime_cli.py",
    "working_dir": "study_runner/plugins/sensors/brainbit",
    "log_dir": "study_runner/plugins/sensors/brainbit/logs",
}
# The operator connects the band by hand (Search, or a band from the list).
_CONNECT_ROLES = ("scan", "select")
_remembered_connection = None
# The operator's auto-reconnect switch. None until they use it: then the
# machine setting decides. Kept for the life of the driver, across restarts.
_auto_reconnect_choice: bool | None = None


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _runtime_dir(context: PluginContext, configured: Any, default_relative: str, name: str) -> str | None:
    """Resolve a BrainBit working/log folder to somewhere writable.

    Logs (and the state file next to them) always go to the plugin's runtime
    folder next to the results -- see ``runtime_path_setting``. The rest of
    this docstring is about the working folder.

    In packaged builds the bundled project folder can be read-only (or a temp
    extraction dir), so anything the CLI writes at runtime goes next to the
    saved results instead. Settings files from earlier versions pin the in-repo
    paths explicitly, so those are redirected too rather than trusted blindly.
    """
    if name == "logs":
        # Shared rule: logs and state never go into the program files.
        return str(runtime_path_setting(context, configured, "brainbit", "logs"))
    from study_runner.shared.runtime_mode import is_frozen

    writable = str(context.data_dir.parent / "brainbit" / name)
    resolved = context.resolve_platform_value(configured)
    if not resolved:
        return writable if is_frozen() else context.resolve_project_path(default_relative)

    resolved_path = context.resolve_project_path(resolved)
    if is_frozen() and resolved_path and _is_inside_bundle(context, resolved_path):
        return writable
    return resolved_path


def _is_inside_bundle(context: PluginContext, candidate: str) -> bool:
    # resolve_project_path() resolves its result, so resolve base_dir too -
    # otherwise the comparison silently fails on relative or drive-less paths.
    try:
        Path(candidate).resolve().relative_to(Path(context.base_dir).resolve())
    except (ValueError, OSError):
        return False
    return True


def _auto_reconnect_enabled(config: dict[str, Any]) -> bool:
    if _auto_reconnect_choice is not None:
        return _auto_reconnect_choice
    return bool(config.get("auto_reconnect", True))


def _initialize(context: PluginContext, *, connect: bool = False) -> None:
    """Configure the adapter. It connects only when the operator asked (``connect``).

    Switched on, the plugin is ready to connect and offers the band used last
    time; nothing searches by itself.
    """
    config = config_section(context, "brainbit")
    if not config.get("enabled"):
        return

    from . import adapter

    lsl_config = config.get("lsl") or {}
    last_connected = config.get("last_connected_device") or {}
    last_connected = last_connected if isinstance(last_connected, dict) else {}
    # A chosen band (serial/address) wins; otherwise the band connected last.
    has_target = bool(config.get("serial_number") or config.get("device_address"))
    adapter.initialize(
        script_path=context.resolve_project_path(
            context.resolve_platform_value(config.get("script_path")) or DEFAULT_BRAINBIT["script_path"]
        ),
        working_dir=_runtime_dir(context, config.get("working_dir"), DEFAULT_BRAINBIT["working_dir"], "runtime"),
        python_executable=context.resolve_project_path(context.resolve_platform_value(config.get("python_executable"))),
        osc_host=config.get("osc_host", "127.0.0.1"),
        osc_port=config.get("osc_port", 8000),
        scan_seconds=config.get("scan_seconds", 5),
        device_index=config.get("device_index", 0),
        device_address=context.resolve_platform_value(config.get("device_address") if has_target else last_connected.get("address")),
        serial_number=context.resolve_platform_value(config.get("serial_number") if has_target else last_connected.get("serial_number")),
        device_name=context.resolve_platform_value(config.get("device_name")),
        require_selection=bool(config.get("require_selection", False)),
        resist_seconds=config.get("resist_seconds", 6),
        signal_seconds=config.get("signal_seconds", 0),
        pretty=config.get("pretty", True),
        debug=config.get("debug", False),
        # Native LSL is the mandatory recording path for an enabled sensor.
        # Legacy hardware settings may still contain ``lsl.enabled: false``;
        # the plugin API intentionally ignores that obsolete kill switch.
        lsl_enabled=True,
        lsl_auto_install=lsl_config.get("auto_install", True),
        lsl_stream_prefix=lsl_config.get("stream_prefix", "BrainBit"),
        quiet_output=config.get("quiet_output", True),
        monitor_refresh_ms=config.get("monitor_refresh_ms", 1000),
        disconnect_timeout_ms=config.get("disconnect_timeout_ms", 45000),
        settle_seconds=config.get("settle_seconds", 2.0),
        auto_restart_max_attempts=config.get("auto_restart_max_attempts", 3),
        log_dir=_runtime_dir(context, config.get("log_dir"), DEFAULT_BRAINBIT["log_dir"], "logs"),
        log_max_bytes=config.get("log_max_bytes", 10 * 1024 * 1024),
        log_backup_count=config.get("log_backup_count", 3),
        start_process=connect,
    )
    if not connect:
        # The host's own initialize (start, study preflight): a band that is
        # already connecting gets the chance to publish its streams.
        adapter.wait_for_stream_contract()


def _apply_auto_reconnect(context: PluginContext, config: dict[str, Any]) -> bool:
    """Tell the adapter whether to restore a lost connection; returns the switch.

    Every status poll carries whether the study runs, so this follows Play,
    Stop and the operator's switch within one poll. The rule itself is the
    core's, the same one the dashboard shows.
    """
    from . import adapter

    enabled = _auto_reconnect_enabled(config)
    if adapter.is_configured():
        adapter.set_auto_reconnect(
            auto_reconnect_active(
                enabled=enabled,
                had_connection=adapter.had_connection(),
                study_running=bool(getattr(context, "study_running", False)),
                roles=_CONNECT_ROLES,
            )
        )
    return enabled


def _status(context: PluginContext) -> dict[str, Any]:
    global _remembered_connection
    config = config_section(context, "brainbit")
    from . import adapter

    enabled = _apply_auto_reconnect(context, config)
    adapter_status = adapter.get_status()
    log_dir = Path(
        _runtime_dir(context, config.get("log_dir"), DEFAULT_BRAINBIT["log_dir"], "logs")
    )
    state_path = log_dir / "brainbit_state.json"
    state_payload = _read_json_file(state_path)
    latest = adapter_status.get("latest") or {}
    connection_id = adapter_status.get("connection_id")
    connected = (adapter_status.get("diagnostic_state") or {}).get("CONNECTED") or {}
    if (connection_id and connection_id != _remembered_connection and connected
            and context.persist_hardware_config):
        identity = {"serial_number": connected.get("serial") or "",
                    "address": connected.get("address") or "", "name": connected.get("name") or ""}
        if identity["serial_number"] or identity["address"]:
            updated = json.loads(json.dumps(context.hardware_config))
            updated.setdefault("brainbit", {})["last_connected_device"] = identity
            context.persist_hardware_config(updated)
            _remembered_connection = connection_id

    connection = adapter_status.get("connection")
    if isinstance(connection, dict):
        connection["auto_reconnect"] = {**(connection.get("auto_reconnect") or {}), "enabled": enabled}

    status_value = adapter_status.get("status")
    if not config.get("enabled"):
        status_value = "disabled"
    elif not status_value or status_value == "not_configured":
        status_value = "waiting"

    health = adapter_status.get("health") if isinstance(adapter_status.get("health"), dict) else {}
    eeg_batch = latest.get("eeg_batch") if isinstance(latest.get("eeg_batch"), dict) else {}
    resist = latest.get("resist") if isinstance(latest.get("resist"), dict) else {}
    battery = latest.get("battery") if isinstance(latest.get("battery"), dict) else {}
    selected = adapter_status.get("selected_device") or latest.get("selected_device") or latest.get("device")
    actual_streams = adapter_status.get("actual_streams") or latest.get("actual_streams") or []
    eeg_stream = next(
        (stream for stream in actual_streams if isinstance(stream, dict) and stream.get("key") == "eeg"),
        {},
    )
    resistance_channels = {
        key: value
        for key, value in resist.items()
        if key not in {
            "ts", "pack", "marker", "units", "packet_shape", "open_channels", "referents_ohm"
        }
    }

    return {
        **adapter_status,
        "status": status_value,
        "device_label": "BrainBit",
        "connected_model": selected,
        "supported_channels": adapter_status.get("supported_channels") or latest.get("supported_channels") or [],
        "resistances_ohm": resistance_channels,
        "battery_percent": battery.get("percent"),
        "raw_status": health.get("raw_eeg"),
        "derived_status": health.get("derived_metrics"),
        "sample_rate_hz": eeg_stream.get("nominal_rate_hz"),
        "batch_size": eeg_batch.get("sample_count"),
        "dropped_samples": eeg_batch.get("packet_gap_frames_total", 0),
        "last_gap_samples": eeg_batch.get("packet_gap_frames", 0),
        "state_file": str(state_path),
        "latest": latest,
        "historical_state": state_payload if not latest else {},
        "runtime_locked": context.runtime_locked,
        "lsl_enabled": bool(config.get("enabled", False)),
        "touchdesigner_target": f"{config.get('osc_host', '127.0.0.1')}:{config.get('osc_port', 8000)}",
        "scan_timeout_seconds": int(config.get("scan_seconds", 5)),
        "scan_mode": adapter_status.get("scan_mode", "on_request"),
        "last_scan_started_at": adapter_status.get("last_scan_started_at") or latest.get("last_scan_started_at"),
        "last_scan_finished_at": adapter_status.get("last_scan_finished_at") or latest.get("last_scan_finished_at"),
        "next_retry_at": adapter_status.get("next_retry_at"),
    }


def _handle_console_line(context: PluginContext, line: str) -> Any:
    """Small, safe BrainBit diagnostic console; never exposes an OS shell."""
    command = line.strip().lower()
    status = _status(context)
    latest = status.get("latest") if isinstance(status.get("latest"), dict) else {}
    if command == "help":
        return (
            "BrainBit commands: help, status, health, channels, raw, derived, errors, "
            "start, stop, restart. The live driver output above is the bounded terminal relay."
        )
    if command == "status":
        return status
    if command == "health":
        return {
            "status": status.get("status"),
            "health": status.get("health"),
            "last_activity_at": status.get("last_activity_at"),
            "seconds_since_last_eeg": status.get("seconds_since_last_eeg"),
            "seconds_since_last_raw_lsl": status.get("seconds_since_last_raw_lsl"),
        }
    if command == "channels":
        return {
            "selected_device": status.get("selected_device"),
            "channel_map": latest.get("channel_map"),
            "actual_streams": status.get("actual_streams") or latest.get("actual_streams"),
        }
    if command == "raw":
        return {
            "eeg": latest.get("eeg"),
            "eeg_batch": latest.get("eeg_batch"),
            "resistance_ohm": latest.get("resist"),
            "quality_diagnostic": latest.get("quality"),
        }
    if command == "derived":
        return {
            "derived_enabled": latest.get("derived_enabled"),
            "calibration": latest.get("calibration"),
            "artifact": latest.get("artifact"),
            "bands": latest.get("bands"),
            "mental": latest.get("mental"),
        }
    if command == "errors":
        return {
            "callback_error": latest.get("callback_error"),
            "stream_error": latest.get("stream_error"),
            "lsl_error": latest.get("lsl_error"),
            "log_error": latest.get("log_error"),
            "data_warning": latest.get("data_warning"),
            "data_warning_count": latest.get("data_warning_count", 0),
            "raw_log_path": status.get("raw_log_path"),
        }
    if command == "start":
        return _start(context)
    if command == "stop":
        return _stop(context)
    if command == "restart":
        return _restart(context)
    return f"Unknown BrainBit command: {line!r}. Type 'help'."


def _start(context: PluginContext) -> Any:
    from . import adapter

    # Pressing Start means "try again", so the automatic-restart budget starts
    # over. Without this, a plugin that had used up its retries earlier stayed
    # unrecoverable until the whole application was restarted.
    adapter.reset_retry_budget()
    config = config_section(context, "brainbit")
    if not adapter.is_configured() and config.get("enabled"):
        _initialize(context)
    elif not adapter.get_status().get("runtime_enabled"):
        # Switching on never searches or connects by itself: ready to connect.
        adapter.await_scan()
    return adapter.get_status()


def _stop(context: PluginContext) -> Any:
    from . import adapter

    adapter.stop()
    # Switched off on purpose: switching on again must not reconnect by itself.
    adapter.forget_connection(keep_band=True)
    return adapter.get_status()


def _restart(context: PluginContext, *, connect: bool | None = None) -> Any:
    """Stop and configure again from the current settings.

    ``connect`` None (the Restart button): reconnect only if a band was
    connected, otherwise stay ready to connect. The call returns once the
    CLI runs; the dashboard follows the connection through the status.
    """
    from . import adapter

    config = config_section(context, "brainbit")
    if connect is None:
        connect = adapter.had_connection()
    adapter.reset_retry_budget()
    adapter.stop()
    if not config.get("enabled"):
        return adapter.get_status()
    if connect:
        # The Bluetooth stack needs a moment to let go of the band.
        adapter.settle_after_stop()
    # Re-read every machine setting from the refreshed v5 context. A plain
    # adapter.restart() would retain the old serial/path/timeout configuration.
    _initialize(context, connect=connect)
    return adapter.get_status()


def _session_end(context: PluginContext, options: dict[str, Any]) -> None:
    """A participant finished: keep streaming, but require contact and calibration anew."""
    from . import adapter

    adapter.session_end()


def _trial_start(context: PluginContext, options: dict[str, Any]) -> None:
    from . import adapter

    plugin_actions = options.get("plugin_actions")
    plugin_actions = plugin_actions if isinstance(plugin_actions, dict) else {}
    actions = plugin_actions.get("brainbit")
    actions = actions if isinstance(actions, dict) else {}
    adapter.set_routing(
        forward_to_lsl=None,
        forward_to_touchdesigner=bool(actions.get("to_touchdesigner", False)),
    )


def _trial_stop(context: PluginContext, options: dict[str, Any]) -> None:
    from . import adapter

    adapter.set_routing(forward_to_lsl=None, forward_to_touchdesigner=False)


def _interval(context: PluginContext, start_epoch: float, end_epoch: float) -> dict[str, Any]:
    from . import adapter

    return adapter.get_interval_summary(start_epoch, end_epoch)


def _export(context: PluginContext, start_epoch: float, end_epoch: float) -> list[dict[str, Any]]:
    from . import adapter

    return adapter.export_interval_samples(start_epoch, end_epoch)


def _run_admin_action(
    context: PluginContext,
    action_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    global _auto_reconnect_choice
    if action_key not in {"select_device", "scan_devices", "check_contact", "calibrate", "auto_reconnect"}:
        raise ValueError(f"Unknown BrainBit admin action: {action_key}")
    from . import adapter

    if action_key == "auto_reconnect":
        # Changes no data, only how a lost connection is handled, so the
        # operator may switch it during a recording too.
        _auto_reconnect_choice = bool(payload.get("enabled"))
        _apply_auto_reconnect(context, config_section(context, "brainbit"))
        return {
            "auto_reconnect": _auto_reconnect_choice,
            "last_message": (
                "Auto-reconnect on: a lost connection is restored while the study runs."
                if _auto_reconnect_choice
                else "Auto-reconnect off: a lost connection waits for you."
            ),
        }
    if context.runtime_locked:
        return {
            "study_controlled": True,
            "last_message": "BrainBit is locked while a participant session is recording.",
        }

    if action_key == "calibrate":
        if not adapter.calibrate():
            raise ValueError("BrainBit must be connected and streaming EEG before it can be initialized.")
        return {"last_message": "Initializing BrainBit: the participant sits still with eyes open (about 6 s)."}
    if action_key == "check_contact":
        # Measured between EEG windows on the same connection.
        if adapter.measure_contact():
            return {"last_message": "Measuring electrode contact (about 6 s)."}
        raise ValueError("Connect the BrainBit headband before measuring the contact.")
    if context.persist_hardware_config is None:
        raise RuntimeError("BrainBit device selection requires a machine-settings context.")
    if action_key == "scan_devices":
        # A temporary discovery restart must not erase the saved preferred band.
        config = json.loads(json.dumps(context.hardware_config))
        section = config.setdefault("brainbit", {})
        for key in ("serial_number", "device_address", "device_name", "last_connected_device"):
            section.pop(key, None)
        # Exactly one band found: it is taken automatically and remembered.
        # Several: the operator chooses one from the list.
        section["require_selection"] = False
        adapter.forget_connection()
        _restart(replace(context, hardware_config=config), connect=True)
        return {"last_message": f"Searching for BrainBit headbands ({int(section.get('scan_seconds', 5) or 5)} s)."}

    serial_number = str(payload.get("serial_number") or "").strip()
    device_address = str(payload.get("address") or "").strip()
    device_name = str(payload.get("name") or "").strip()
    device_index = payload.get("index")
    # A band is only reliably re-findable by serial or address. A position
    # in the scan list is not: the next scan can order the bands differently, so
    # saving a bare index would quietly point at whichever band answers first
    # next time. Refusing is better than saving a selection that drifts.
    if not (serial_number or device_address):
        return {
            "last_message": (
                "This band reported no serial number or address, so it "
                "cannot be saved. Switch the band off and on and scan again."
            ),
            "saved": False,
        }
    hardware_config = json.loads(json.dumps(context.hardware_config))
    brainbit_config = hardware_config.setdefault("brainbit", {})
    if not isinstance(brainbit_config, dict):
        brainbit_config = {}
        hardware_config["brainbit"] = brainbit_config
    brainbit_config.update(
        {
            "serial_number": serial_number,
            "device_address": device_address,
            "device_name": device_name,
        }
    )
    if device_index is not None:
        brainbit_config["device_index"] = device_index

    context.persist_hardware_config(hardware_config)
    adapter.forget_connection()
    restart_result = None
    restart_error = ""
    try:
        restart_result = _restart(replace(context, hardware_config=hardware_config), connect=True)
    except Exception as error:  # The persisted selection remains recoverable.
        restart_error = str(error)
    label = " ".join(part for part in (device_name or "BrainBit", serial_number) if part)
    return {
        "last_message": (
            f"Connecting to {label} …"
            if not restart_error
            else "BrainBit band saved; connecting needs attention"
        ),
        "target_device": {
            "serial_number": serial_number,
            "address": device_address,
            "name": device_name,
            "index": device_index,
        },
        "restart": restart_result,
        "restart_error": restart_error,
    }


PLUGIN = Plugin(
    key="brainbit",
    label="BrainBit",
    category="biosignal",
    config_key="brainbit",
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
    on_session_end=_session_end,
    get_interval_summary=_interval,
    export_interval_samples=_export,
    handle_console_line=_handle_console_line,
    sidecar_sensor="brainbit",
    sidecar_filename_suffix="brainbit_signals",
    sidecar_output_key="brainbit_file",
)
