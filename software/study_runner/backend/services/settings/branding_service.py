"""Operator-supplied branding: the research-group mark and any funder logos.

The participant waiting slide is the one screen a study subject looks at before
anything happens, so it carries the institution's identity. Which institution
that is must not be a code change, so the assets are uploaded and stored beside
the other machine settings.

Everything that decides *what is allowed* lives here; the route layer only moves
bytes. In particular, an asset is only ever resolved through the manifest -
never by joining a caller-supplied name onto the branding directory - which is
what keeps the serve endpoint free of path traversal.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any
import uuid

from study_runner.shared.atomic_io import atomic_write_bytes, atomic_write_json

MANIFEST_NAME = "branding.json"

GROUP_SLOT = "group"
FUNDER_SLOT_PREFIX = "funder:"

# Raster and vector marks only. An uploaded SVG is untrusted input, so the serve
# route sends it with nosniff and the pages render it through <img>, where a
# script inside the file cannot execute.
ALLOWED_SUFFIXES = {".svg": "image/svg+xml",
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".webp": "image/webp"}

MAX_ASSET_BYTES = 1024 * 1024
MAX_FUNDERS = 6
MAX_ALT_LENGTH = 200

_FUNDER_SLOT = re.compile(r"^funder:([0-9a-f]{8,32})$")


class BrandingError(ValueError):
    """An upload or a slot reference the operator can be told about."""


@dataclass(frozen=True)
class BrandingAsset:
    slot: str
    file: str
    alt: str

    def as_dict(self) -> dict[str, str]:
        return {"slot": self.slot, "file": self.file, "alt": self.alt}


def load_manifest(branding_dir: Path) -> dict[str, Any]:
    """Return the stored manifest, or an empty one when nothing is configured."""
    path = branding_dir / MANIFEST_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"group": None, "funders": []}
    return _normalize(raw)


def public_manifest(branding_dir: Path) -> dict[str, Any]:
    """The shape the pages consume: slots plus alt text, never filesystem paths."""
    manifest = load_manifest(branding_dir)
    group = manifest.get("group")
    return {
        "group": {"slot": GROUP_SLOT, "alt": group.get("alt", "")} if group else None,
        "funders": [
            {"slot": f"{FUNDER_SLOT_PREFIX}{entry['id']}", "alt": entry.get("alt", "")}
            for entry in manifest.get("funders", [])
        ],
    }


def resolve_asset(branding_dir: Path, slot: str) -> tuple[Path, str]:
    """Map a slot to a stored file, or raise. The only way to reach an asset."""
    manifest = load_manifest(branding_dir)
    entry = _find_entry(manifest, slot)
    if entry is None:
        raise BrandingError(f"No branding asset is configured for '{slot}'.")

    # The name comes from the manifest we wrote, but resolve and re-check anyway:
    # a hand-edited manifest must not be able to read outside the directory.
    path = (branding_dir / entry["file"]).resolve()
    if path.parent != branding_dir.resolve() or not path.is_file():
        raise BrandingError(f"The branding asset for '{slot}' is missing.")
    suffix = path.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise BrandingError(f"The branding asset for '{slot}' has an unsupported type.")
    return path, ALLOWED_SUFFIXES[suffix]


def store_asset(branding_dir: Path, slot: str, filename: str, payload: bytes, alt: str = "") -> dict[str, Any]:
    """Validate and write one asset, replacing whatever occupied the slot."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        allowed = ", ".join(sorted(ALLOWED_SUFFIXES))
        raise BrandingError(f"'{filename}' is not a supported image. Use one of: {allowed}.")
    if not payload:
        raise BrandingError("The uploaded file is empty.")
    if len(payload) > MAX_ASSET_BYTES:
        raise BrandingError("The logo is larger than the 1 MB limit.")

    manifest = load_manifest(branding_dir)
    alt_text = str(alt or "").strip()[:MAX_ALT_LENGTH]
    branding_dir.mkdir(parents=True, exist_ok=True)

    if slot == GROUP_SLOT:
        previous = manifest.get("group")
        stored_name = f"group-{uuid.uuid4().hex[:8]}{suffix}"
        atomic_write_bytes(branding_dir / stored_name, payload)
        manifest["group"] = {"file": stored_name, "alt": alt_text}
        _discard_file(branding_dir, previous)
    elif slot == "funder":
        if len(manifest["funders"]) >= MAX_FUNDERS:
            raise BrandingError(f"At most {MAX_FUNDERS} funder logos can be shown.")
        funder_id = uuid.uuid4().hex[:8]
        stored_name = f"funder-{funder_id}{suffix}"
        atomic_write_bytes(branding_dir / stored_name, payload)
        manifest["funders"].append({"id": funder_id, "file": stored_name, "alt": alt_text})
    else:
        raise BrandingError(f"Unknown branding slot '{slot}'.")

    atomic_write_json(branding_dir / MANIFEST_NAME, manifest)
    return public_manifest(branding_dir)


def remove_asset(branding_dir: Path, slot: str) -> dict[str, Any]:
    """Drop one slot and delete the file it pointed at."""
    manifest = load_manifest(branding_dir)

    if slot == GROUP_SLOT:
        if manifest.get("group") is None:
            raise BrandingError("No group logo is configured.")
        _discard_file(branding_dir, manifest["group"])
        manifest["group"] = None
    else:
        match = _FUNDER_SLOT.match(slot)
        if match is None:
            raise BrandingError(f"Unknown branding slot '{slot}'.")
        funder_id = match.group(1)
        remaining = [entry for entry in manifest["funders"] if entry["id"] != funder_id]
        if len(remaining) == len(manifest["funders"]):
            raise BrandingError("That funder logo is no longer configured.")
        for entry in manifest["funders"]:
            if entry["id"] == funder_id:
                _discard_file(branding_dir, entry)
        manifest["funders"] = remaining

    atomic_write_json(branding_dir / MANIFEST_NAME, manifest)
    return public_manifest(branding_dir)


def _find_entry(manifest: dict[str, Any], slot: str) -> dict[str, Any] | None:
    if slot == GROUP_SLOT:
        return manifest.get("group")
    match = _FUNDER_SLOT.match(slot)
    if match is None:
        return None
    return next((e for e in manifest["funders"] if e["id"] == match.group(1)), None)


def _discard_file(branding_dir: Path, entry: dict[str, Any] | None) -> None:
    """Delete a replaced asset. A leftover file is untidy, not dangerous."""
    if not entry or not entry.get("file"):
        return
    try:
        (branding_dir / str(entry["file"])).unlink(missing_ok=True)
    except OSError:
        pass


def _normalize(raw: Any) -> dict[str, Any]:
    """Accept only well-formed entries, so a corrupt manifest degrades to empty."""
    if not isinstance(raw, dict):
        return {"group": None, "funders": []}

    group = raw.get("group")
    if not isinstance(group, dict) or not isinstance(group.get("file"), str):
        group = None
    else:
        group = {"file": group["file"], "alt": str(group.get("alt", ""))}

    funders: list[dict[str, Any]] = []
    for entry in raw.get("funders", []) if isinstance(raw.get("funders"), list) else []:
        if not isinstance(entry, dict):
            continue
        if not isinstance(entry.get("file"), str) or not isinstance(entry.get("id"), str):
            continue
        funders.append({"id": entry["id"], "file": entry["file"], "alt": str(entry.get("alt", ""))})

    return {"group": group, "funders": funders[:MAX_FUNDERS]}
