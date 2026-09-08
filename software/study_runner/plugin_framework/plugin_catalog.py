"""Discover trusted plugins and validate their runtime files and objects.

Manifest normalization lives in contracts.manifest; public imports here remain
temporarily available to existing framework callers during the architecture move.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import importlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from study_runner.contracts.manifest import (
    ACQUISITION_DELIVERIES,
    ACQUISITION_TRANSPORTS,
    CAPABILITY_ALIASES,
    DEFAULT_POLL_INTERVAL_MS,
    DEFAULT_REQUEST_TIMEOUT_MS,
    ENTRY_POINT_PATTERN,
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
# The framework and the plugins are sibling packages, so the trusted root is
# resolved from this file rather than from the caller or the process directory.
DEFAULT_PLUGINS_DIRECTORY = Path(__file__).resolve().parent.parent / "plugins"


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
    module_importer: Callable[[str], Any] = importlib.import_module,
) -> PluginCatalog:
    """Discover built-in plugins below one trusted plugins directory.

    ``plugins_dir`` and ``package_name`` are parameters primarily so the
    isolation rules can be tested with a temporary package.  Production uses the
    sibling ``plugins`` package and never accepts either value from a request.
    """

    roots = ((Path(plugins_dir).resolve(), package_name),) if plugins_dir is not None else trusted_roots()
    packages = {root.resolve(): package for root, package in roots}
    candidates = [_read_candidate(path) for root, _ in roots for path in _plugin_directories(root)]
    _mark_duplicate_plugin_keys(candidates)
    _mark_duplicate_stream_ids(candidates)
    _mark_conflicting_upload_destinations(candidates)

    entries: list[PluginCatalogEntry] = []
    for candidate in candidates:
        if candidate.errors:
            entries.append(_invalid_entry(candidate))
            continue

        try:
            if int((candidate.manifest or {}).get("api_version") or 0) >= 4:
                from .process_host import build_process_plugin

                plugin = build_process_plugin(candidate.manifest or {}, candidate.directory)
            else:
                plugin = _import_plugin(candidate, packages[candidate.directory.parent.resolve()], module_importer)
            _validate_plugin_object(plugin, candidate.manifest or {})
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


def _import_plugin(
    candidate: _Candidate,
    package_name: str,
    module_importer: Callable[[str], Any],
) -> Plugin:
    manifest = candidate.manifest or {}
    entry_point = str(manifest.get("entry_point") or "")
    match = ENTRY_POINT_PATTERN.fullmatch(entry_point)
    if match is None:
        raise PluginManifestError("entry_point is invalid")
    module_name = match.group("module")
    attribute = match.group("attribute")
    qualified_module = f"{package_name}.{candidate.directory.name}.{module_name}"
    module = module_importer(qualified_module)
    plugin = getattr(module, attribute, None)
    if not isinstance(plugin, Plugin):
        raise PluginManifestError(
            f"entry_point {entry_point!r} did not expose an Plugin"
        )
    return plugin


def _validate_plugin_object(plugin: Plugin, manifest: dict[str, Any]) -> None:
    expected = (
        ("key", "plugin_key", manifest.get("plugin_key")),
        ("config_key", "config_key", manifest.get("config_key")),
        ("category", "category", manifest.get("category")),
        ("label", "ui.label", (manifest.get("ui") or {}).get("label")),
    )
    for attribute, manifest_field, value in expected:
        if getattr(plugin, attribute) != value:
            raise PluginManifestError(
                f"plugin.{attribute} does not match manifest {manifest_field}"
            )

    capabilities = set(manifest.get("capabilities") or [])
    if "health" in capabilities and plugin.get_status is None:
        raise PluginManifestError("health capability requires plugin.get_status")
    if "runtime_control" in capabilities:
        actions = (
            (plugin.can_start, plugin.start, "start"),
            (plugin.can_stop, plugin.stop, "stop"),
            (plugin.can_restart, plugin.restart, "restart"),
        )
        if not any(enabled for enabled, _handler, _name in actions):
            raise PluginManifestError("runtime_control requires at least one enabled action")
        for enabled, handler, name in actions:
            if enabled and handler is None:
                raise PluginManifestError(f"runtime_control enables {name} without a handler")
    if "admin_actions" in capabilities and not callable(
        getattr(plugin, "run_admin_action", None)
    ):
        raise PluginManifestError("admin_actions capability requires plugin.run_admin_action")
    if "participant_actions" in capabilities and not callable(
        getattr(plugin, "run_participant_action", None)
    ):
        raise PluginManifestError(
            "participant_actions capability requires plugin.run_participant_action"
        )
    if "participant_ingest" in capabilities and not callable(
        getattr(plugin, "ingest_participant", None)
    ):
        raise PluginManifestError(
            "participant_ingest capability requires plugin.ingest_participant"
        )
    if "interval_summary" in capabilities and plugin.get_interval_summary is None:
        raise PluginManifestError(
            "interval_summary capability requires plugin.get_interval_summary"
        )
    if "sidecar_export" in capabilities and plugin.export_interval_samples is None:
        raise PluginManifestError(
            "sidecar_export capability requires plugin.export_interval_samples"
        )
    if "upload_destination" in capabilities and not callable(
        getattr(plugin, "publish_destination", None)
    ):
        raise PluginManifestError(
            "upload_destination capability requires plugin.publish_destination"
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
