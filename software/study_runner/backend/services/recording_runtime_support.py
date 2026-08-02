"""Small shared values and pure helpers for recording orchestration."""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
import socket
import time
from typing import Any, Callable, Mapping

from ..recording.artifacts import ArtifactPaths, SessionIdentity
from ..recording.errors import RecordingError
from ..recording.worker_protocol import LoopbackWorkerClient


RECORDING_PLAN_SCHEMA = "study-runner/recording-plan/v1"
DEFAULT_WORKER_START_TIMEOUT_SECONDS = 8.0
RECORDING_COMMAND_TIMEOUT_SECONDS = 8.0


class RecordingRuntimeError(RecordingError):
    """A recording session could not be prepared or finalized safely."""


def identity_from_session(session: Mapping[str, Any]) -> SessionIdentity:
    try:
        started = dt.datetime.fromtimestamp(float(session["started_at_epoch"]), tz=dt.timezone.utc)
    except (KeyError, TypeError, ValueError) as error:
        raise RecordingRuntimeError("tracked session has no valid started_at_epoch") from error
    return SessionIdentity(
        study_id=str(session.get("study_id") or ""),
        participant_id=str(session.get("participant_id") or ""),
        session_id=str(session.get("session_id") or ""),
        started_at=started,
    )


def reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def recovery_backup_grid_anchor(
    session_anchor_epoch: float,
    rate_hz: float,
    recovery_epoch: float,
) -> float:
    """Return the last grid point so a recovered writer starts at the next one."""

    if not all(math.isfinite(value) for value in (session_anchor_epoch, rate_hz, recovery_epoch)):
        raise RecordingRuntimeError("backup recovery grid values must be finite")
    if rate_hz <= 0:
        raise RecordingRuntimeError("backup recovery rate must be positive")
    if recovery_epoch <= session_anchor_epoch:
        return session_anchor_epoch
    period = 1.0 / rate_hz
    elapsed_periods = math.floor((recovery_epoch - session_anchor_epoch) / period)
    return session_anchor_epoch + elapsed_periods * period


def wait_for_required_worker_sources(
    client: LoopbackWorkerClient,
    *,
    session_id: str,
    generation: int,
    manifests: Mapping[str, Mapping[str, Any]],
    required_sources: set[str],
    timeout_seconds: float = 4.0,
    maximum_primary_age_seconds: float = 2.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> Mapping[str, Any]:
    """Gate visual study onset on worker-observed required-source freshness."""

    deadline = monotonic() + timeout_seconds
    last_issues: list[str] = ["worker did not publish readiness state"]
    while monotonic() < deadline:
        response = client.send(
            "health",
            {"session_id": session_id, "generation": generation},
            command_id=f"health-readiness-{session_id}-g{generation}-{time.monotonic_ns()}",
        )
        require_worker_ok(response.ok, response.error, "report recording readiness")
        health = response.result
        if health.get("readiness_contract") != "fresh-primary/v1":
            return health
        if health.get("frozen"):
            raise RecordingRuntimeError("recording worker froze before study onset")
        source_states = health.get("sources")
        source_states = source_states if isinstance(source_states, Mapping) else {}
        issues: list[str] = []
        for plugin_key in sorted(required_sources):
            source = source_states.get(plugin_key)
            if not isinstance(source, Mapping):
                issues.append(f"{plugin_key}: worker has no source state")
                continue
            if source.get("fatal_error"):
                issues.append(f"{plugin_key}: {source['fatal_error']}")
            streams = source.get("streams")
            streams = streams if isinstance(streams, list) else []
            if not streams or any(
                not isinstance(item, Mapping) or not bool(item.get("header_written"))
                for item in streams
            ):
                issues.append(f"{plugin_key}: not all declared XDF stream headers are open")
                continue
            capabilities = set((manifests.get(plugin_key) or {}).get("capabilities") or [])
            if "study_sensor" not in capabilities:
                continue
            manifest_streams = (manifests.get(plugin_key) or {}).get("streams") or []
            primary_manifest = next(
                (
                    item
                    for item in manifest_streams
                    if isinstance(item, Mapping) and bool(item.get("primary"))
                ),
                manifest_streams[0] if manifest_streams else {},
            )
            primary_key = (
                str(primary_manifest.get("key") or "")
                if isinstance(primary_manifest, Mapping)
                else ""
            )
            primary = next(
                (
                    item
                    for item in streams
                    if isinstance(item, Mapping) and str(item.get("key") or "") == primary_key
                ),
                None,
            )
            if not isinstance(primary, Mapping) or int(primary.get("sample_count") or 0) < 1:
                issues.append(f"{plugin_key}: primary stream has no sample")
                continue
            age = primary.get("last_sample_age_seconds")
            try:
                fresh = age is not None and 0.0 <= float(age) <= maximum_primary_age_seconds
            except (TypeError, ValueError):
                fresh = False
            if not fresh:
                issues.append(f"{plugin_key}: primary sample is stale")
        if not issues:
            return health
        last_issues = issues
        sleeper(0.1)
    raise RecordingRuntimeError(
        "required recording sources did not become ready: " + "; ".join(last_issues)
    )


def require_worker_ok(ok: bool, error: str | None, operation: str) -> None:
    if not ok:
        raise RecordingRuntimeError(error or f"recording worker could not {operation}")


def read_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RecordingRuntimeError(f"JSON artifact is unreadable: {path}") from error
    if not isinstance(payload, dict):
        raise RecordingRuntimeError(f"JSON artifact must be an object: {path}")
    return payload


def parse_utc(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def session_relative_path(paths: ArtifactPaths, value: str) -> Path:
    candidate = (paths.root / value).resolve()
    if not candidate.is_relative_to(paths.root.resolve()):
        raise RecordingRuntimeError("recording plan contains a path outside its session")
    return candidate


def public_plan(plan: Mapping[str, Any], *, reused: bool = False) -> dict[str, Any]:
    return {
        "recording_expected": True,
        "status": plan.get("status"),
        "plugins": list(plan.get("recording_plugins") or []),
        "backup": plan.get("backup"),
        "worker": plan.get("worker"),
        "reused": reused,
    }
