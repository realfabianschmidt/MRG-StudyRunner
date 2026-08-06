"""Periodic background export of live sensor history to disk.

Without this, all sensor data for a running session exists only in the
in-memory history deques and is lost the moment the process dies -
answers survive per-card (``_partial/``), but biosignals do not. Every
``interval_seconds`` this re-exports each active session's full history
(session start to now) and atomically overwrites one flush file per
sensor, so a crash never loses more than one interval's worth of data.
The export itself is a pure read of the same bounded deques the final
submit reads, so re-running it on a growing window is cheap and safe.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from study_runner.plugin_framework.registry import build_context, export_interval_sidecars

from ..shared.atomic_io import atomic_write_json
from ..studies.results_service import sanitize_identifier_for_filename

DEFAULT_INTERVAL_SECONDS = 60


class SensorFlushService:
    def __init__(
        self,
        app,
        *,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.app = app
        self.interval_seconds = interval_seconds
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="study-runner-sensor-flush",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, timeout))
        self._thread = None

    def _worker_loop(self) -> None:
        while not self._stop.wait(timeout=self.interval_seconds):
            try:
                self.flush_once()
            except Exception as error:
                print(f"[SENSOR-FLUSH] Flush pass failed: {error}")

    def flush_once(self) -> int:
        """Export every active session's sensor history once. Returns file count written."""
        config = self.app.config
        hardware_config = config.get("ACTIVE_STUDY_HARDWARE_CONFIG")
        if not hardware_config:
            return 0

        sessions = [
            session
            for session in config["SESSION_STORE"].list_active()
            if session.get("started_at_epoch") is not None
        ]
        if not sessions:
            return 0

        context = build_context(
            base_dir=config["BASE_DIR"],
            data_dir=config["DATA_DIR"],
            hardware_config=hardware_config,
            local_secrets=config.get("LOCAL_SECRETS", {}),
            local_secrets_file=config["LOCAL_SECRETS_FILE"],
        )
        now = self._clock()

        written = 0
        for session in sessions:
            try:
                written += self._flush_session(context, session, now)
            except Exception as error:
                print(f"[SENSOR-FLUSH] Could not flush session {session.get('session_id')}: {error}")
        return written

    def _flush_session(self, context, session: dict[str, Any], now: float) -> int:
        session_id = str(session.get("session_id") or "")
        study_id = str(session.get("study_id") or "")
        if not session_id or not study_id:
            return 0
        start_epoch = float(session["started_at_epoch"])
        if now <= start_epoch:
            return 0

        coordinator = self.app.config.get("SENSOR_COORDINATOR")
        exports = (
            coordinator.export_interval_sidecars(context, start_epoch, now)
            if coordinator
            else export_interval_sidecars(context, start_epoch, now)
        )
        if not exports:
            return 0

        safe_study_id = sanitize_identifier_for_filename(study_id)
        safe_session_id = sanitize_identifier_for_filename(session_id)
        flush_dir = Path(self.app.config["DATA_DIR"]) / safe_study_id / "_flush"

        for export in exports:
            suffix = str(export.get("filename_suffix") or export.get("plugin_key"))
            path = flush_dir / f"{safe_session_id}_{suffix}.json"
            atomic_write_json(
                path,
                {
                    "session_id": session_id,
                    "study_id": study_id,
                    "participant_id": session.get("participant_id"),
                    "sensor": export.get("sensor"),
                    "filename_suffix": suffix,
                    "output_key": export.get("output_key"),
                    "flushed_at": now,
                    "interval_start_epoch": start_epoch,
                    "interval_end_epoch": now,
                    "samples": export.get("samples") or [],
                },
            )
        return len(exports)


def discard_session_flush_files(data_dir: Path, study_id: str, session_id: str) -> None:
    """Remove flush files once a session's results are safely saved (or discarded)."""
    if not study_id or not session_id:
        return
    safe_study_id = sanitize_identifier_for_filename(study_id)
    safe_session_id = sanitize_identifier_for_filename(session_id)
    flush_dir = Path(data_dir) / safe_study_id / "_flush"
    if not flush_dir.is_dir():
        return
    for path in flush_dir.glob(f"{safe_session_id}_*.json"):
        try:
            path.unlink()
        except OSError as error:
            print(f"[SENSOR-FLUSH] Could not remove flush file {path.name}: {error}")
