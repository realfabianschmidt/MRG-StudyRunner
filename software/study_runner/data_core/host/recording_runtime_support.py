"""Small shared values and pure helpers for recording orchestration."""

from __future__ import annotations

import datetime as dt
import json
import math
import os
from pathlib import Path
import socket
import time
from typing import Any, Callable, Mapping

from study_runner.contracts.quality_journal import (
    EVENT_UNCONFIRMED_TAIL,
    QUALITY_JOURNAL_FILENAME,
    quality_record,
)
from study_runner.contracts.recording_checkpoint import (
    CHECKPOINT_JOURNAL_FILENAME,
    confirmed_stream_positions,
    last_checkpoint_for_generation,
    read_checkpoints,
)
from study_runner.data_core.host.artifacts import ArtifactPaths, SessionIdentity
from study_runner.data_core.contract.recording_errors import RecordingError
from study_runner.data_core.contract.worker_protocol import LoopbackWorkerClient


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
    secondary_grace_seconds: float = 2.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> Mapping[str, Any]:
    """Gate the study-start marker until every regular sensor stream is being written.

    Waiting for the primary stream alone was not enough: derived streams (for
    example band power at 25 Hz) open their LSL inlet a little later, so their
    first sample landed after the marker and the session failed its
    time-coverage check although every sample was recorded.

    The primary stream of each required study sensor must hold a fresh sample
    within ``timeout_seconds``; otherwise the start fails. Every other regular
    stream (``nominal_rate_hz > 0``) is then waited for up to
    ``secondary_grace_seconds`` more. A derived stream may legitimately be
    silent (for example before a calibration), so after the grace the start
    continues and the silent streams are returned as ``late_streams``; the
    source validation reports them as a quality warning.
    """

    deadline = monotonic() + timeout_seconds
    last_issues: list[str] = ["worker did not publish readiness state"]
    primary_ready_at: float | None = None
    while monotonic() < deadline or primary_ready_at is not None:
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
        late: list[str] = []
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
            primary_issues, secondary_issues = split_stream_readiness_issues(
                plugin_key,
                streams,
                maximum_age_seconds=maximum_primary_age_seconds,
            )
            issues.extend(primary_issues)
            late.extend(secondary_issues)
        if issues:
            primary_ready_at = None
            last_issues = issues
            if monotonic() >= deadline:
                break
            sleeper(0.1)
            continue
        if not late:
            return health
        if primary_ready_at is None:
            primary_ready_at = monotonic()
        if monotonic() - primary_ready_at >= secondary_grace_seconds:
            return {**dict(health), "late_streams": late}
        sleeper(0.05)
    raise RecordingRuntimeError(
        "required recording sources did not become ready: " + "; ".join(last_issues)
    )


def _stream_rate(item: Mapping[str, Any]) -> float:
    try:
        rate = float(item.get("nominal_rate_hz") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return rate if math.isfinite(rate) and rate > 0 else 0.0


def _stream_issue(plugin_key: str, item: Mapping[str, Any], maximum_age_seconds: float) -> str | None:
    rate = _stream_rate(item)
    stream_key = str(item.get("key") or item.get("source_id") or "stream")
    if int(item.get("sample_count") or 0) < 1:
        return f"{plugin_key}.{stream_key}: no sample recorded yet"
    age = item.get("last_sample_age_seconds")
    limit = max(float(maximum_age_seconds), 5.0 / rate)
    try:
        fresh = age is not None and 0.0 <= float(age) <= limit
    except (TypeError, ValueError):
        fresh = False
    return None if fresh else f"{plugin_key}.{stream_key}: latest sample is stale"


def split_stream_readiness_issues(
    plugin_key: str,
    streams: list[Any],
    *,
    maximum_age_seconds: float = 2.0,
) -> tuple[list[str], list[str]]:
    """Return ``(primary issues, other regular stream issues)``.

    The primary stream is the one the worker marks ``primary``; without a
    mark, the fastest regular stream (the raw signal) plays that role.
    Irregular event streams (rate 0) never block: they may be quiet.
    """

    regular = [item for item in streams if isinstance(item, Mapping) and _stream_rate(item) > 0]
    if not regular:
        return [], []
    primary = next((item for item in regular if bool(item.get("primary"))), None)
    if primary is None:
        primary = max(regular, key=_stream_rate)
    primary_issues: list[str] = []
    secondary_issues: list[str] = []
    for item in regular:
        issue = _stream_issue(plugin_key, item, maximum_age_seconds)
        if issue is None:
            continue
        (primary_issues if item is primary else secondary_issues).append(issue)
    return primary_issues, secondary_issues


def regular_stream_readiness_issues(
    plugin_key: str,
    streams: list[Any],
    *,
    maximum_age_seconds: float = 2.0,
) -> list[str]:
    """Name every regular stream that has not written a fresh sample yet."""

    primary_issues, secondary_issues = split_stream_readiness_issues(
        plugin_key,
        streams,
        maximum_age_seconds=maximum_age_seconds,
    )
    return [*primary_issues, *secondary_issues]


def stream_tail_issues(
    source_states: Mapping[str, Any],
    required_sources: set[str],
    *,
    marker_lsl_timestamp: float,
) -> list[str]:
    """Name every regular stream whose last sample is still before the end marker."""

    issues: list[str] = []
    for plugin_key in sorted(required_sources):
        source = source_states.get(plugin_key)
        if not isinstance(source, Mapping):
            continue
        streams = source.get("streams")
        for item in streams if isinstance(streams, list) else []:
            if not isinstance(item, Mapping) or _stream_rate(item) <= 0:
                continue
            stream_key = str(item.get("key") or item.get("source_id") or "stream")
            last = item.get("last_timestamp")
            try:
                reached = last is not None and float(last) >= float(marker_lsl_timestamp)
            except (TypeError, ValueError):
                reached = False
            if not reached:
                issues.append(f"{plugin_key}.{stream_key}")
    return issues


def wait_for_stream_tail(
    client: LoopbackWorkerClient,
    *,
    session_id: str,
    generation: int,
    required_sources: set[str],
    marker_lsl_timestamp: float,
    timeout_seconds: float = 3.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Wait until every regular stream has written data past the end marker.

    This replaces stopping the sensors before the recording is frozen: the
    sensors keep streaming for the next participant, and the file is closed
    only once each stream covers the marker-defined session window. A timeout
    never blocks finalization; the lagging streams are reported instead and
    the source validation names them as a quality warning.
    """

    deadline = monotonic() + max(0.0, timeout_seconds)
    lagging: list[str] = ["worker did not publish stream state"]
    while True:
        response = client.send(
            "health",
            {"session_id": session_id, "generation": generation},
            command_id=f"health-tail-{session_id}-g{generation}-{time.monotonic_ns()}",
        )
        require_worker_ok(response.ok, response.error, "report recording tail")
        health = response.result
        if health.get("frozen"):
            return {"reached": False, "lagging_streams": [], "worker_already_frozen": True}
        source_states = health.get("sources")
        source_states = source_states if isinstance(source_states, Mapping) else {}
        lagging = stream_tail_issues(
            source_states,
            required_sources,
            marker_lsl_timestamp=marker_lsl_timestamp,
        )
        if not lagging:
            return {"reached": True, "lagging_streams": []}
        if monotonic() >= deadline:
            return {"reached": False, "lagging_streams": lagging}
        sleeper(0.1)


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


def report_unconfirmed_tail(paths: ArtifactPaths, *, generation: int, monotonic: float) -> int:
    """Journal what the dying worker generation could not vouch for (5h).

    The crashed process is the one that could have described its own last
    seconds, so the host does it instead, once, at the moment it decides to
    start a replacement generation.

    What gets reported is a *boundary*, not a loss: samples after the last
    surviving checkpoint may well be in the file. The point is that nobody
    confirmed they are, and an unconfirmed boundary that nobody wrote down
    is exactly the silent case package 5h exists to remove. An operator
    reading ``quality.jsonl`` afterwards sees where to look.

    Returns how many streams were reported, for the caller's logging.
    Never raises: a recovery must not fail because its own note-taking did.
    """
    if generation < 1:
        return 0
    checkpoint = last_checkpoint_for_generation(
        read_checkpoints(paths.checkpoint_journal_file),
        generation=int(generation),
    )
    if checkpoint is not None and str(checkpoint.get("reason") or "") == "freeze":
        # That generation closed cleanly and confirmed its whole segment;
        # there is no tail, and inventing an event here would teach
        # operators to ignore a warning that is usually meaningless.
        return 0
    positions = confirmed_stream_positions(checkpoint)
    records = [
        quality_record(
            event=EVENT_UNCONFIRMED_TAIL,
            monotonic=float(monotonic),
            plugin_key=str(commit.get("plugin_key") or ""),
            stream_key=str(commit.get("stream_key") or ""),
            generation=int(generation),
            segment_relative_path=str(checkpoint.get("segment_relative_path") or "") if checkpoint else "",
            confirmed_sample_count=commit.get("sample_count"),
            confirmed_last_timestamp=commit.get("last_timestamp"),
            confirmed_at_monotonic=checkpoint.get("monotonic") if checkpoint else None,
        )
        for commit in positions.values()
    ]
    if not records:
        # No checkpoint survived at all: the generation died before its
        # first durable flush, or the journal itself was lost. Say so
        # rather than staying quiet, because "nothing confirmed" is the
        # strongest form of this warning, not the absence of one.
        records = [
            quality_record(
                event=EVENT_UNCONFIRMED_TAIL,
                monotonic=float(monotonic),
                generation=int(generation),
                confirmed_sample_count=0,
                note="no checkpoint survived for this worker generation",
            )
        ]
    try:
        with (paths.quality_journal_file).open("a", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        return 0
    return len(records)
