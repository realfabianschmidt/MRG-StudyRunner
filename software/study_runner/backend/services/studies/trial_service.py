from __future__ import annotations

import datetime as dt
import math
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


class TrialDispatchError(RuntimeError):
    """One or more durable trial-event components failed."""

    def __init__(self, outcomes: dict[str, dict[str, Any]], response: dict[str, Any]) -> None:
        self.outcomes = outcomes
        self.response = response
        failures = [name for name, outcome in outcomes.items() if not outcome.get("ok")]
        super().__init__(f"Trial event failed for component(s): {', '.join(failures)}")


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
    prior_outcomes = _take_prior_outcomes(options)
    outcomes = _notify_internal_recording_sources(
        options,
        fallback_marker="study:start",
        prior_outcomes=prior_outcomes,
    )
    outcomes = run_trial_start(options, _runtime_context(), outcomes)
    response = _public_event_response(options, outcomes)
    _raise_for_dispatch_failures(outcomes, response)
    print("[SERVER] Trial started")
    return response


def stop_trial_session(options=None):
    options = _prepare_event_options("stop", options)
    prior_outcomes = _take_prior_outcomes(options)
    outcomes = _notify_internal_recording_sources(
        options,
        fallback_marker="study:stop",
        prior_outcomes=prior_outcomes,
    )
    outcomes = run_trial_stop(options, _runtime_context(), outcomes)
    response = _public_event_response(options, outcomes)
    _raise_for_dispatch_failures(outcomes, response)
    print("[SERVER] Trial stopped")
    return response


def send_trial_marker(event: str, options=None):
    options = _prepare_event_options(event, options)
    prior_outcomes = _take_prior_outcomes(options)
    outcomes = _notify_internal_recording_sources(
        options,
        fallback_marker="study:marker",
        prior_outcomes=prior_outcomes,
    )
    outcomes = run_trial_marker(options, _runtime_context(), outcomes)
    response = _public_event_response(options, outcomes)
    _raise_for_dispatch_failures(outcomes, response)
    print(f"[SERVER] Trial marker: {event}")
    return response


def _notify_internal_recording_sources(
    options: dict[str, Any],
    *,
    fallback_marker: str,
    prior_outcomes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Feed the two recording sources every session carries.

    They are not plugins -- see study_runner/recording/markers.py -- so they
    are not reached by run_trial_start/stop/marker above and are called here
    directly. Each is isolated so one failure does not prevent the other from
    being attempted, while the returned outcomes keep the durable event from
    being incorrectly acknowledged as complete.
    """
    outcomes = dict(prior_outcomes or {})
    components = (
        (
            "core.markers",
            lambda: recording_markers.send_marker(
                str(options.get("marker_value") or fallback_marker),
                server_epoch_ms=options.get("source_epoch_ms")
                or options.get("server_received_epoch_ms"),
            ),
        ),
        ("core.clock_diagnostics", lambda: recording_clock_diagnostics.emit(options)),
    )
    for component, callback in components:
        previous = outcomes.get(component)
        if isinstance(previous, dict) and previous.get("ok") is True:
            continue
        try:
            result = callback()
        except Exception as error:
            outcomes[component] = {"ok": False, "component": component, "error": str(error)}
            print(f"[RECORDING] {component} trial event failed: {error}")
        else:
            outcomes[component] = {"ok": True, "component": component}
            if isinstance(result, dict):
                outcomes[component].update(result)
    return outcomes


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
    if options.get("visual_onset_epoch_ms") is not None:
        parts.append(f"visual_onset_ms={_marker_value(options.get('visual_onset_epoch_ms'))}")
    if options.get("onset_uncertainty_ms") is not None:
        parts.append(f"onset_uncertainty_ms={_marker_value(options.get('onset_uncertainty_ms'))}")
    return "|".join(parts)


def _prepare_event_options(event: str, options: dict[str, Any] | None) -> dict[str, Any]:
    event_options = dict(options or {})
    normalized_event = str(event_options.get("marker_event") or event or "marker").strip() or "marker"
    now = time.time()
    server_received_epoch_ms = _finite_epoch_ms(event_options.get("server_received_epoch_ms"))
    if server_received_epoch_ms is None:
        server_received_epoch_ms = round(now * 1000.0, 3)
    source_epoch_ms = _finite_epoch_ms(event_options.get("visual_onset_epoch_ms"))
    if source_epoch_ms is None:
        source_epoch_ms = _finite_epoch_ms(event_options.get("source_epoch_ms"))
    if source_epoch_ms is None:
        source_epoch_ms = _finite_epoch_ms(event_options.get("client_trigger_epoch_ms"))
    if source_epoch_ms is None:
        source_epoch_ms = server_received_epoch_ms
    event_options["event"] = normalized_event
    event_options["server_received_epoch_ms"] = server_received_epoch_ms
    event_options["server_received_at"] = dt.datetime.fromtimestamp(
        server_received_epoch_ms / 1000.0,
        tz=dt.timezone.utc,
    ).isoformat().replace("+00:00", "Z")
    event_options["processing_epoch_ms"] = round(now * 1000.0, 3)
    event_options["source_epoch_ms"] = source_epoch_ms
    event_options["marker_value"] = _build_marker(normalized_event, event_options)
    return event_options


def _public_event_response(
    options: dict[str, Any],
    outcomes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    marker_outcome = (outcomes or {}).get("core.markers") or {}
    return {
        "server_received_epoch_ms": options.get("server_received_epoch_ms"),
        "server_received_at": options.get("server_received_at"),
        "marker_value": options.get("marker_value"),
        "source_epoch_ms": options.get("source_epoch_ms"),
        "visual_onset_epoch_ms": options.get("visual_onset_epoch_ms"),
        "onset_uncertainty_ms": options.get("onset_uncertainty_ms"),
        "marker_lsl_timestamp": marker_outcome.get("marker_lsl_timestamp"),
        "marker_push_epoch_ms": marker_outcome.get("marker_push_epoch_ms"),
        "dispatch": dict(outcomes or {}),
    }


def _take_prior_outcomes(options: dict[str, Any]) -> dict[str, dict[str, Any]]:
    prior = options.pop("_trial_component_outcomes", None)
    return dict(prior) if isinstance(prior, dict) else {}


def _raise_for_dispatch_failures(
    outcomes: dict[str, dict[str, Any]],
    response: dict[str, Any],
) -> None:
    if any(not outcome.get("ok") for outcome in outcomes.values()):
        raise TrialDispatchError(outcomes, response)


def _finite_epoch_ms(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return round(number, 3)


def _marker_value(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "_").replace("\n", " ").replace("\r", " ").strip()
