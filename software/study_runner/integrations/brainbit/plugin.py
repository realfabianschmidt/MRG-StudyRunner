from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..plugin_api import IntegrationContext, IntegrationPlugin


DEFAULT_BRAINBIT = {
    "script_path": "study_runner/integrations/brainbit/brainbit_realtime_cli_OSC_15.py",
    "working_dir": "study_runner/integrations/brainbit",
    "log_dir": "study_runner/integrations/brainbit/logs",
}


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _config_section(context: IntegrationContext) -> dict[str, Any]:
    section = context.hardware_config.get("brainbit", {})
    return section if isinstance(section, dict) else {}


def _initialize(context: IntegrationContext) -> None:
    config = _config_section(context)
    if not config.get("enabled"):
        return

    from . import adapter

    lsl_config = config.get("lsl") or {}
    adapter.initialize(
        script_path=context.resolve_project_path(
            context.resolve_platform_value(config.get("script_path")) or DEFAULT_BRAINBIT["script_path"]
        ),
        working_dir=context.resolve_project_path(
            context.resolve_platform_value(config.get("working_dir")) or DEFAULT_BRAINBIT["working_dir"]
        ),
        python_executable=context.resolve_project_path(context.resolve_platform_value(config.get("python_executable"))),
        osc_host=config.get("osc_host", "127.0.0.1"),
        osc_port=config.get("osc_port", 8000),
        scan_seconds=config.get("scan_seconds", 5),
        resist_seconds=config.get("resist_seconds", 6),
        signal_seconds=config.get("signal_seconds", 0),
        pretty=config.get("pretty", True),
        debug=config.get("debug", False),
        lsl_enabled=lsl_config.get("enabled", False),
        lsl_auto_install=lsl_config.get("auto_install", True),
        lsl_stream_prefix=lsl_config.get("stream_prefix", "BrainBit"),
        quiet_output=config.get("quiet_output", True),
        monitor_refresh_ms=config.get("monitor_refresh_ms", 1000),
        disconnect_timeout_ms=config.get("disconnect_timeout_ms", 5000),
        log_dir=context.resolve_project_path(
            context.resolve_platform_value(config.get("log_dir")) or DEFAULT_BRAINBIT["log_dir"]
        ),
    )


def _status(context: IntegrationContext) -> dict[str, Any]:
    config = _config_section(context)
    from . import adapter

    adapter_status = adapter.get_status()
    log_dir_value = context.resolve_platform_value(config.get("log_dir")) or DEFAULT_BRAINBIT["log_dir"]
    log_dir = Path(context.resolve_project_path(log_dir_value) or context.base_dir / DEFAULT_BRAINBIT["log_dir"])
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
        "lsl_enabled": bool((config.get("lsl") or {}).get("enabled", False)),
        "touchdesigner_target": f"{config.get('osc_host', '127.0.0.1')}:{config.get('osc_port', 8000)}",
        "scan_timeout_seconds": int(config.get("scan_seconds", 5)),
        "scan_mode": "one_shot_on_start",
        "last_scan_started_at": adapter_status.get("last_scan_started_at") or latest.get("last_scan_started_at"),
        "last_scan_finished_at": adapter_status.get("last_scan_finished_at") or latest.get("last_scan_finished_at"),
        "next_retry_at": None,
    }


def _start(context: IntegrationContext) -> Any:
    from . import adapter

    if not adapter.is_configured() and _config_section(context).get("enabled"):
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

    if not adapter.is_configured() and _config_section(context).get("enabled"):
        _initialize(context)
    else:
        adapter.restart()
    return adapter.get_status()


def _trial_start(context: IntegrationContext, options: dict[str, Any]) -> None:
    from . import adapter

    adapter.set_routing(
        forward_to_lsl=None,
        forward_to_touchdesigner=bool(options.get("brainbit_to_touchdesigner", False)),
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
    on_trial_start=_trial_start,
    on_trial_stop=_trial_stop,
    get_interval_summary=_interval,
    export_interval_samples=_export,
    sidecar_sensor="brainbit",
    sidecar_filename_suffix="brainbit_signals",
    sidecar_output_key="brainbit_file",
)
