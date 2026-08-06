from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from study_runner.plugin_framework.adapter_utils import config_section
from study_runner.plugin_framework.plugin_api import IntegrationContext, IntegrationPlugin


DEFAULT_BRAINBIT = {
    "script_path": "study_runner/plugins/brainbit/brainbit_realtime_cli.py",
    "working_dir": "study_runner/plugins/brainbit",
    "log_dir": "study_runner/plugins/brainbit/logs",
}


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _runtime_dir(context: IntegrationContext, configured: Any, default_relative: str, name: str) -> str | None:
    """Resolve a BrainBit working/log folder to somewhere writable.

    In packaged builds the bundled project folder can be read-only (or a temp
    extraction dir), so anything the CLI writes at runtime goes next to the
    saved results instead. Settings files from earlier versions pin the in-repo
    paths explicitly, so those are redirected too rather than trusted blindly.
    """
    from study_runner.backend.services.runtime_config import is_frozen

    writable = str(context.data_dir.parent / "brainbit" / name)
    resolved = context.resolve_platform_value(configured)
    if not resolved:
        return writable if is_frozen() else context.resolve_project_path(default_relative)

    resolved_path = context.resolve_project_path(resolved)
    if is_frozen() and resolved_path and _is_inside_bundle(context, resolved_path):
        return writable
    return resolved_path


def _is_inside_bundle(context: IntegrationContext, candidate: str) -> bool:
    # resolve_project_path() resolves its result, so resolve base_dir too -
    # otherwise the comparison silently fails on relative or drive-less paths.
    try:
        Path(candidate).resolve().relative_to(Path(context.base_dir).resolve())
    except (ValueError, OSError):
        return False
    return True


def _initialize(context: IntegrationContext) -> None:
    config = config_section(context, "brainbit")
    if not config.get("enabled"):
        return

    from . import adapter

    lsl_config = config.get("lsl") or {}
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
        device_address=context.resolve_platform_value(config.get("device_address")),
        serial_number=context.resolve_platform_value(config.get("serial_number")),
        device_name=context.resolve_platform_value(config.get("device_name")),
        resist_seconds=config.get("resist_seconds", 6),
        signal_seconds=config.get("signal_seconds", 0),
        pretty=config.get("pretty", True),
        debug=config.get("debug", False),
        # Native LSL is the mandatory recording path for an enabled sensor.
        # Legacy hardware settings may still contain ``lsl.enabled: false``;
        # API v3 intentionally ignores that obsolete kill switch.
        lsl_enabled=True,
        lsl_auto_install=lsl_config.get("auto_install", True),
        lsl_stream_prefix=lsl_config.get("stream_prefix", "BrainBit"),
        quiet_output=config.get("quiet_output", True),
        monitor_refresh_ms=config.get("monitor_refresh_ms", 1000),
        disconnect_timeout_ms=config.get("disconnect_timeout_ms", 20000),
        log_dir=_runtime_dir(context, config.get("log_dir"), DEFAULT_BRAINBIT["log_dir"], "logs"),
    )


def _status(context: IntegrationContext) -> dict[str, Any]:
    config = config_section(context, "brainbit")
    from . import adapter

    adapter_status = adapter.get_status()
    log_dir = Path(
        _runtime_dir(context, config.get("log_dir"), DEFAULT_BRAINBIT["log_dir"], "logs")
        or context.base_dir / DEFAULT_BRAINBIT["log_dir"]
    )
    state_path = log_dir / "brainbit_state.json"
    state_payload = _read_json_file(state_path)
    latest = adapter_status.get("latest") or state_payload

    status_value = adapter_status.get("status")
    if not config.get("enabled"):
        status_value = "disabled"
    elif not status_value or status_value == "not_configured":
        status_value = state_payload.get("status", "waiting") if state_payload else "waiting"

    return {
        **adapter_status,
        "status": status_value,
        "device_label": "BrainBit",
        "state_file": str(state_path),
        "latest": latest,
        "lsl_enabled": bool(config.get("enabled", False)),
        "touchdesigner_target": f"{config.get('osc_host', '127.0.0.1')}:{config.get('osc_port', 8000)}",
        "scan_timeout_seconds": int(config.get("scan_seconds", 5)),
        "scan_mode": "one_shot_on_start",
        "last_scan_started_at": adapter_status.get("last_scan_started_at") or latest.get("last_scan_started_at"),
        "last_scan_finished_at": adapter_status.get("last_scan_finished_at") or latest.get("last_scan_finished_at"),
        "next_retry_at": None,
    }


def _start(context: IntegrationContext) -> Any:
    from . import adapter

    if not adapter.is_configured() and config_section(context, "brainbit").get("enabled"):
        _initialize(context)
        return adapter.get_status()
    adapter.start()
    return adapter.get_status()


def _stop(context: IntegrationContext) -> Any:
    from . import adapter

    adapter.stop()
    return adapter.get_status()


def _restart(context: IntegrationContext) -> Any:
    from . import adapter

    if not adapter.is_configured() and config_section(context, "brainbit").get("enabled"):
        _initialize(context)
    else:
        adapter.restart()
    return adapter.get_status()


def _trial_start(context: IntegrationContext, options: dict[str, Any]) -> None:
    from . import adapter

    plugin_actions = options.get("plugin_actions")
    plugin_actions = plugin_actions if isinstance(plugin_actions, dict) else {}
    actions = plugin_actions.get("brainbit")
    actions = actions if isinstance(actions, dict) else {}
    adapter.set_routing(
        forward_to_lsl=None,
        forward_to_touchdesigner=bool(
            actions.get(
                "to_touchdesigner",
                # One-release compatibility for already-open legacy tablets.
                options.get("brainbit_to_touchdesigner", False),
            )
        ),
    )


def _trial_stop(context: IntegrationContext, options: dict[str, Any]) -> None:
    from . import adapter

    adapter.set_routing(forward_to_lsl=None, forward_to_touchdesigner=False)


def _interval(context: IntegrationContext, start_epoch: float, end_epoch: float) -> dict[str, Any]:
    from . import adapter

    return adapter.get_interval_summary(start_epoch, end_epoch)


def _export(context: IntegrationContext, start_epoch: float, end_epoch: float) -> list[dict[str, Any]]:
    from . import adapter

    return adapter.export_interval_samples(start_epoch, end_epoch)


def _run_admin_action(
    context: IntegrationContext,
    action_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if action_key != "select_device":
        raise ValueError(f"Unknown BrainBit admin action: {action_key}")
    if context.runtime_locked:
        return {
            "study_controlled": True,
            "last_message": "BrainBit band selection is locked while a study is running.",
        }
    if context.persist_hardware_config is None:
        raise RuntimeError("BrainBit device selection requires a machine-settings context.")

    serial_number = str(payload.get("serial_number") or "").strip()
    device_address = str(payload.get("address") or "").strip()
    device_name = str(payload.get("name") or "").strip()
    device_index = payload.get("index")
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
    restart_result = None
    restart_error = ""
    try:
        restart_result = _restart(replace(context, hardware_config=hardware_config))
    except Exception as error:  # The persisted selection remains recoverable.
        restart_error = str(error)
    return {
        "last_message": (
            "BrainBit band saved and restart requested"
            if not restart_error
            else "BrainBit band saved; restart needs attention"
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


PLUGIN = IntegrationPlugin(
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
    get_interval_summary=_interval,
    export_interval_samples=_export,
    sidecar_sensor="brainbit",
    sidecar_filename_suffix="brainbit_signals",
    sidecar_output_key="brainbit_file",
)
