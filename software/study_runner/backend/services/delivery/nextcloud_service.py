"""Small WebDAV client for writable Nextcloud public-share links."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import requests


SUCCESS_STATUS = {200, 201, 204, 207}
FALLBACK_STATUS = {404, 405}
LOCAL_ONLY_OPERATIONAL_FILES = {
    "worker-state.json",
    "worker-commands.json",
    "recording-lease.json",
    "finalization-state.json",
    ".submission-commit.json",
}
COMPLETION_MARKERS = {"COMPLETE.json", "ATTENTION_REQUIRED.json"}
PRELIMINARY_ATTENTION_ARTIFACTS = {
    "result.json",
    "card-summary.json",
    "manifest.json",
    "checksums.sha256",
}


class NextcloudError(RuntimeError):
    """Plain-language failure safe to surface without credentials."""


@dataclass(frozen=True)
class NextcloudShare:
    base_url: str
    token: str


def parse_share_link(url: str) -> tuple[str, str]:
    """Return the Nextcloud installation base URL and public-share token."""
    normalized = str(url or "").strip()
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Enter a complete Nextcloud share link.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("The Nextcloud share link must not contain credentials, query parameters, or a fragment.")

    path_parts = [part for part in parsed.path.split("/") if part]
    try:
        share_index = len(path_parts) - 2
        if path_parts[share_index] != "s":
            raise ValueError
        token = path_parts[share_index + 1]
    except (IndexError, ValueError):
        raise ValueError("Expected a Nextcloud public share link ending in /s/<token>.") from None
    if not token or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in token):
        raise ValueError("The Nextcloud share token is invalid.")

    base_parts = path_parts[:share_index]
    if base_parts and base_parts[-1] == "index.php":
        base_parts.pop()
    base_path = "/" + "/".join(base_parts) if base_parts else ""
    base_url = urlunsplit((parsed.scheme, parsed.netloc, base_path, "", "")).rstrip("/")
    return base_url, token


class NextcloudPublicShareClient:
    def __init__(
        self,
        share_link: str,
        *,
        password: str = "",
        timeout_seconds: int = 30,
        session: Any = None,
    ) -> None:
        base_url, token = parse_share_link(share_link)
        self.share = NextcloudShare(base_url=base_url, token=token)
        self.password = str(password or "")
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.session = session or requests.Session()
        self._endpoint: str | None = None

    def test_connection(self) -> dict[str, Any]:
        endpoint = self._select_endpoint()
        return {
            "ok": True,
            "endpoint": "dav" if "/public.php/dav/" in endpoint else "legacy_webdav",
            "message": "Nextcloud share is reachable.",
        }

    def upload_session_folder(
        self,
        local_folder: Path,
        *,
        study_id: str,
        participant_id: str,
        session_relative_path: str = "",
    ) -> dict[str, Any]:
        folder = Path(local_folder)
        if not folder.is_dir():
            raise NextcloudError(f"Session folder not found: {folder}")
        endpoint = self._select_endpoint()
        remote_parts = (
            _session_remote_parts(session_relative_path)
            if session_relative_path
            else (
                _safe_remote_segment(study_id, "study_id"),
                _safe_remote_segment(participant_id, "participant_id"),
            )
        )
        ensured: set[tuple[str, ...]] = set()
        for depth in range(1, len(remote_parts) + 1):
            self._ensure_collection_once(endpoint, remote_parts[:depth], ensured)

        uploaded: list[dict[str, Any]] = []
        remote_sha256: dict[str, str] = {}
        local_files = [
            path
            for path in folder.rglob("*")
            if path.is_file()
            and path.name not in LOCAL_ONLY_OPERATIONAL_FILES
            and not path.name.endswith(".tmp")
            and "logs" not in path.relative_to(folder).parts
        ]
        local_markers = {path.name for path in local_files if path.name in COMPLETION_MARKERS}
        if len(local_markers) > 1:
            raise NextcloudError("Session contains conflicting completion markers.")
        if "ATTENTION_REQUIRED.json" in local_markers:
            # These files are not scientifically final until an operator has
            # confirmed a degraded completion. Raw/merged evidence and the
            # attention marker are immutable and uploaded immediately; the
            # final JSON/manifest generation follows later if confirmed.
            local_files = [
                path
                for path in local_files
                if path.name not in PRELIMINARY_ATTENTION_ARTIFACTS
            ]
        # A consumer must never observe COMPLETE/ATTENTION before every
        # immutable artifact is present and hash-verified.
        local_files.sort(
            key=lambda path: (
                path.name in COMPLETION_MARKERS,
                path.relative_to(folder).as_posix().lower(),
            )
        )
        for local_file in local_files:
            relative = local_file.relative_to(folder)
            relative_parts = tuple(
                _safe_remote_segment(part, "session artifact path")
                for part in relative.parts
            )
            for depth in range(1, len(relative_parts)):
                self._ensure_collection_once(
                    endpoint,
                    (*remote_parts, *relative_parts[:depth]),
                    ensured,
                )
            remote_path = (*remote_parts, *relative_parts)
            digest = _sha256_file(local_file)
            existing_hash = self._read_remote_sha256(endpoint, remote_path)
            if existing_hash == digest:
                relative_name = relative.as_posix()
                remote_sha256[relative_name] = digest
                uploaded.append(
                    {
                        "name": local_file.name,
                        "path": relative_name,
                        "size": local_file.stat().st_size,
                        "sha256": digest,
                        "skipped_existing": True,
                    }
                )
                continue
            if existing_hash is not None and local_file.name not in COMPLETION_MARKERS:
                raise NextcloudError(
                    f"Immutable Nextcloud artifact already exists with different content: {relative.as_posix()}."
                )
            if local_file.name in COMPLETION_MARKERS:
                obsolete = (
                    "ATTENTION_REQUIRED.json"
                    if local_file.name == "COMPLETE.json"
                    else "COMPLETE.json"
                )
                response = self._request(
                    "DELETE",
                    _remote_url(endpoint, (*remote_parts, obsolete)),
                )
                if response.status_code not in {204, 404}:
                    raise self._response_error("remove obsolete completion marker", response)
            with local_file.open("rb") as file_handle:
                response = self._request(
                    "PUT",
                    _remote_url(endpoint, remote_path),
                    data=file_handle,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "X-Hash": f"sha256:{digest}",
                        "OC-Checksum": f"SHA256:{digest}",
                    },
                )
            if response.status_code not in {200, 201, 204}:
                raise self._response_error("upload file", response)
            verified = self._verify_remote_sha256(endpoint, remote_path, digest, response)
            relative_name = relative.as_posix()
            remote_sha256[relative_name] = verified
            uploaded.append(
                {
                    "name": local_file.name,
                    "path": relative_name,
                    "size": local_file.stat().st_size,
                    "sha256": verified,
                }
            )

        return {
            "ok": True,
            "study_id": study_id,
            "participant_id": participant_id,
            "remote_path": "/".join(remote_parts),
            "uploaded": uploaded,
            "remote_sha256": remote_sha256,
        }

    def _read_remote_sha256(
        self,
        endpoint: str,
        remote_path: tuple[str, ...],
    ) -> str | None:
        """Read-before-write protects immutable artifacts from overwrite."""

        response = self._request("GET", _remote_url(endpoint, remote_path), stream=True)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise self._response_error("inspect existing remote artifact", response)
        digest = hashlib.sha256()
        if hasattr(response, "iter_content"):
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    digest.update(chunk)
        else:
            digest.update(bytes(getattr(response, "content", b"")))
        return digest.hexdigest()

    def _ensure_collection_once(
        self,
        endpoint: str,
        path_parts: tuple[str, ...],
        ensured: set[tuple[str, ...]],
    ) -> None:
        if path_parts in ensured:
            return
        self._ensure_collection(endpoint, path_parts)
        ensured.add(path_parts)

    def _verify_remote_sha256(
        self,
        endpoint: str,
        remote_path: tuple[str, ...],
        expected: str,
        upload_response: Any,
    ) -> str:
        response_hash = _response_sha256(upload_response)
        if response_hash:
            if response_hash != expected:
                raise NextcloudError("Nextcloud returned a checksum which differs from the local artifact.")
            return response_hash

        # Public-share servers do not consistently echo checksum headers.
        # A GET-based byte hash is the mandatory fallback before local purge.
        response = self._request("GET", _remote_url(endpoint, remote_path), stream=True)
        if response.status_code != 200:
            raise self._response_error("verify uploaded file", response)
        digest = hashlib.sha256()
        if hasattr(response, "iter_content"):
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    digest.update(chunk)
        else:
            digest.update(bytes(getattr(response, "content", b"")))
        actual = digest.hexdigest()
        if actual != expected:
            raise NextcloudError("Nextcloud upload verification failed: remote SHA-256 differs.")
        return actual

    def _select_endpoint(self) -> str:
        if self._endpoint:
            return self._endpoint

        primary = (
            f"{self.share.base_url}/public.php/dav/files/"
            f"{quote(self.share.token, safe='')}"
        )
        primary_response = self._request(
            "PROPFIND",
            primary,
            headers={"Depth": "0"},
        )
        if primary_response.status_code in SUCCESS_STATUS:
            self._endpoint = primary
            return primary
        if primary_response.status_code not in FALLBACK_STATUS:
            raise self._response_error("connect to share", primary_response)

        legacy = f"{self.share.base_url}/public.php/webdav"
        legacy_response = self._request(
            "PROPFIND",
            legacy,
            headers={"Depth": "0"},
        )
        if legacy_response.status_code not in SUCCESS_STATUS:
            raise self._response_error("connect to share", legacy_response)
        self._endpoint = legacy
        return legacy

    def _ensure_collection(self, endpoint: str, path_parts: tuple[str, ...]) -> None:
        response = self._request("MKCOL", _remote_url(endpoint, path_parts))
        if response.status_code not in {201, 405}:
            raise self._response_error("create remote folder", response)

    def _request(self, method: str, url: str, **kwargs):
        try:
            return self.session.request(
                method,
                url,
                auth=(self.share.token, self.password),
                timeout=self.timeout_seconds,
                allow_redirects=False,
                **kwargs,
            )
        except requests.RequestException as error:
            raise NextcloudError(f"Nextcloud request failed: {error}") from error

    @staticmethod
    def _response_error(action: str, response) -> NextcloudError:
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code in {401, 403}:
            return NextcloudError("Nextcloud rejected the share token or password.")
        return NextcloudError(f"Could not {action}; Nextcloud returned HTTP {status_code}.")


def test_connection(
    share_link: str,
    *,
    password: str = "",
    timeout_seconds: int = 10,
    session: Any = None,
) -> dict[str, Any]:
    try:
        return NextcloudPublicShareClient(
            share_link,
            password=password,
            timeout_seconds=timeout_seconds,
            session=session,
        ).test_connection()
    except (NextcloudError, ValueError) as error:
        return {"ok": False, "error": str(error)}


# The name matches the "Test connection" button and the Notion adapter's API, but
# pytest collects any module-level test_* function a test module imports. This
# marker keeps it out of collection instead of forcing a less obvious API name.
test_connection.__test__ = False


def _remote_url(endpoint: str, path_parts: tuple[str, ...]) -> str:
    encoded_path = "/".join(quote(part, safe="") for part in path_parts)
    return f"{endpoint.rstrip('/')}/{encoded_path}" if encoded_path else endpoint.rstrip("/")


def _safe_remote_segment(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or normalized in {".", ".."} or "/" in normalized or "\\" in normalized:
        raise ValueError(f"Invalid {label}.")
    return normalized


def _session_remote_parts(value: str) -> tuple[str, ...]:
    normalized = str(value or "").replace("\\", "/").strip("/")
    raw_parts = tuple(part for part in normalized.split("/") if part)
    if len(raw_parts) != 5 or raw_parts[1] != "participants" or raw_parts[3] != "sessions":
        raise ValueError(
            "session_relative_path must be <study>/participants/<participant>/sessions/<session>."
        )
    return tuple(_safe_remote_segment(part, "session_relative_path") for part in raw_parts)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _response_sha256(response: Any) -> str | None:
    headers = getattr(response, "headers", {}) or {}
    for key in ("X-Hash", "OC-Checksum"):
        raw = str(headers.get(key) or headers.get(key.lower()) or "").strip()
        if not raw:
            continue
        normalized = raw.replace("=", ":", 1)
        algorithm, separator, digest = normalized.partition(":")
        if separator and algorithm.strip().lower() == "sha256":
            candidate = digest.strip().lower()
            if len(candidate) == 64 and all(character in "0123456789abcdef" for character in candidate):
                return candidate
    return None
