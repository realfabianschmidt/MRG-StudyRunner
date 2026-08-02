"""Checksums, provenance, completion markers, and guarded source cleanup."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Iterable

from study_runner.backend.recording.artifacts import ArtifactPaths, SessionIdentity, sha256_file

from .atomic_io import atomic_write_bytes, atomic_write_json


MANIFEST_SCHEMA = "study-runner/artifact-manifest/v1"
COMPLETE_MARKER = "COMPLETE.json"
ATTENTION_MARKER = "ATTENTION_REQUIRED.json"
_MUTABLE_OR_SELF_REFERENTIAL = {
    "finalization-state.json",
    "manifest.json",
    "checksums.sha256",
    COMPLETE_MARKER,
    ATTENTION_MARKER,
    ".submission-commit.json",
}


class ArtifactManifestError(RuntimeError):
    pass


class ArtifactManifestStore:
    """Publish a deterministic inventory for one immutable session tree."""

    def write(
        self,
        paths: ArtifactPaths,
        *,
        identity: SessionIdentity,
        quality_status: str,
        merge_parity: bool,
        provenance: dict[str, Any] | None = None,
        warnings: Iterable[str] = (),
    ) -> dict[str, Any]:
        artifacts = [self._metadata(paths.root, path) for path in self._artifact_files(paths.root)]
        checksum_lines = [f"{item['sha256']}  {item['path']}" for item in artifacts]
        atomic_write_bytes(
            paths.root / "checksums.sha256",
            (("\n".join(checksum_lines) + "\n") if checksum_lines else "").encode("utf-8"),
        )
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "generated_at": _utc_now(),
            "identity": identity.as_dict(),
            "quality_status": str(quality_status),
            "merge_parity": bool(merge_parity),
            "warnings": [str(value) for value in warnings if str(value).strip()],
            "provenance": dict(provenance or {}),
            "artifacts": artifacts,
        }
        atomic_write_json(paths.root / "manifest.json", manifest)
        return manifest

    def publish_marker(
        self,
        paths: ArtifactPaths,
        *,
        status: str,
        job_id: str,
        details: dict[str, Any] | None = None,
    ) -> Path:
        attention = status == "attention_required"
        destination = paths.root / (ATTENTION_MARKER if attention else COMPLETE_MARKER)
        obsolete = paths.root / (COMPLETE_MARKER if attention else ATTENTION_MARKER)
        atomic_write_json(
            destination,
            {
                "schema": "study-runner/finalization-marker/v1",
                "status": status,
                "job_id": job_id,
                "published_at": _utc_now(),
                "details": dict(details or {}),
            },
        )
        try:
            obsolete.unlink(missing_ok=True)
        except OSError:
            # A stale opposite marker is less dangerous than losing the new
            # durable marker. The next state transition retries cleanup.
            pass
        return destination

    def purge_plugin_xdfs(
        self,
        paths: ArtifactPaths,
        *,
        remote_sha256: dict[str, str],
        session_status: str,
        merge_parity: bool,
    ) -> dict[str, Any]:
        """Delete local native sources only after exact remote verification.

        The original checksums stay in ``manifest.json`` and
        ``checksums.sha256`` as provenance.  Only ``raw/plugins/**/*.xdf`` is
        eligible; backup, merged XDF, JSON, logs, and manifests are retained.
        """

        if session_status != "completed":
            raise ArtifactManifestError("Local sources may only be purged from a completed session.")
        if not merge_parity:
            raise ArtifactManifestError("Local sources may not be purged without merge parity.")
        manifest_path = paths.root / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ArtifactManifestError("Artifact manifest is missing or unreadable.") from error

        purge_state = manifest.get("source_purge")
        if isinstance(purge_state, dict) and purge_state.get("status") in {"prepared", "running"}:
            planned = purge_state.get("planned")
            if not isinstance(planned, list):
                raise ArtifactManifestError("Prepared source-purge journal is invalid.")
        else:
            candidates = sorted(
                path for path in paths.raw_plugins_dir.rglob("*.xdf") if path.is_file()
            )
            planned = []
            for path in candidates:
                if path.is_symlink() or not path.resolve().is_relative_to(paths.raw_plugins_dir.resolve()):
                    raise ArtifactManifestError(f"Unsafe native source path: {path}.")
                relative = path.relative_to(paths.root).as_posix()
                local_hash = sha256_file(path).lower()
                manifest_entry = next(
                    (
                        artifact
                        for artifact in manifest.get("artifacts", [])
                        if artifact.get("path") == relative
                    ),
                    None,
                )
                manifest_hash = str((manifest_entry or {}).get("sha256") or "").lower()
                if (
                    not isinstance(manifest_entry, dict)
                    or manifest_entry.get("role") != "native_plugin_xdf"
                    or manifest_entry.get("local_present") is False
                    or manifest_hash != local_hash
                ):
                    raise ArtifactManifestError(
                        f"Native source changed after merge parity or is absent from the manifest: {relative}."
                    )
                remote_hash = str(remote_sha256.get(relative) or "").lower()
                if not remote_hash or remote_hash != local_hash:
                    raise ArtifactManifestError(f"Nextcloud checksum is not verified for {relative}.")
                planned.append({"path": relative, "sha256": local_hash})

            # Persist the complete, remotely verified deletion intent before
            # unlinking the first byte. After a power loss, a missing file can
            # then be reconciled as an already completed deletion instead of
            # silently disappearing from provenance.
            purge_state = {
                "status": "prepared",
                "prepared_at": _utc_now(),
                "planned": planned,
                "removed": [],
                "remote": "nextcloud",
            }
            manifest["source_purge"] = purge_state
            atomic_write_json(manifest_path, manifest)

        removed = list(dict.fromkeys(str(value) for value in purge_state.get("removed", [])))
        raw_root = paths.raw_plugins_dir.resolve()
        session_root = paths.root.resolve()
        for item in planned:
            if not isinstance(item, dict):
                raise ArtifactManifestError("Prepared source-purge entry is invalid.")
            relative = str(item.get("path") or "")
            digest = str(item.get("sha256") or "").lower()
            path = (session_root / relative).resolve()
            if (
                not relative
                or not digest
                or not path.is_relative_to(raw_root)
                or path.suffix.lower() != ".xdf"
            ):
                raise ArtifactManifestError("Prepared source-purge entry escapes native source storage.")
            manifest_entry = next(
                (
                    artifact
                    for artifact in manifest.get("artifacts", [])
                    if artifact.get("path") == relative
                ),
                None,
            )
            if (
                not isinstance(manifest_entry, dict)
                or manifest_entry.get("role") != "native_plugin_xdf"
                or str(manifest_entry.get("sha256") or "").lower() != digest
            ):
                raise ArtifactManifestError(
                    f"Prepared source-purge entry no longer matches the manifest: {relative}."
                )
            remote_hash = str(remote_sha256.get(relative) or "").lower()
            if remote_hash != digest:
                raise ArtifactManifestError(f"Nextcloud checksum is not verified for {relative}.")
            if path.exists():
                if path.is_symlink() or not path.is_file() or sha256_file(path).lower() != digest:
                    raise ArtifactManifestError(f"Local source changed before purge: {relative}.")
                path.unlink()
            if relative not in removed:
                removed.append(relative)
            for artifact in manifest.get("artifacts", []):
                if artifact.get("path") == relative and str(artifact.get("sha256") or "").lower() == digest:
                    artifact["local_present"] = False
                    artifact["remote_verified"] = True
                    artifact["remote_sha256"] = digest
            purge_state.update(status="running", removed=removed)
            atomic_write_json(manifest_path, manifest)

        purge_state.update(
            status="completed",
            completed_at=_utc_now(),
            removed=removed,
        )
        atomic_write_json(manifest_path, manifest)
        return {"removed": removed, "removed_count": len(removed)}

    def _artifact_files(self, root: Path) -> list[Path]:
        files = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if path.name in _MUTABLE_OR_SELF_REFERENTIAL:
                continue
            if relative.parts and relative.parts[0] == "logs":
                continue
            if path.name.startswith(".") or path.suffix == ".tmp":
                continue
            files.append(path)
        return sorted(files, key=lambda value: value.relative_to(root).as_posix())

    @staticmethod
    def _metadata(root: Path, path: Path) -> dict[str, Any]:
        relative = path.relative_to(root).as_posix()
        stat = path.stat()
        return {
            "path": relative,
            "role": _artifact_role(relative),
            "size_bytes": stat.st_size,
            "sha256": sha256_file(path),
            "local_present": True,
        }


def _artifact_role(relative_path: str) -> str:
    if relative_path == "submission.json":
        return "submission"
    if relative_path == "result.json":
        return "result"
    if relative_path == "card-summary.json":
        return "card_summary"
    if relative_path == "session-identity.json":
        return "session_identity"
    if relative_path == "derived/session.xdf":
        return "merged_xdf"
    if relative_path.startswith("raw/plugins/"):
        return "native_plugin_xdf"
    if relative_path.startswith("raw/backup/"):
        return "derived_backup_xdf"
    return "session_artifact"


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
