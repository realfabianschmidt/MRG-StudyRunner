from __future__ import annotations

import datetime as dt
import time
from pathlib import Path
from typing import Any

from ..settings.runtime_config import get_project_base_dir
from study_runner.plugin_framework.registry import (
    build_context,
    run_trial_marker,
    run_trial_start,
    run_trial_stop,
)
from study_runner.recording import clock_diagnostics as recording_clock_diagnostics
from study_runner.recording import markers as recording_markers


_RUNTIME = {
    "base_dir": None,
    "data_dir": None,
    "hardware_config": {},
    "local_secrets": {},
    "local_secrets_file": None,
}


def configure_runtime(
    *,
    base_dir: Path,
    data_dir: Path,
    hardware_config: dict[str, Any],
    local_secrets: dict[str, Any],
    local_secrets_file: Path,
) -> None:
    """Store runtime context for trial callbacks outside Flask request handlers."""
    _RUNTIME.update(
        {
            "base_dir": base_dir,
            "data_dir": data_dir,
            "hardware_config": hardware_config,
            "local_secrets": local_secrets,
            "local_secrets_file": local_secrets_file,
        }
    )


def start_trial_session(options=None):
    options = _prepare_event_options("start", options)
    run_trial_start(options, _runtime_context())
    _notify_internal_recording_sources(options, fallback_marker="study:start")
    print("[SERVER] Trial started")
    return _public_event_response(options)


def stop_trial_session(options=None):
    options = _prepare_event_options("stop", options)
    run_trial_stop(options, _runtime_context())
    _notify_internal_recording_sources(options, fallback_marker="study:stop")
    print("[SERVER] Trial stopped")
    return _public_event_response(options)


def send_trial_marker(event: str, options=None):
    options = _prepare_event_options(event, options)
    run_trial_marker(options, _runtime_context())
    _notify_internal_recording_sources(options, fallback_marker="study:marker")
    print(f"[SERVER] Trial marker: {event}")
    return _public_event_response(options)


def _notify_internal_recording_sources(options: dict[str, Any], *, fallback_marker: str) -> None:
    """Feed the two recording sources every session carries.

    They are not plugins -- see study_runner/recording/markers.py -- so they
    are not reached by run_trial_start/stop/marker above and are called here
    directly. Each is isolated the same way the generic dispatch isolates a
    plugin: one failing must not stop the other or the trial event itself.
    """
    try:
        recording_markers.send_marker(str(options.get("marker_value") or fallback_marker))
    except Exception as error:
        print(f"[RECORDING] markers trial event failed: {error}")
    try:
        recording_clock_diagnostics.emit(options)
    except Exception as error:
        print(f"[RECORDING] clock_diagnostics trial event failed: {error}")


def _runtime_context():
    base_dir = _RUNTIME.get("base_dir")
    if base_dir is None:
        base_dir = get_project_base_dir()
    data_dir = _RUNTIME.get("data_dir") or Path(base_dir) / "saved_results"
    local_secrets_file = _RUNTIME.get("local_secrets_file") or Path(base_dir) / "settings" / "local_secrets.json"
    return build_context(
        base_dir=Path(base_dir),
        data_dir=Path(data_dir),
        hardware_config=_RUNTIME.get("hardware_config") or {},
        local_secrets=_RUNTIME.get("local_secrets") or {},
        local_secrets_file=Path(local_secrets_file),
    )


def _build_marker(event: str, options: dict) -> str:
    study_id = options.get("study_id") or "study"
    participant_id = options.get("participant_id") or "participant"
    card_index = options.get("question_index")
    card_type = options.get("question_type") or "stimulus"
    phase = options.get("phase") or event
    parts = [
        event,
        f"study={_marker_value(study_id)}",
        f"participant={_marker_value(participant_id)}",
        f"card={_marker_value(card_index)}",
        f"type={_marker_value(card_type)}",
        f"phase={_marker_value(phase)}",
        f"server_ms={_marker_value(options.get('server_received_epoch_ms'))}",
    ]
    if options.get("client_trigger_epoch_ms") is not None:
        parts.append(f"client_ms={_marker_value(options.get('client_trigger_epoch_ms'))}")
    if options.get("event_id"):
        parts.append(f"event_id={_marker_value(options.get('event_id'))}")
    if options.get("stimulus_id"):
        parts.append(f"stimulus_id={_marker_value(options.get('stimulus_id'))}")
    if options.get("source_epoch_ms") is not None:
        parts.append(f"source_epoch_ms={_marker_value(options.get('source_epoch_ms'))}")
    return "|".join(parts)


def _prepare_event_options(event: str, options: dict[str, Any] | None) -> dict[str, Any]:
    event_options = dict(options or {})
    normalized_event = str(event_options.get("marker_event") or event or "marker").strip() or "marker"
    now = time.time()
    event_options["event"] = normalized_event
    event_options["server_received_epoch_ms"] = round(now * 1000.0, 3)
    event_options["server_received_at"] = dt.datetime.fromtimestamp(
        now,
        tz=dt.timezone.utc,
    ).isoformat().replace("+00:00", "Z")
    event_options["marker_value"] = _build_marker(normalized_event, event_options)
    return event_options


def _public_event_response(options: dict[str, Any]) -> dict[str, Any]:
    return {
        "server_received_epoch_ms": options.get("server_received_epoch_ms"),
        "server_received_at": options.get("server_received_at"),
        "marker_value": options.get("marker_value"),
    }


def _marker_value(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "_").replace("\n", " ").replace("\r", " ").strip()
