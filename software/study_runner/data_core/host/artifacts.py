"""Immutable session identities and their on-disk artifact layout."""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from study_runner.shared.atomic_io import atomic_write_json


IDENTITY_SCHEMA = "study-runner/session-identity/v1"
_UNSAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_COMPONENT_LENGTH = 80
_WINDOWS_RESERVED_COMPONENTS = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Return the SHA-256 digest of an immutable artifact without loading it all."""

    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    digest = hashlib.sha256()
    with Path(path).open("rb") as file_handle:
        while chunk := file_handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def safe_path_component(value: str, *, fallback: str) -> str:
    """Return a readable, bounded path component with collision protection.

    Identifiers which are already filesystem-safe remain unchanged.  If any
    normalization or truncation is necessary, a short digest of the *original*
    identifier is appended.  Therefore identifiers such as ``a/b`` and
    ``a?b`` cannot silently share one participant directory.
    """

    original = str(value or "").strip()
    candidate = _UNSAFE_COMPONENT.sub("_", original).strip("._-")
    if not candidate:
        candidate = fallback
    # A lowercase canonical spelling is the only hash-free form. This keeps
    # paths stable across case-sensitive and case-insensitive filesystems:
    # ``p01`` and ``P01`` no longer collide on Windows/macOS. Reserved Windows
    # device names are never emitted verbatim on any platform.
    unchanged = (
        candidate == original
        and len(candidate) <= _MAX_COMPONENT_LENGTH
        and original == original.casefold()
        and candidate.casefold().split(".", 1)[0] not in _WINDOWS_RESERVED_COMPONENTS
    )
    if unchanged:
        return candidate

    digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:10]
    prefix_length = _MAX_COMPONENT_LENGTH - len(digest) - 2
    prefix = candidate[:prefix_length].rstrip("._-") or fallback[:prefix_length]
    return f"{prefix}--{digest}"


@dataclass(frozen=True)
class SessionIdentity:
    """Stable identity used to reserve one immutable session directory."""

    study_id: str
    participant_id: str
    session_id: str
    started_at: dt.datetime

    def __post_init__(self) -> None:
        if not str(self.study_id).strip():
            raise ValueError("study_id is required")
        if not str(self.participant_id).strip():
            raise ValueError("participant_id is required")
        if not str(self.session_id).strip():
            raise ValueError("session_id is required")
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("started_at must be timezone-aware")

    @property
    def started_at_utc(self) -> dt.datetime:
        return self.started_at.astimezone(dt.timezone.utc)

    @property
    def study_component(self) -> str:
        return safe_path_component(self.study_id, fallback="study")

    @property
    def participant_component(self) -> str:
        return safe_path_component(self.participant_id, fallback="participant")

    @property
    def session_component(self) -> str:
        timestamp = self.started_at_utc.strftime("%Y%m%dT%H%M%SZ")
        safe_session_id = safe_path_component(self.session_id, fallback="session")
        return f"{timestamp}__{safe_session_id}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": IDENTITY_SCHEMA,
            "study_id": self.study_id,
            "participant_id": self.participant_id,
            "session_id": self.session_id,
            "started_at": self.started_at_utc.isoformat().replace("+00:00", "Z"),
            "path_components": {
                "study": self.study_component,
                "participant": self.participant_component,
                "session": self.session_component,
            },
        }


@dataclass(frozen=True)
class ArtifactPaths:
    """All canonical locations below one already-bound session directory."""

    root: Path
    identity: SessionIdentity

    @property
    def meta_dir(self) -> Path:
        return self.root / "meta"

    @property
    def meta_logs_dir(self) -> Path:
        return self.meta_dir / "logs"

    @property
    def answers_dir(self) -> Path:
        return self.root / "answers"

    @property
    def identity_file(self) -> Path:
        return self.meta_dir / "session-identity.json"

    @property
    def raw_dir(self) -> Path:
        return self.root / "raw"

    @property
    def raw_plugins_dir(self) -> Path:
        return self.raw_dir / "plugins"

    @property
    def raw_backup_dir(self) -> Path:
        return self.raw_dir / "backup"

    @property
    def derived_dir(self) -> Path:
        return self.root / "derived"

    @property
    def merged_xdf(self) -> Path:
        return self.derived_dir / "session.xdf"

    @property
    def worker_state_file(self) -> Path:
        return self.meta_dir / "worker-state.json"

    @property
    def worker_commands_file(self) -> Path:
        return self.meta_dir / "worker-commands.json"

    @property
    def recording_lease_file(self) -> Path:
        return self.meta_dir / "recording-lease.json"

    @property
    def recording_plan_file(self) -> Path:
        return self.meta_dir / "recording-plan.json"

    @property
    def stream_contracts_file(self) -> Path:
        return self.meta_dir / "stream-contracts.json"

    @property
    def submission_commit_file(self) -> Path:
        return self.meta_dir / ".submission-commit.json"

    @property
    def finalization_state_file(self) -> Path:
        return self.meta_dir / "finalization-state.json"

    @property
    def manifest_file(self) -> Path:
        return self.meta_dir / "manifest.json"

    @property
    def checksums_file(self) -> Path:
        return self.meta_dir / "checksums.sha256"

    @property
    def quality_journal_file(self) -> Path:
        return self.meta_dir / "quality.jsonl"

    @property
    def timing_journal_file(self) -> Path:
        return self.meta_dir / "timing.jsonl"

    @property
    def checkpoint_journal_file(self) -> Path:
        return self.meta_dir / "checkpoints.jsonl"

    @property
    def finalization_log_file(self) -> Path:
        return self.meta_logs_dir / "finalization.jsonl"

    @property
    def recording_worker_log_file(self) -> Path:
        return self.meta_logs_dir / "recording-worker.log"

    @property
    def session_journals_archive_file(self) -> Path:
        return self.meta_logs_dir / "session-journals.archive.json"

    @property
    def submission_file(self) -> Path:
        return self.answers_dir / "submission.json"

    @property
    def result_file(self) -> Path:
        return self.answers_dir / "result.json"

    @property
    def card_summary_file(self) -> Path:
        return self.answers_dir / "card-summary.json"

    @property
    def csv_export_file(self) -> Path:
        component = safe_path_component(self.identity.session_id, fallback="session")
        return self.root / f"{component}.csv"

    def plugin_dir(self, plugin_key: str) -> Path:
        component = safe_path_component(plugin_key, fallback="plugin")
        return self.raw_plugins_dir / component

    def backup_xdf(self, rate_hz: float) -> Path:
        if rate_hz <= 0:
            raise ValueError("backup rate must be positive")
        rate_label = f"{rate_hz:.9f}".rstrip("0").rstrip(".")
        return self.raw_backup_dir / f"slowest-grid_{rate_label}hz.xdf"


class ArtifactStore:
    """Reserves and reopens immutable study/participant/session paths."""

    def __init__(self, saved_results_root: Path) -> None:
        self.root = Path(saved_results_root)

    def paths_for(self, identity: SessionIdentity) -> ArtifactPaths:
        session_root = (
            self.root
            / identity.study_component
            / "participants"
            / identity.participant_component
            / "sessions"
            / identity.session_component
        )
        return ArtifactPaths(root=session_root, identity=identity)

    def reserve(self, identity: SessionIdentity) -> ArtifactPaths:
        """Create or idempotently reopen the directory bound to ``identity``.

        The binding file is written before any data subdirectory is exposed.
        An existing directory with a different identity is never reused.
        """

        paths = self.paths_for(identity)
        paths.root.mkdir(parents=True, exist_ok=True)
        paths.meta_dir.mkdir(parents=True, exist_ok=True)
        expected = identity.as_dict()
        if paths.identity_file.exists():
            try:
                actual = json.loads(paths.identity_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise RuntimeError(f"Session identity is unreadable: {paths.identity_file}") from error
            if actual != expected:
                raise RuntimeError(f"Session directory is bound to another identity: {paths.root}")
        else:
            atomic_write_json(paths.identity_file, expected)

        # These are the only mutable artifact containers.  Answers/meta result
        # files are published atomically by the finalization service.
        for directory in (
            paths.raw_plugins_dir,
            paths.raw_backup_dir,
            paths.derived_dir,
            paths.meta_logs_dir,
            paths.answers_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return paths
