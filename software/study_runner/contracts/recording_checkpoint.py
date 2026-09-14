"""How far a recording is known to be on disk, and what lies beyond that.

Package 5h (docs/archive/architecture-1.0-umbau.md), target doc §7: an interrupted
recording must never lose data *silently*. The existing machinery already
survives a crash -- the dying generation's XDF segment is left in place and
a new segment is opened (``recording_runtime._reattach_or_recover``) -- but
it could not answer the one question that makes the promise checkable:

    How much of that segment was actually on the disk when the power went?

The worker flushes durably every few seconds, so the answer is "everything
up to some flush, and then an unknown amount more". Without a record of
where the flushes fell, the unknown amount is indistinguishable from no
loss at all. That is the silent case this module removes.

The rule is an ordering, not a new mechanism:

    write samples -> flush the data durably -> append a checkpoint naming
    the committed position -> fsync the checkpoint -> only now is that
    prefix confirmed

Because the checkpoint is fsynced *after* the data it describes, a
checkpoint that survives a crash is a promise the data under it survived
too. The converse is what recovery uses: samples past the last surviving
checkpoint are the **unconfirmed tail**. They are usually fine -- the OS
very likely wrote them -- but "very likely" is not a claim this project
makes about scientific data, so the tail is journaled as a quality event
and the operator decides.

Lives in ``contracts`` rather than next to the writer because both sides
need it and may not import each other (invariant #1): the worker appends
these records, the host reads them back during recovery.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

CHECKPOINT_JOURNAL_SCHEMA = "study-runner/recording-checkpoints/v1"
CHECKPOINT_JOURNAL_FILENAME = "checkpoints.jsonl"


def stream_commit(
    *,
    plugin_key: str,
    stream_key: str,
    sample_count: int,
    last_timestamp: float | None,
) -> dict[str, Any]:
    """One stream's committed position within a checkpoint."""
    return {
        "plugin_key": str(plugin_key),
        "stream_key": str(stream_key),
        "sample_count": int(sample_count),
        "last_timestamp": None if last_timestamp is None else float(last_timestamp),
    }


def checkpoint_record(
    *,
    generation: int,
    monotonic: float,
    segment_relative_path: str = "",
    streams: Sequence[Mapping[str, Any]] = (),
    reason: str = "periodic",
) -> dict[str, Any]:
    """One ``checkpoints.jsonl`` line: everything below this point is on disk.

    ``generation`` matters as much as the position: a session that crashed
    twice has three segments, and a committed sample count only means
    anything against the segment it was counted in.
    """
    return {
        "schema": CHECKPOINT_JOURNAL_SCHEMA,
        "generation": int(generation),
        "monotonic": float(monotonic),
        "segment_relative_path": str(segment_relative_path),
        "reason": str(reason),
        "streams": [dict(entry) for entry in streams],
    }


def read_checkpoints(path: Path) -> list[dict[str, Any]]:
    """Read a checkpoint journal, tolerating damage at the end.

    A torn final line is the *expected* state after a hard crash, not an
    error: the process died mid-write. Malformed lines are skipped rather
    than raised on, because refusing to read the journal would throw away
    the confirmed prefix over the one line that proves a crash happened.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("schema") == CHECKPOINT_JOURNAL_SCHEMA:
            records.append(record)
    return records


def last_checkpoint_for_generation(
    records: Iterable[Mapping[str, Any]],
    *,
    generation: int,
) -> dict[str, Any] | None:
    """The newest surviving checkpoint of one worker generation, if any."""
    latest: dict[str, Any] | None = None
    for record in records:
        if not isinstance(record, Mapping):
            continue
        if int(record.get("generation") or 0) != int(generation):
            continue
        if latest is None or float(record.get("monotonic") or 0.0) >= float(
            latest.get("monotonic") or 0.0
        ):
            latest = dict(record)
    return latest


def confirmed_stream_positions(
    checkpoint: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """``{"plugin.stream": commit}`` for a checkpoint, keyed for lookup."""
    if not isinstance(checkpoint, Mapping):
        return {}
    positions: dict[str, dict[str, Any]] = {}
    for entry in checkpoint.get("streams") or ():
        if not isinstance(entry, Mapping):
            continue
        plugin_key = str(entry.get("plugin_key") or "")
        stream_key = str(entry.get("stream_key") or "")
        if plugin_key and stream_key:
            positions[f"{plugin_key}.{stream_key}"] = dict(entry)
    return positions
