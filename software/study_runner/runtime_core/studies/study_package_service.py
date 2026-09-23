"""Portable study packages: one ``.study-runner`` file with the study and its images.

A package is a zip archive::

    study.json            the study config (same JSON the editor saves)
    manifest.json         schema, and the SHA-256 of every other member
    assets/<asset_id>     each image the study refers to

Older ``.study-runner`` files are plain JSON; ``read_package`` still accepts
them, so every existing study keeps importing unchanged.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any, Mapping
import zipfile

from study_runner.runtime_core.studies.study_assets_service import (
    ASSET_ID,
    MAX_ASSET_BYTES,
    StudyAssetError,
    asset_id_for,
    referenced_assets,
    resolve_asset,
    store_asset,
)

PACKAGE_SCHEMA = "study-runner/study-package/v1"
STUDY_MEMBER = "study.json"
MANIFEST_MEMBER = "manifest.json"
ASSET_PREFIX = "assets/"
MAX_PACKAGE_BYTES = 200 * 1024 * 1024
MAX_STUDY_JSON_BYTES = 5 * 1024 * 1024
MAX_MEMBERS = 500


class StudyPackageError(ValueError):
    """A package the operator can be told is unusable, and why."""


def build_package(saved_studies_dir: Path, config: Mapping[str, Any]) -> bytes:
    """Return a deterministic zip with the study and every image it uses."""
    study_bytes = json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8")
    members: dict[str, bytes] = {STUDY_MEMBER: study_bytes}
    for asset_id in referenced_assets(config):
        try:
            path, _mime = resolve_asset(saved_studies_dir, asset_id)
        except StudyAssetError as error:
            raise StudyPackageError(f"Image {asset_id} is missing on this computer.") from error
        members[f"{ASSET_PREFIX}{asset_id}"] = path.read_bytes()
    manifest = {
        "schema": PACKAGE_SCHEMA,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(members.items())},
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in [(MANIFEST_MEMBER, json.dumps(manifest, indent=2).encode("utf-8")), *sorted(members.items())]:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def read_package(data: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Return ``(config, {asset_id: bytes})`` from a package or a plain JSON study."""
    if len(data) > MAX_PACKAGE_BYTES:
        raise StudyPackageError("The study file is too large.")
    if not data.startswith(b"PK"):
        return _parse_study_json(data), {}
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise StudyPackageError("The study package is not a valid archive.") from error
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS:
            raise StudyPackageError("The study package contains too many files.")
        names = {info.filename for info in infos}
        if len(names) != len(infos):
            raise StudyPackageError("The study package contains duplicate files.")
        for info in infos:
            if not (info.filename in {STUDY_MEMBER, MANIFEST_MEMBER} or _is_asset_member(info.filename)):
                raise StudyPackageError(f"Unexpected file in study package: {info.filename}")
            limit = MAX_STUDY_JSON_BYTES if not _is_asset_member(info.filename) else MAX_ASSET_BYTES
            if info.file_size > limit:
                raise StudyPackageError(f"{info.filename} is too large.")
        if STUDY_MEMBER not in names or MANIFEST_MEMBER not in names:
            raise StudyPackageError("The study package has no study.json or manifest.json.")
        manifest = _parse_json_object(_read_member(archive, MANIFEST_MEMBER, MAX_STUDY_JSON_BYTES), MANIFEST_MEMBER)
        if manifest.get("schema") != PACKAGE_SCHEMA or not isinstance(manifest.get("files"), dict):
            raise StudyPackageError("The study package manifest has an unsupported format.")
        declared = manifest["files"]
        if set(declared) != names - {MANIFEST_MEMBER}:
            raise StudyPackageError("The study package manifest does not list exactly its files.")
        contents: dict[str, bytes] = {}
        for name in declared:
            limit = MAX_ASSET_BYTES if _is_asset_member(name) else MAX_STUDY_JSON_BYTES
            content = _read_member(archive, name, limit)
            if hashlib.sha256(content).hexdigest() != declared[name]:
                raise StudyPackageError(f"{name} is damaged (checksum mismatch).")
            contents[name] = content
    config = _parse_study_json(contents.pop(STUDY_MEMBER))
    assets: dict[str, bytes] = {}
    for name, content in contents.items():
        asset_id = name[len(ASSET_PREFIX):]
        try:
            actual_id = asset_id_for(content)
        except StudyAssetError as error:
            raise StudyPackageError(f"{name}: {error}") from error
        if actual_id != asset_id:
            raise StudyPackageError(f"{name} does not match its content.")
        assets[asset_id] = content
    missing = [asset_id for asset_id in referenced_assets(config) if asset_id not in assets]
    if missing:
        raise StudyPackageError("The study package is missing images: " + ", ".join(missing[:3]))
    return config, assets


def store_package_assets(saved_studies_dir: Path, assets: Mapping[str, bytes]) -> None:
    for content in assets.values():
        store_asset(saved_studies_dir, content)


def _is_asset_member(name: str) -> bool:
    return name.startswith(ASSET_PREFIX) and bool(ASSET_ID.fullmatch(name[len(ASSET_PREFIX):]))


def _read_member(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    # Read with a hard cap: the declared size in the zip header is not trusted.
    with archive.open(name) as handle:
        content = handle.read(limit + 1)
    if len(content) > limit:
        raise StudyPackageError(f"{name} is too large.")
    return content


def _parse_study_json(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_STUDY_JSON_BYTES:
        raise StudyPackageError("The study file is too large.")
    return _parse_json_object(data, "The study file")


def _parse_json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as error:
        raise StudyPackageError(f"{label} is not valid JSON.") from error
    if not isinstance(value, dict):
        raise StudyPackageError(f"{label} must contain a JSON object.")
    return value
