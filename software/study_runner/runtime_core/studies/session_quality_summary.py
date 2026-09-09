"""Turn a session's quality journal into three sentences, not thirty numbers.

Package A1 (docs/architecture-1.0-umbau.md, "Sichtbarkeit zuerst"). 5c wrote
``quality.jsonl`` during recording and 5h taught it to also carry
``unconfirmed_tail`` events during recovery, but neither had a reader on the
UI side -- ``summarize_quality_journal`` (contracts/quality_journal.py) had
no caller at all. A session could have gaps, a clock jump, or an unconfirmed
tail, and the only way to find out was to open ``quality.jsonl`` in a text
editor. That is exactly the kind of unreachable machinery
``CONTRIBUTING.md`` section 1 rules out, regardless of how correct it is.

This module is the missing reader. It returns *structured* findings rather
than English sentences: each finding names a kind, the stream it concerns,
and a small number of details, and the frontend renders and translates them
(the existing pattern -- see ``sessions-browser.js``'s use of
``t(key, fallback).replace('{placeholder}', value)``). Baking English text
in here would make it unlocalizable and duplicate the one place that already
knows how to translate.

Deliberately coarse: three health levels and a handful of finding kinds, not
per-sample jitter values or raw event dumps. Detail junkies still have
``quality.jsonl`` itself, and ``docs/how-recording-quality-works.md``
explains what is in it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from study_runner.contracts.quality_journal import (
    EVENT_CLOCK_JUMP,
    EVENT_GAP,
    EVENT_INGEST_BACKLOG,
    EVENT_SUMMARY,
    EVENT_TIMESTAMP_REGRESSION,
    EVENT_UNCONFIRMED_TAIL,
    QUALITY_JOURNAL_FILENAME,
)

SESSION_QUALITY_SUMMARY_SCHEMA = "study-runner/session-quality-summary/v1"

# Health levels, ordered from best to worst. A finding's kind maps to the
# worst level it can cause; the session's overall health is the worst level
# any finding actually reached.
HEALTH_CLEAN = "clean"
HEALTH_WARNINGS = "warnings"
HEALTH_ATTENTION = "attention"
# Distinct from "clean" on purpose: a session recorded before 5c existed, or
# a withdrawn tombstone, has no journal to read. Reporting that as "clean"
# would be the same confident-wrong-answer mistake 5a's lifecycle derivation
# already had to fix once (an archived session that never started reporting
# itself as IDLE) -- "nothing was measured" and "measured and found clean"
# are different claims, and only one of them is true here.
HEALTH_UNKNOWN = "unknown"

# Findings that mean a defect was recorded, ranked by severity for the
# per-kind -> health mapping below.
_ATTENTION_EVENTS = {EVENT_UNCONFIRMED_TAIL, EVENT_TIMESTAMP_REGRESSION}
_WARNING_EVENTS = {EVENT_GAP, EVENT_CLOCK_JUMP, EVENT_INGEST_BACKLOG}


def summarize_session_quality(session_root: Path) -> dict[str, Any]:
    """Read one session's ``quality.jsonl`` and reduce it to a UI-sized shape.

    Never raises: a missing, empty, or damaged journal is reported as
    ``HEALTH_UNKNOWN`` with no findings, the same way ``read_checkpoints``
    (contracts/recording_checkpoint.py) treats a torn journal as the
    expected shape of a crash rather than an error to propagate.
    """
    records = _read_journal(Path(session_root) / QUALITY_JOURNAL_FILENAME)
    if records is None:
        return _summary(HEALTH_UNKNOWN, findings=[], kept_up=None)

    findings = _findings(records)
    health = _health(findings)
    return _summary(health, findings=findings, kept_up=_kept_up(records, findings))


def _summary(health: str, *, findings: list[dict[str, Any]], kept_up: bool | None) -> dict[str, Any]:
    return {
        "schema": SESSION_QUALITY_SUMMARY_SCHEMA,
        "recording_health": health,
        "findings": findings,
        "kept_up": kept_up,
    }


def _read_journal(path: Path) -> list[dict[str, Any]] | None:
    """``None`` for "no evidence exists"; a list (possibly empty) otherwise.

    A session that recorded cleanly can have a journal of nothing but
    periodic ``summary`` events -- that is a real, readable "no defects
    found" and must not collapse into the same ``None`` as "never measured".
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            # A torn final line is the expected shape of a crash (5h), not
            # a reason to discard every line that came before it.
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _findings(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One finding per (event kind, stream), each counted rather than listed.

    An operator wants to know "there were 3 gaps in EEG", not read 3 near-
    identical lines. Clock jumps have no stream -- they are a property of
    the machine's clock, not of one sensor -- so they are counted globally.
    """
    gap_counts: dict[str, int] = {}
    regression_counts: dict[str, int] = {}
    backlog: dict[str, dict[str, Any]] = {}
    tails: dict[str, dict[str, Any]] = {}
    clock_jump_count = 0
    max_drift_seconds = 0.0

    for record in records:
        event = str(record.get("event") or "")
        details = record.get("details")
        details = details if isinstance(details, Mapping) else {}
        stream_key = str(record.get("stream_key") or "")

        if event == EVENT_GAP:
            gap_counts[stream_key] = gap_counts.get(stream_key, 0) + 1
        elif event == EVENT_TIMESTAMP_REGRESSION:
            regression_counts[stream_key] = regression_counts.get(stream_key, 0) + 1
        elif event == EVENT_CLOCK_JUMP:
            clock_jump_count += 1
            drift = abs(_as_float(details.get("drift_seconds")) or 0.0)
            max_drift_seconds = max(max_drift_seconds, drift)
        elif event == EVENT_INGEST_BACKLOG and not details.get("cleared"):
            # Edge-triggered in the journal (one "start", one "clear"); the
            # UI only needs "this happened", so only the onset is counted.
            entry = backlog.setdefault(stream_key, {"count": 0, "peak_fill_ratio": 0.0})
            entry["count"] += 1
            entry["peak_fill_ratio"] = max(
                entry["peak_fill_ratio"], _as_float(details.get("peak_fill_ratio")) or 0.0
            )
        elif event == EVENT_UNCONFIRMED_TAIL:
            # Last one wins: a session can recover more than once, and the
            # most recent boundary is the one that still matters.
            tails[stream_key] = {
                "confirmed_sample_count": details.get("confirmed_sample_count"),
                "generation": details.get("generation"),
            }

    findings: list[dict[str, Any]] = []
    for stream_key, count in sorted(gap_counts.items()):
        findings.append({"kind": EVENT_GAP, "stream_key": stream_key, "count": count})
    for stream_key, count in sorted(regression_counts.items()):
        findings.append({"kind": EVENT_TIMESTAMP_REGRESSION, "stream_key": stream_key, "count": count})
    if clock_jump_count:
        findings.append(
            {
                "kind": EVENT_CLOCK_JUMP,
                "count": clock_jump_count,
                "max_drift_seconds": round(max_drift_seconds, 3),
            }
        )
    for stream_key, entry in sorted(backlog.items()):
        findings.append(
            {
                "kind": EVENT_INGEST_BACKLOG,
                "stream_key": stream_key,
                "count": entry["count"],
                "peak_fill_ratio": round(entry["peak_fill_ratio"], 4),
            }
        )
    for stream_key, entry in sorted(tails.items()):
        findings.append({"kind": EVENT_UNCONFIRMED_TAIL, "stream_key": stream_key, **entry})
    return findings


def _health(findings: list[dict[str, Any]]) -> str:
    kinds = {finding["kind"] for finding in findings}
    if kinds & _ATTENTION_EVENTS:
        return HEALTH_ATTENTION
    if kinds & _WARNING_EVENTS:
        return HEALTH_WARNINGS
    return HEALTH_CLEAN


def _kept_up(records: list[dict[str, Any]], findings: list[dict[str, Any]]) -> bool | None:
    """The positive evidence: did the machine ever fall behind a sensor?

    ``True`` only when there is actual evidence to be positive about -- a
    periodic ``summary`` event (added in 5h) carrying a ``peak_fill_ratio``.
    A session with no summary events at all (it never reached the first
    checkpoint) has not demonstrated anything either way, so this stays
    ``None`` rather than defaulting to an unearned ``True``. Tied to the
    same ``ingest_backlog`` finding used for ``recording_health`` rather
    than a second threshold, so the two can never disagree with each other.
    """
    saw_summary = any(
        str(record.get("event") or "") == EVENT_SUMMARY
        and isinstance(record.get("details"), Mapping)
        and record["details"].get("peak_fill_ratio") is not None
        for record in records
    )
    if not saw_summary:
        return None
    return not any(finding["kind"] == EVENT_INGEST_BACKLOG for finding in findings)


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
