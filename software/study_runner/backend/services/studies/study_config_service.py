import hashlib
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from study_runner.shared.atomic_io import atomic_write_json

from .study_plugin_config import migrate_study_plugin_config

STUDY_FILE_SUFFIXES = (".study-runner", ".json")
_STUDY_SAVE_LOCK = threading.RLock()
STUDY_TRANSACTION_SCHEMA = "study-runner/study-save-transaction/v1"


class StudyRevisionConflict(ValueError):
    """A client tried to replace a study revision it did not load."""


DEFAULT_STIMULUS_CARD: dict[str, Any] = {
    "type": "stimulus",
    "title": "Observe the material",
    "subtitle": "Pay attention to all sensory impressions. The questionnaire will appear automatically.",
    "warmup_duration_ms": 0,
    "duration_ms": 30000,
    "trigger_type": "timer",
    "trigger_content": "",
    "plugin_actions": {},
}


def normalize_config(config_data: dict[str, Any]) -> dict[str, Any]:
    """Migrate old config keys into the current card-based study structure."""
    if "stimulus_duration_ms" in config_data:
        card = dict(DEFAULT_STIMULUS_CARD)
        card["duration_ms"] = config_data.pop("stimulus_duration_ms")
        questions = config_data.get("questions", [])
        if not any(q.get("type") == "stimulus" for q in questions):
            config_data["questions"] = [card] + questions
    config_data.pop("stimulus_duration_ms", None)

    for question_data in config_data.get("questions", []):
        if (
            isinstance(question_data, dict)
            and question_data.get("type") == "choice"
            and question_data.get("multiple") is False
        ):
            question_data["type"] = "single"
            question_data.pop("multiple", None)

    return migrate_study_plugin_config(config_data)


def load_config(config_file: Path) -> dict[str, Any]:
    recover_active_study_transaction(config_file)
    return _load_config_unchecked(config_file)


def _load_config_unchecked(config_file: Path) -> dict[str, Any]:
    with config_file.open(encoding="utf-8") as file_handle:
        return normalize_config(json.load(file_handle))


def save_config(config_file: Path, config_data: dict[str, Any]) -> None:
    atomic_write_json(config_file, config_data, ensure_ascii=False)


def normalize_study_id(study_id: str) -> str:
    """The study's stable key: its filename stem, and its credential key.

    Public because per-study credentials are stored under the same normalized
    id; if the two ever disagreed, renaming a study would strand its secrets.
    """
    return "".join(c for c in study_id if c.isalnum() or c in " _-") or "unnamed"


# Kept so existing internal callers and any out-of-tree use keep working.
_normalize_study_id = normalize_study_id


def _study_paths_for_id(studies_dir: Path, study_id: str) -> list[Path]:
    safe_id = _normalize_study_id(study_id)
    return [studies_dir / f"{safe_id}{suffix}" for suffix in STUDY_FILE_SUFFIXES]


def _resolve_study_file(studies_dir: Path, study_id: str) -> Path | None:
    candidates = [path for path in _study_paths_for_id(studies_dir, study_id) if path.exists()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def list_studies(studies_dir: Path) -> list[dict[str, Any]]:
    studies_dir.mkdir(parents=True, exist_ok=True)
    latest_by_id: dict[str, dict[str, Any]] = {}

    for suffix in STUDY_FILE_SUFFIXES:
        for file_path in studies_dir.glob(f"*{suffix}"):
            if not file_path.is_file():
                continue

            study_id = file_path.stem
            modified = file_path.stat().st_mtime
            existing = latest_by_id.get(study_id)
            if existing and existing["modified"] >= modified:
                continue

            latest_by_id[study_id] = {
                "id": study_id,
                "modified": modified,
            }

    results = list(latest_by_id.values())
    results.sort(key=lambda x: x["modified"], reverse=True)
    return results


def save_study(studies_dir: Path, config_data: dict[str, Any]) -> None:
    studies_dir.mkdir(parents=True, exist_ok=True)
    study_id = config_data.get("study_id", "Unbenannte Studie").strip()
    safe_id = _normalize_study_id(study_id)
    file_path = studies_dir / f"{safe_id}.study-runner"
    save_config(file_path, config_data)


def save_active_study(
    config_file: Path,
    studies_dir: Path,
    config_data: dict[str, Any],
    *,
    expected_revision: str | None = None,
) -> str:
    """Serialize the archive/current pair and preserve the archive first.

    The two filenames cannot be replaced as one filesystem operation. Writing
    the recoverable saved-study copy first means a crash or second-write error
    can leave an older active projection, but cannot leave the only copy of the
    newly submitted study unarchived. The process lock also prevents concurrent
    requests from interleaving the two files into mismatched revisions.
    """

    return save_active_study_revision(
        config_file,
        studies_dir,
        config_data,
        expected_revision=expected_revision,
    )


def save_active_study_revision(
    config_file: Path,
    studies_dir: Path,
    config_data: dict[str, Any],
    *,
    expected_revision: str | None,
) -> str:
    """Atomically write each projection under a recoverable CAS transaction."""

    with _STUDY_SAVE_LOCK:
        recover_active_study_transaction(config_file)
        current_revision = None
        if config_file.is_file():
            current_revision = study_config_revision(_load_config_unchecked(config_file))
        normalized_expected = str(expected_revision or "").strip().lower() or None
        if normalized_expected is not None and normalized_expected != current_revision:
            raise StudyRevisionConflict(
                "The active study changed after this editor loaded it; reload before saving."
            )

        revision = study_config_revision(config_data)
        safe_id = normalize_study_id(str(config_data.get("study_id") or ""))
        archive_path = studies_dir / f"{safe_id}.study-runner"
        marker_path = study_transaction_path(config_file)
        marker: dict[str, Any] = {
            "schema": STUDY_TRANSACTION_SCHEMA,
            "transaction_id": uuid.uuid4().hex,
            "status": "prepared",
            "started_at_epoch": time.time(),
            "updated_at_epoch": time.time(),
            "active_path": str(Path(config_file).resolve(strict=False)),
            "archive_path": str(Path(archive_path).resolve(strict=False)),
            "previous_active_revision": current_revision,
            "revision": revision,
        }
        _write_transaction_marker(marker_path, marker)
        save_config(archive_path, config_data)
        marker.update(status="archive_written", updated_at_epoch=time.time())
        _write_transaction_marker(marker_path, marker)
        save_config(config_file, config_data)
        marker.update(
            status="committed",
            committed_at_epoch=time.time(),
            updated_at_epoch=time.time(),
        )
        _write_transaction_marker(marker_path, marker)
        return revision


def study_config_revision(config_data: dict[str, Any]) -> str:
    """Return the stable compare-and-swap revision of a stored study."""

    # Revision the same normalized document that a subsequent load exposes;
    # this also keeps pre-migration study files comparable during the v3/v4
    # compatibility release.
    normalized = normalize_config(json.loads(json.dumps(config_data)))
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def study_transaction_path(config_file: Path) -> Path:
    config_file = Path(config_file)
    return config_file.with_name(f".{config_file.name}.transaction.json")


def _write_transaction_marker(path: Path, marker: dict[str, Any]) -> None:
    atomic_write_json(path, marker, ensure_ascii=True, trailing_newline=True)


def recover_active_study_transaction(config_file: Path) -> bool:
    """Finish an archive-first save interrupted before replacing ``current``."""

    marker_path = study_transaction_path(config_file)
    if not marker_path.is_file():
        return False
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Study save transaction marker is unreadable: {error}") from error
    if not isinstance(marker, dict) or marker.get("schema") != STUDY_TRANSACTION_SCHEMA:
        raise ValueError("Study save transaction marker has an unsupported schema.")
    marker_active = Path(str(marker.get("active_path") or "")).resolve(strict=False)
    if marker_active != Path(config_file).resolve(strict=False):
        raise ValueError("Study save transaction marker targets another active study file.")
    if marker.get("status") in {"committed", "recovered", "aborted"}:
        return False

    revision = str(marker.get("revision") or "").strip().lower()
    archive_path = Path(str(marker.get("archive_path") or ""))
    archive_payload: dict[str, Any] | None = None
    if archive_path.is_file():
        try:
            candidate = _load_config_unchecked(archive_path)
            if study_config_revision(candidate) == revision:
                archive_payload = candidate
        except (OSError, ValueError, json.JSONDecodeError):
            archive_payload = None

    if archive_payload is None:
        if marker.get("status") == "prepared":
            marker.update(
                status="aborted",
                recovery_reason="archive revision was not committed",
                updated_at_epoch=time.time(),
            )
            _write_transaction_marker(marker_path, marker)
            return False
        raise ValueError("Study save transaction archive is missing or has the wrong revision.")

    if config_file.is_file():
        try:
            if study_config_revision(_load_config_unchecked(config_file)) == revision:
                marker.update(status="committed", updated_at_epoch=time.time())
                _write_transaction_marker(marker_path, marker)
                return False
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    save_config(config_file, archive_payload)
    marker.update(
        status="recovered",
        recovered_at_epoch=time.time(),
        updated_at_epoch=time.time(),
    )
    _write_transaction_marker(marker_path, marker)
    return True


def load_study(studies_dir: Path, study_id: str) -> dict[str, Any]:
    file_path = _resolve_study_file(studies_dir, study_id)
    if file_path is None:
        raise FileNotFoundError(f"Study {study_id} not found.")
    return load_config(file_path)


def delete_study(studies_dir: Path, study_id: str) -> bool:
    deleted = False
    for file_path in _study_paths_for_id(studies_dir, study_id):
        if file_path.exists():
            file_path.unlink()
            deleted = True
    return deleted
