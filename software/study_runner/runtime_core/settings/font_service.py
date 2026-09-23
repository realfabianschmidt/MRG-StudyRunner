"""Operator-chosen fonts for headings and body text (admin and participant pages).

Each slot (``heading``, ``body``) either uses a built-in stack or one uploaded
font file. Uploaded fonts live beside the logos in ``settings/branding/fonts``,
which is local machine state and never part of a release archive; the operator
is responsible for the rights to a font they upload.

As with logos, a font is only ever resolved through the manifest, never by
joining a caller-supplied name onto the directory.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import uuid

from study_runner.shared.atomic_io import atomic_write_bytes, atomic_write_json

FONTS_DIRNAME = "fonts"
MANIFEST_NAME = "fonts.json"
SLOTS = ("heading", "body")
BUILTIN_CHOICES = ("default", "geist", "system", "serif")
CHOICES = (*BUILTIN_CHOICES, "uploaded")
MAX_FONT_BYTES = 5 * 1024 * 1024
MAX_NAME_LENGTH = 120
FORMATS = {  # detected type -> (suffix, MIME type)
    "woff2": (".woff2", "font/woff2"),
    "woff": (".woff", "font/woff"),
    "ttf": (".ttf", "font/ttf"),
    "otf": (".otf", "font/otf"),
}


class FontError(ValueError):
    """An upload or choice the operator can be told about."""


def fonts_dir(branding_dir: Path) -> Path:
    return Path(branding_dir) / FONTS_DIRNAME


def detect_format(data: bytes) -> str:
    """Identify the font by its signature, never by the file name."""
    head = data[:4]
    if head == b"wOF2":
        return "woff2"
    if head == b"wOFF":
        return "woff"
    if head == b"OTTO":
        return "otf"
    if head in (b"\x00\x01\x00\x00", b"true"):
        return "ttf"
    raise FontError("Only WOFF2, WOFF, TTF, and OTF font files can be used.")


def load_manifest(branding_dir: Path) -> dict[str, Any]:
    try:
        raw = json.loads((fonts_dir(branding_dir) / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return {slot: _normalize_slot(raw.get(slot) if isinstance(raw, dict) else None) for slot in SLOTS}


def public_manifest(branding_dir: Path) -> dict[str, Any]:
    """What the pages need: the choice per slot and, for uploads, a name to show."""
    manifest = load_manifest(branding_dir)
    result = {}
    for slot in SLOTS:
        entry = manifest[slot]
        has_upload = bool(entry.get("file")) and _file_path(branding_dir, entry) is not None
        choice = entry["choice"] if entry["choice"] != "uploaded" or has_upload else "default"
        result[slot] = {
            "choice": choice,
            "has_upload": has_upload,
            "name": entry.get("name", "") if has_upload else "",
            # Changes whenever the file changes, so browsers never keep a stale font.
            "version": entry.get("file", "") if has_upload else "",
        }
    return result


def resolve_font(branding_dir: Path, slot: str) -> tuple[Path, str]:
    if slot not in SLOTS:
        raise FontError(f"Unknown font slot '{slot}'.")
    entry = load_manifest(branding_dir)[slot]
    path = _file_path(branding_dir, entry)
    if path is None:
        raise FontError(f"No font is uploaded for '{slot}'.")
    return path, FORMATS[entry["format"]][1]


def store_font(branding_dir: Path, slot: str, filename: str, payload: bytes) -> dict[str, Any]:
    """Validate and store a font for a slot, and select it."""
    if slot not in SLOTS:
        raise FontError(f"Unknown font slot '{slot}'.")
    if not payload:
        raise FontError("The uploaded file is empty.")
    if len(payload) > MAX_FONT_BYTES:
        raise FontError("The font is larger than the 5 MB limit.")
    font_format = detect_format(payload)
    manifest = load_manifest(branding_dir)
    previous = manifest[slot]
    directory = fonts_dir(branding_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stored = f"{slot}-{uuid.uuid4().hex[:8]}{FORMATS[font_format][0]}"
    atomic_write_bytes(directory / stored, payload)
    manifest[slot] = {
        "choice": "uploaded",
        "file": stored,
        "format": font_format,
        "name": Path(filename or stored).stem[:MAX_NAME_LENGTH],
    }
    _discard(branding_dir, previous)
    atomic_write_json(directory / MANIFEST_NAME, manifest)
    return public_manifest(branding_dir)


def remove_font(branding_dir: Path, slot: str) -> dict[str, Any]:
    if slot not in SLOTS:
        raise FontError(f"Unknown font slot '{slot}'.")
    manifest = load_manifest(branding_dir)
    _discard(branding_dir, manifest[slot])
    manifest[slot] = _normalize_slot(None)
    fonts_dir(branding_dir).mkdir(parents=True, exist_ok=True)
    atomic_write_json(fonts_dir(branding_dir) / MANIFEST_NAME, manifest)
    return public_manifest(branding_dir)


def set_choices(branding_dir: Path, choices: Any) -> dict[str, Any]:
    if not isinstance(choices, dict):
        raise FontError("Font choices must be a JSON object.")
    manifest = load_manifest(branding_dir)
    for slot, choice in choices.items():
        if slot not in SLOTS:
            raise FontError(f"Unknown font slot '{slot}'.")
        if choice not in CHOICES:
            raise FontError(f"Unknown font choice '{choice}'.")
        if choice == "uploaded" and _file_path(branding_dir, manifest[slot]) is None:
            raise FontError(f"Upload a font for '{slot}' first.")
        manifest[slot]["choice"] = choice
    fonts_dir(branding_dir).mkdir(parents=True, exist_ok=True)
    atomic_write_json(fonts_dir(branding_dir) / MANIFEST_NAME, manifest)
    return public_manifest(branding_dir)


def _normalize_slot(raw: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"choice": "default"}
    if not isinstance(raw, dict):
        return entry
    if raw.get("choice") in CHOICES:
        entry["choice"] = raw["choice"]
    if isinstance(raw.get("file"), str) and raw.get("format") in FORMATS:
        entry.update(file=raw["file"], format=raw["format"], name=str(raw.get("name", ""))[:MAX_NAME_LENGTH])
    return entry


def _file_path(branding_dir: Path, entry: dict[str, Any]) -> Path | None:
    name = entry.get("file")
    if not name:
        return None
    directory = fonts_dir(branding_dir).resolve()
    # A hand-edited manifest must not be able to read outside the directory.
    path = (directory / str(name)).resolve()
    if path.parent != directory or not path.is_file():
        return None
    return path


def _discard(branding_dir: Path, entry: dict[str, Any]) -> None:
    path = _file_path(branding_dir, entry)
    if path is not None:
        try:
            path.unlink()
        except OSError:
            pass
