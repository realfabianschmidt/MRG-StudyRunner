from __future__ import annotations

from pathlib import Path
from typing import Any

from study_runner.integrations.registry import build_context, run_trial_start, run_trial_stop


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
    options = dict(options or {})
    options["marker_value"] = _build_marker("start", options)
    run_trial_start(options, _runtime_context())
    print("[SERVER] Trial started")


def stop_trial_session(options=None):
    options = dict(options or {})
    options["marker_value"] = _build_marker("stop", options)
    run_trial_stop(options, _runtime_context())
    print("[SERVER] Trial stopped")


def _runtime_context():
    base_dir = _RUNTIME.get("base_dir")
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent
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
    return f"{event}|study={study_id}|participant={participant_id}|card={card_index}|type={card_type}"
