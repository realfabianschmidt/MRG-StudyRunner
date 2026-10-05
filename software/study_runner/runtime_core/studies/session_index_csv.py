"""Regenerable, human-readable index of finished sessions in one study."""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

from study_runner.shared.atomic_io import atomic_write_bytes
from study_runner.data_core.host.artifacts import study_storage_dir
from .sessions_index_service import list_sessions


FIELDS = (
    "study_id", "participant_id", "session_id", "saved_at", "session_path",
    "status", "quality_status", "raw_xdf_present", "upload_failures",
)


def rebuild_study_index(data_dir: Path, study_id: str) -> Path:
    """Write a replaceable overview; session files remain authoritative."""
    root = Path(data_dir)
    sessions = [item for item in list_sessions(root) if item.get("study_id") == study_id]
    # The bundled demo predates the current component sanitizer. Keep its
    # index beside its actual session instead of creating a second study tree.
    components = {str(item["session_path"]).split("/", 1)[0] for item in sessions}
    if len(components) > 1:
        raise ValueError("One study has sessions in multiple storage folders.")
    study_dir = root / next(iter(components)) if components else study_storage_dir(root, study_id)
    target = study_dir / "sessions-index.csv"
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=FIELDS)
    writer.writeheader()
    for session in sessions:
        session_root = root / str(session["session_path"])
        writer.writerow({
            "study_id": session["study_id"],
            "participant_id": session["participant_id"],
            "session_id": session["session_id"],
            "saved_at": session.get("saved_at") or "",
            "session_path": session["session_path"],
            "status": session.get("status") or "",
            "quality_status": session.get("quality_status") or "",
            "raw_xdf_present": (session_root / "derived" / "session.xdf").is_file()
            or any((session_root / "raw" / "plugins").rglob("*.xdf")),
            "upload_failures": ";".join(session.get("upload_failures") or []),
        })
    atomic_write_bytes(target, output.getvalue().encode("utf-8-sig"))
    return target
