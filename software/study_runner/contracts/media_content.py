"""Shared rules for read-only participant content: a title, text, and an optional image.

Used by the info card (in its extension worker) and by the study cover page
(in the core), so both accept exactly the same fields.
"""
from __future__ import annotations

import re
from typing import Any

# Content-addressed study image: SHA-256 of the bytes plus the detected type.
ASSET_ID = re.compile(r"^[0-9a-f]{64}\.(png|jpg|webp|svg)$")
LAYOUTS = ("text", "image-left", "image-right")
MAX_TITLE_LENGTH = 200
MAX_TEXT_LENGTH = 20_000
MAX_ALT_LENGTH = 300


class MediaContentError(ValueError):
    """A content field the editor can point the operator at."""


def normalize_media_content(data: Any, *, label: str) -> dict[str, str]:
    """Return ``{title, text, image_asset, image_alt, layout}`` or raise MediaContentError."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise MediaContentError(f"{label} must be a JSON object.")
    title = _text(data.get("title"), MAX_TITLE_LENGTH, f"{label} title")
    text = _text(data.get("text"), MAX_TEXT_LENGTH, f"{label} text", strip=False).strip("\n")
    image_asset = _text(data.get("image_asset"), 80, f"{label} image")
    if image_asset and not ASSET_ID.fullmatch(image_asset):
        raise MediaContentError(f"{label} image reference is invalid.")
    image_alt = _text(data.get("image_alt"), MAX_ALT_LENGTH, f"{label} image description")
    layout = str(data.get("layout") or "text").strip()
    if layout not in LAYOUTS:
        raise MediaContentError(f"{label} layout must be one of: {', '.join(LAYOUTS)}.")
    if not image_asset:
        layout = "text"
    return {
        "title": title,
        "text": text,
        "image_asset": image_asset,
        "image_alt": image_alt,
        "layout": layout,
    }


def _text(value: Any, limit: int, label: str, *, strip: bool = True) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise MediaContentError(f"{label} must be text.")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if strip:
        normalized = normalized.strip()
    if len(normalized) > limit:
        raise MediaContentError(f"{label} may be at most {limit} characters.")
    return normalized
