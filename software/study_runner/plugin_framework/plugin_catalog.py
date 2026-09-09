"""Discover trusted plugins and validate their runtime files and objects.

Manifest normalization lives in contracts.manifest; public imports here remain
temporarily available to existing framework callers during the architecture move.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from study_runner.contracts.manifest import (
    ACQUISITION_DELIVERIES,
    ACQUISITION_TRANSPORTS,
    CAPABILITY_ALIASES,
    DEFAULT_POLL_INTERVAL_MS,
    DEFAULT_REQUEST_TIMEOUT_MS,
    PLUGIN_API_VERSION,
    SUPPORTED_PLUGIN_API_VERSIONS,
    UI_EXTENSION_SURFACES,
    UI_VISIBILITY_AREAS,
    PluginManifestError,
    validate_admin_action_payload,
    validate_and_normalize_manifest,
)
from .extension_layout import trusted_roots, candidate_directories
from study_runner.shared.runtime_mode import is_frozen
from study_runner.contracts.plugin_api import Plugin


MANIFEST_FILENAME = "manifest.json"
PLUGIN_IGNORE_FILENAME = ".pluginignore"
DEFAULT_PACKAGE_NAME = "study_runner.extensions"


@dataclass(frozen=True)
class PluginCatalogEntry:
    """One discovered directory, valid or invalid."""

    directory: str
    status: str
    plugin_key: str | None
    manifest: dict[str, Any] | None = None
    plugin: Plugin | None = None
    errors: tuple[str, ...] = ()

    def public_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "directory": self.directory,
            "status": self.status,
            "plugin_key": self.plugin_key,
        }
        if self.manifest is not None:
            payload.update(deepcopy(self.manifest))
        if self.errors:
            payload["errors"] = list(self.errors)
        return payload


@dataclass(frozen=True)
class PluginCatalog:
    """Immutable result of one discovery pass."""

    entries: tuple[PluginCatalogEntry, ...]

    @property
    def plugins(self) -> tuple[Plugin, ...]:
        valid = [entry for entry in self.entries if entry.status == "valid" and entry.plugin]
        return tuple(entry.plugin for entry in valid if entry.plugin is not None)

    @property
    def manifests(self) -> dict[str, dict[str, Any]]:
        return {
            str(entry.plugin_key): deepcopy(entry.manifest)
            for entry in self.entries
            if entry.status == "valid" and entry.plugin_key and entry.manifest is not None
        }

    @property
    def invalid_entries(self) -> tuple[PluginCatalogEntry, ...]:
        return tuple(entry for entry in self.entries if entry.status != "valid")

    def public_payload(self) -> dict[str, Any]:
        valid = [entry.public_payload() for entry in self.entries if entry.status == "valid"]
        invalid = [entry.public_payload() for entry in self.entries if entry.status != "valid"]
        return {
            "ok": True,
            "api_version": PLUGIN_API_VERSION,
            "plugins": valid,
            "plugins_by_key": {
                str(entry["plugin_key"]): entry for entry in valid if entry.get("plugin_key")
            },
            "invalid_plugins": invalid,
        }


@dataclass
class _Candidate:
    directory: Path
    plugin_key: str | None = None
    manifest: dict[str, Any] | None = None
    errors: list[str] | None = None

    def add_error(self, message: str) -> None:
        if self.errors is None:
            self.errors = []
        self.errors.append(message)


def discover_plugin_catalog(
    plugins_dir: Path | None = None,
    *,
    package_name: str = DEFAULT_PACKAGE_NAME,
) -> PluginCatalog:
    """Discover built-in plugins below one trusted plugins directory.

    ``plugins_dir`` and ``package_name`` are parameters primarily so the
    isolation rules can be tested with a temporary package.  Production uses the
    sibling ``plugins`` package and never accepts either value from a request.
    """

    roots = ((Path(plugins_dir).resolve(), package_name),) if plugins_dir is not None else trusted_roots()
    candidates = [_read_candidate(path) for root, _ in roots for path in _plugin_directories(root)]
    _mark_duplicate_plugin_keys(candidates)
    _mark_duplicate_card_types(candidates)
    _mark_duplicate_stream_ids(candidates)
    _mark_conflicting_upload_destinations(candidates)

    entries: list[PluginCatalogEntry] = []
    for candidate in candidates:
        if candidate.errors:
            entries.append(_invalid_entry(candidate))
            continue

        try:
            # Every candidate that reached this point already normalized
            # cleanly (validate_and_normalize_manifest rejects anything
            # outside SUPPORTED_PLUGIN_API_VERSIONS = (4,)), so the process
            # host is the only path -- see Phase 3.1,
            # docs/architecture-1.0-umbau.md, for the v3 in-process import
            # path this replaced. No separate object-shape validation
            # follows: build_process_plugin derives every handler directly
            # and unconditionally from this same manifest's own capabilities
            # set, so a "capability X requires handler X" check can never
            # fail here by construction -- that check only ever caught
            # anything for a hand-written v3 Plugin object, which could
            # genuinely omit a handler while still declaring the capability.
            from .process_host import build_process_plugin

            plugin = build_process_plugin(candidate.manifest or {}, candidate.directory)
        except Exception as error:
            candidate.add_error(str(error))
            entries.append(_invalid_entry(candidate))
            continue

        entries.append(
            PluginCatalogEntry(
                directory=candidate.directory.name,
                status="valid",
                plugin_key=candidate.plugin_key,
                manifest=candidate.manifest,
                plugin=plugin,
            )
        )

    entries.sort(key=_entry_sort_key)
    return PluginCatalog(entries=tuple(entries))


def _validate_declared_driver(directory: Path, manifest: Mapping[str, Any]) -> None:
    if int(manifest.get("api_version") or 0) < 4:
        return
    entrypoint = str((manifest.get("runtime") or {}).get("entrypoint") or "")
    driver = (directory / entrypoint).resolve()
    if not driver.is_relative_to(directory.resolve()) or (not is_frozen() and not driver.is_file()):
        raise PluginManifestError(f"runtime.entrypoint does not exist: {entrypoint}")


def _plugin_directories(root: Path) -> list[Path]:
    return candidate_directories(root)


def _read_candidate(directory: Path) -> _Candidate:
    candidate = _Candidate(directory=directory, errors=[])
    manifest_path = directory / MANIFEST_FILENAME
    if not manifest_path.is_file():
        candidate.add_error(f"missing {MANIFEST_FILENAME}")
        return candidate
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = validate_and_normalize_manifest(payload, directory_name=directory.name)
        candidate.manifest = manifest
        candidate.plugin_key = str(manifest["plugin_key"])
        _validate_declared_ui_assets(directory, manifest)
        _validate_declared_driver(directory, manifest)
    except (OSError, json.JSONDecodeError, PluginManifestError) as error:
        candidate.add_error(f"invalid {MANIFEST_FILENAME}: {error}")
        return candidate
    candidate.manifest = manifest
    candidate.plugin_key = str(manifest["plugin_key"])
    return candidate


def _mark_duplicate_plugin_keys(candidates: list[_Candidate]) -> None:
    counts = Counter(candidate.plugin_key for candidate in candidates if candidate.plugin_key)
    duplicates = {key for key, count in counts.items() if count > 1}
    for candidate in candidates:
        if candidate.plugin_key in duplicates:
            candidate.add_error(f"duplicate plugin_key: {candidate.plugin_key}")


def _mark_duplicate_stream_ids(candidates: list[_Candidate]) -> None:
    owners: dict[str, list[_Candidate]] = {}
    for candidate in candidates:
        if candidate.errors or not candidate.manifest:
            continue
        for stream in candidate.manifest.get("streams", []):
            source_id = str(stream.get("source_id") or "")
            owners.setdefault(source_id, []).append(candidate)
    for source_id, matches in owners.items():
        if not source_id or len(matches) < 2:
            continue
        for candidate in matches:
            candidate.add_error(f"duplicate stream source_id: {source_id}")


def _mark_conflicting_upload_destinations(candidates: list[_Candidate]) -> None:
    owners: dict[str, list[_Candidate]] = {}
    purge_candidates: list[_Candidate] = []
    for candidate in candidates:
        if candidate.errors or not candidate.manifest:
            continue
        capability = (
            (candidate.manifest.get("capability_config") or {})
            .get("upload_destination")
        )
        if not isinstance(capability, dict):
            continue
        destination = str(capability.get("destination") or "")
        owners.setdefault(destination, []).append(candidate)
        if capability.get("purge_verified_sources") is True:
            purge_candidates.append(candidate)
    for destination, matches in owners.items():
        if not destination or len(matches) < 2:
            continue
        for candidate in matches:
            candidate.add_error(f"duplicate upload destination: {destination}")
    if len(purge_candidates) > 1:
        keys = ", ".join(
            sorted(str(candidate.plugin_key or candidate.directory.name) for candidate in purge_candidates)
        )
        for candidate in purge_candidates:
            candidate.add_error(
                "only one upload destination may declare purge_verified_sources: "
                + keys
            )


def _validate_declared_ui_assets(directory: Path, manifest: dict[str, Any]) -> None:
    plugin_root = directory.resolve()
    ui = manifest.get("ui") or {}
    declared_assets = [*((ui.get("extensions") or {}).values()), *(ui.get("assets") or [])]
    for relative_path in declared_assets:
        candidate = (plugin_root / PurePosixPath(relative_path)).resolve()
        try:
            candidate.relative_to(plugin_root)
        except ValueError as error:
            raise PluginManifestError(
                f"declared UI asset escapes plugin directory: {relative_path}"
            ) from error
        if not candidate.is_file():
            raise PluginManifestError(f"declared UI asset does not exist: {relative_path}")


def _invalid_entry(candidate: _Candidate) -> PluginCatalogEntry:
    return PluginCatalogEntry(
        directory=candidate.directory.name,
        status="invalid",
        plugin_key=candidate.plugin_key,
        manifest=candidate.manifest,
        errors=tuple(candidate.errors or ["unknown catalog error"]),
    )


def _entry_sort_key(entry: PluginCatalogEntry) -> tuple[int, int, str]:
    order = 1_000
    if entry.manifest:
        order = int((entry.manifest.get("ui") or {}).get("order", order))
    return (0 if entry.status == "valid" else 1, order, entry.directory)


def _mark_duplicate_card_types(candidates: list[_Candidate]) -> None:
    providers: dict[str, list[_Candidate]] = {}
    for candidate in candidates:
        contract = ((candidate.manifest or {}).get("capability_config") or {}).get("card_contract") or {}
        for question_type in contract.get("question_types", []):
            providers.setdefault(question_type, []).append(candidate)
    for question_type, matches in providers.items():
        if len(matches) > 1:
            for candidate in matches:
                candidate.add_error(f"duplicate card question type: {question_type}")
