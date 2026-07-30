"""Small WebDAV client for writable Nextcloud public-share links."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import requests


SUCCESS_STATUS = {200, 201, 204, 207}
FALLBACK_STATUS = {404, 405}


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
    ) -> dict[str, Any]:
        folder = Path(local_folder)
        if not folder.is_dir():
            raise NextcloudError(f"Session folder not found: {folder}")
        endpoint = self._select_endpoint()
        remote_parts = (
            _safe_remote_segment(study_id, "study_id"),
            _safe_remote_segment(participant_id, "participant_id"),
        )
        self._ensure_collection(endpoint, remote_parts[:1])
        self._ensure_collection(endpoint, remote_parts)

        uploaded: list[dict[str, Any]] = []
        for local_file in sorted(folder.iterdir(), key=lambda path: path.name.lower()):
            if not local_file.is_file():
                continue
            remote_path = (*remote_parts, local_file.name)
            with local_file.open("rb") as file_handle:
                response = self._request(
                    "PUT",
                    _remote_url(endpoint, remote_path),
                    data=file_handle,
                    headers={"Content-Type": "application/octet-stream"},
                )
            if response.status_code not in {200, 201, 204}:
                raise self._response_error("upload file", response)
            uploaded.append({"name": local_file.name, "size": local_file.stat().st_size})

        return {
            "ok": True,
            "study_id": study_id,
            "participant_id": participant_id,
            "uploaded": uploaded,
        }

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
