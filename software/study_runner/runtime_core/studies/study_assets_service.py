"""Images that belong to a study (info cards, cover page).

Assets are content-addressed: the id is the SHA-256 of the bytes plus the file
type, e.g. ``3f2a...c9.png``. A study only stores that id, so the study
revision (a hash of the study JSON) already pins the exact image bytes, the
same image is stored once however many studies use it, and exporting a study
package can verify every file against its own name.

Everything that decides *what is allowed* lives here; routes only move bytes.
An asset is only ever resolved from an id that matches ``ASSET_ID``, which keeps
the serve endpoint free of path traversal.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

from study_runner.contracts.media_content import ASSET_ID
from study_runner.shared.atomic_io import atomic_write_bytes

ASSETS_DIRNAME = "_assets"
MAX_ASSET_BYTES = 5 * 1024 * 1024
MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "webp": "image/webp",
    "svg": "image/svg+xml",
}
# Study fields that may reference an asset. Kept explicit so an unrelated
# string can never be mistaken for a file reference.
ASSET_FIELDS = ("image_asset",)


class StudyAssetError(ValueError):
    """An upload or reference the operator can be told about."""


def assets_dir(saved_studies_dir: Path) -> Path:
    return Path(saved_studies_dir) / ASSETS_DIRNAME


def detect_type(data: bytes) -> str:
    """Return the file type from the content itself, never from a file name."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith(b"<?xml") or head.startswith(b"<svg"):
        if b"<svg" not in data[:8192].lower():
            raise StudyAssetError("The SVG file has no <svg> element.")
        lowered = data.lower()
        # Pages render images through <img>, where scripts never run; refusing
        # them anyway keeps a downloaded study package harmless when opened
        # directly in a browser.
        if b"<script" in lowered or b"javascript:" in lowered or b"<foreignobject" in lowered:
            raise StudyAssetError("SVG images must not contain scripts or embedded HTML.")
        return "svg"
    raise StudyAssetError("Only PNG, JPEG, WebP, and SVG images can be used.")


def asset_id_for(data: bytes) -> str:
    if not data:
        raise StudyAssetError("The image file is empty.")
    if len(data) > MAX_ASSET_BYTES:
        raise StudyAssetError(f"Images may be at most {MAX_ASSET_BYTES // (1024 * 1024)} MB.")
    return f"{hashlib.sha256(data).hexdigest()}.{detect_type(data)}"


def store_asset(saved_studies_dir: Path, data: bytes) -> str:
    """Validate and store image bytes; return their content-addressed id."""
    asset_id = asset_id_for(data)
    target = assets_dir(saved_studies_dir) / asset_id
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target, data)
    return asset_id


def resolve_asset(saved_studies_dir: Path, asset_id: str) -> tuple[Path, str]:
    """Return the file and MIME type of a stored asset, or raise."""
    if not isinstance(asset_id, str) or not ASSET_ID.fullmatch(asset_id):
        raise StudyAssetError("Unknown image.")
    path = assets_dir(saved_studies_dir) / asset_id
    if not path.is_file():
        raise StudyAssetError("Unknown image.")
    return path, MIME_TYPES[asset_id.rsplit(".", 1)[1]]


def referenced_assets(config: Mapping[str, Any]) -> list[str]:
    """Every asset id a study config refers to, in first-use order."""
    found: list[str] = []

    def collect(item: Any) -> None:
        if not isinstance(item, Mapping):
            return
        for field in ASSET_FIELDS:
            value = item.get(field)
            if isinstance(value, str) and value and value not in found:
                found.append(value)

    for question in config.get("questions") or []:
        collect(question)
    settings = config.get("study_settings") or {}
    if isinstance(settings, Mapping):
        collect(settings.get("cover_page"))
    return found


def missing_assets(saved_studies_dir: Path, asset_ids: Iterable[str]) -> list[str]:
    missing = []
    for asset_id in asset_ids:
        try:
            resolve_asset(saved_studies_dir, asset_id)
        except StudyAssetError:
            missing.append(asset_id)
    return missing


def require_assets(saved_studies_dir: Path, config: Mapping[str, Any]) -> None:
    """Refuse a study that points at an image this installation does not have."""
    missing = missing_assets(saved_studies_dir, referenced_assets(config))
    if missing:
        raise StudyAssetError(
            "The study refers to images that are not stored here: "
            + ", ".join(missing[:3])
            + (" …" if len(missing) > 3 else "")
            + ". Import the study as a .study-runner package to bring its images along."
        )
