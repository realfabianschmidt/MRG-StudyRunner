"""The one explicit session lifecycle, mapped from the machines that own it.

Package 5a (docs/archive/architecture-1.0-umbau.md), target doc §10. Until now a
session's overall state was implicit, spread across three files that each
answer a different question:

- ``recording-plan.json``'s ``status`` -- is the recorder running?
- ``finalization-state.json``'s ``status`` -- has the finalization job run?
- ``finalization-state.json``'s ``quality_status`` -- did the data validate?

Those three deliberately **stay** separate state machines: each one is
owned by a different part of the system and answers its own question well
(target doc §10: "Der Finalisierungsauftrag bleibt getrennt davon in seinen
bewährten Zuständen"). What was missing is a single name for "where is this
session overall", which is what an operator, the session browser, and a
withdrawal request all actually ask. This module adds that name and the
documented mapping to it -- it does not replace anything.

Two rules the mapping exists to protect:

- **Upload status is not an input.** A destination that will not accept a
  file cannot un-seal validated scientific data (ownership table:
  "failed upload does not unseal valid data"). Uploads have their own job
  states and are read separately.
- **SEALED means the data validated**, not merely that a job finished.
  ``quality_status`` stays the place that says *how* clean it was; a
  human-confirmed degraded outcome is still sealed, an unresolved failure
  is not.
"""
from __future__ import annotations

from typing import Any, Mapping


# Terminal marker files a session tree can carry. COMPLETE/ATTENTION_REQUIRED
# already exist (artifact_manifest_service.py); WITHDRAWN is defined here so
# 5i's withdrawal workflow has a settled name and transition to write into,
# rather than inventing one later next to an already-shipped lifecycle.
WITHDRAWN_MARKER = "WITHDRAWN.json"

IDLE = "IDLE"
PREFLIGHT = "PREFLIGHT"
RECORDING = "RECORDING"
FINALIZING = "FINALIZING"
SEALED = "SEALED"
WITHDRAWN = "WITHDRAWN"
FAILED = "FAILED"

SESSION_LIFECYCLE_STATES = (
    IDLE,
    PREFLIGHT,
    RECORDING,
    FINALIZING,
    SEALED,
    WITHDRAWN,
    FAILED,
)

# SEALED never returns to FINALIZING: re-running a delivery step does not
# re-open validated data. FAILED does, because a retry is a real operator
# action on a finalization job that stopped. WITHDRAWN is the only state
# reachable from everywhere and reachable from nowhere -- consent can be
# withdrawn during recording and after sealing alike (target doc §10).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    IDLE: frozenset({PREFLIGHT, WITHDRAWN}),
    PREFLIGHT: frozenset({RECORDING, FAILED, WITHDRAWN}),
    RECORDING: frozenset({FINALIZING, FAILED, WITHDRAWN}),
    FINALIZING: frozenset({SEALED, FAILED, WITHDRAWN}),
    SEALED: frozenset({WITHDRAWN}),
    FAILED: frozenset({FINALIZING, WITHDRAWN}),
    WITHDRAWN: frozenset(),
}

# recording-plan.json status -> lifecycle, while no finalization job exists.
_RECORDING_STATES = {
    "starting": PREFLIGHT,
    "recording": RECORDING,
    "recovering": RECORDING,
    # The recorder surfaced a problem but the participation continues; the
    # reason stays in the plan's own status, which is not overwritten here.
    "attention_required": RECORDING,
    "frozen": FINALIZING,
}

# finalization-state.json status -> lifecycle. "attention_required" is not
# terminal: an operator can retry it or confirm a degraded outcome, so the
# session is still finalizing, just stalled on a human.
_FINALIZATION_STATES = {
    "queued": FINALIZING,
    "running": FINALIZING,
    "retrying": FINALIZING,
    "attention_required": FINALIZING,
    "completed": SEALED,
    "completed_degraded": SEALED,
    "failed": FAILED,
}


class SessionLifecycleError(ValueError):
    """Raised when a caller asks for a transition the lifecycle forbids."""


def is_allowed_transition(current: str, following: str) -> bool:
    """Whether ``current -> following`` is a defined lifecycle transition."""

    return following in ALLOWED_TRANSITIONS.get(str(current), frozenset())


def require_transition(current: str, following: str) -> None:
    """Raise unless ``current -> following`` is allowed. Staying put is fine."""

    if current == following:
        return
    if not is_allowed_transition(current, following):
        raise SessionLifecycleError(
            f"session lifecycle cannot move from {current!r} to {following!r}"
        )


def derive_session_lifecycle(
    *,
    recording_plan: Mapping[str, Any] | None = None,
    finalization_state: Mapping[str, Any] | None = None,
    terminal_marker: Mapping[str, Any] | None = None,
    withdrawn: bool = False,
) -> str:
    """Name the session's overall state from the machines that own the detail.

    Reads only documents a session already writes, so this stays a pure
    derivation with nothing of its own to keep in sync. An unknown or
    missing status is treated as "not started" rather than guessed at --
    a wrong confident answer here would be worse than an honest ``IDLE``.

    ``terminal_marker`` is the ``COMPLETE.json``/``ATTENTION_REQUIRED.json``
    payload, checked when no finalization state survives. An archival
    session recorded before this machinery existed still carries its
    marker, and reporting such a visibly finished session as ``IDLE``
    would be exactly the confident wrong answer above. The marker uses the
    same status vocabulary as the finalization job that wrote it.
    """
    if withdrawn:
        return WITHDRAWN

    for document in (finalization_state, terminal_marker):
        mapped = _FINALIZATION_STATES.get(_status_of(document))
        if mapped is None:
            continue
        # A completed job whose data never validated is not sealed. This is
        # the one place the two dimensions are read together, and only to
        # keep SEALED honest -- quality_status stays its own field.
        if mapped is SEALED and _quality_of(document) == "invalid":
            return FINALIZING
        return mapped

    mapped = _RECORDING_STATES.get(_status_of(recording_plan))
    return mapped if mapped is not None else IDLE


def _status_of(document: Mapping[str, Any] | None) -> str:
    if not isinstance(document, Mapping):
        return ""
    return str(document.get("status") or "").strip()


def _quality_of(document: Mapping[str, Any] | None) -> str:
    if not isinstance(document, Mapping):
        return ""
    return str(document.get("quality_status") or "").strip()
