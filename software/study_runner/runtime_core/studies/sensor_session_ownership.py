"""Who owns each sensor's per-participant state.

Sensors keep running between participants; ``session_end`` only resets what
belonged to one person (contact, calibration). Finalization sends it late,
after the recording froze, and by then the operator may already be preparing
the next person. This ledger keeps that late reset from reaching anyone else:

- a session claims its sensors when it starts;
- an operator setup action outside the owning session claims the sensor for
  the next participant;
- ``session_end`` for a session is sent only to sensors that session still
  owns (or that nobody claimed since);
- a ``session_end`` that timed out has an unknown outcome. The sensor stays
  unavailable for setup and for a new session until the plugin's own status
  reports that end as finished (``driver_runtime`` keeps that record).
"""
from __future__ import annotations

import threading
from typing import Any, Iterable, Mapping

SETUP_OWNER = "setup"


class SensorSessionOwnership:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner: dict[str, str] = {}
        self._unresolved: dict[str, str] = {}

    def claim_session(self, plugin_keys: Iterable[str], session_id: str) -> None:
        with self._lock:
            for key in plugin_keys:
                self._owner[str(key)] = str(session_id)

    def claim_setup(self, plugin_key: str, *, active_session_id: str | None) -> None:
        """A setup action belongs to the active session, or else to the next person."""
        with self._lock:
            if active_session_id and self._owner.get(plugin_key) == active_session_id:
                return
            self._owner[plugin_key] = SETUP_OWNER

    def may_end(self, plugin_key: str, session_id: str) -> bool:
        with self._lock:
            return self._owner.get(plugin_key) in (None, session_id)

    def ended(self, plugin_key: str, session_id: str, *, outcome_known: bool) -> None:
        with self._lock:
            if outcome_known:
                if self._owner.get(plugin_key) == session_id:
                    self._owner.pop(plugin_key, None)
            else:
                self._unresolved[plugin_key] = session_id

    def unresolved(self, plugin_key: str) -> str | None:
        with self._lock:
            return self._unresolved.get(plugin_key)

    def reconcile(self, plugin_key: str, status: Any) -> bool:
        """True when nothing is pending, possibly after reading the plugin's status."""
        with self._lock:
            pending = self._unresolved.get(plugin_key)
            if pending is None:
                return True
            ledger = status.get("session_end") if isinstance(status, Mapping) else None
            ledger = ledger if isinstance(ledger, Mapping) else {}
            if pending in (ledger.get("last_completed"), ledger.get("last_failed")):
                self._unresolved.pop(plugin_key, None)
                if self._owner.get(plugin_key) == pending:
                    self._owner.pop(plugin_key, None)
                return True
            return False
