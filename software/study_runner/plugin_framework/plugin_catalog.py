"""Trusted, manifest-driven discovery for the built-in plugins.

Only plugin folders shipped inside :mod:`study_runner.plugins` are
considered.  The catalog never installs packages and never imports a plugin
until its manifest has passed the API-v3 checks.  A broken folder therefore
becomes a visible catalog entry instead of preventing the application from
starting.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import importlib
import json
import math
from pathlib import Path
from pathlib import PurePosixPath
import re
from typing import Any, Callable

from .plugin_api import Plugin, PLUGIN_API_VERSION


MANIFEST_FILENAME = "manifest.json"
PLUGIN_IGNORE_FILENAME = ".pluginignore"
DEFAULT_PACKAGE_NAME = "study_runner.plugins"
# The framework and the plugins are sibling packages, so the trusted root is
# resolved from this file rather than from the caller or the process directory.
DEFAULT_PLUGINS_DIRECTORY = Path(__file__).resolve().parent.parent / "plugins"
DEFAULT_POLL_INTERVAL_MS = 2_000
DEFAULT_REQUEST_TIMEOUT_MS = 1_000
UI_VISIBILITY_AREAS = (
    "dashboard",
    "settings_hub",
    "study_settings",
    "destination_settings",
)
UI_EXTENSION_SURFACES = ("dashboard", "participant")
_UI_ASSET_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]*\.js$")
_TIMELINE_CHANNEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_PLATFORM_TARGET_PATTERN = re.compile(r"^(?:default|[a-z][a-z0-9]*-[a-z0-9_]+)$")
ACQUISITION_TRANSPORTS = {
    "internal",
    "lan",
    "wlan",
    "ble",
    "serial",
    "browser_https",
    "local_hardware",
    "network_adapter",
}
ACQUISITION_DELIVERIES = {"native_lsl", "host_lsl_bridge"}
_NATIVE_LSL_TRANSPORTS = {"lan", "wlan"}
_HOST_LSL_BRIDGE_TRANSPORTS = ACQUISITION_TRANSPORTS - _NATIVE_LSL_TRANSPORTS
_BROWSER_TRANSPORT_REQUIREMENTS = (
    "heartbeat_required",
    "sequence_required",
    "source_timestamp_required",
)
_BROWSER_SOURCE_TIMESTAMP_FIELDS = {"source_epoch_ms", "source_timestamp"}
_CANONICAL_RECORDING_DISABLE_TOKENS = {
    "captureenabled",
    "canonicalrecording",
    "canonicalrecordingenabled",
    "canonicalstreamrecording",
    "canonicalstreamrecordingenabled",
    "lslenabled",
    "recordingenabled",
    "sendmarker",
    "tolsl",
}
_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_LSL_CHANNEL_FORMATS = {
    "int8",
    "int16",
    "int32",
    "int64",
    "float32",
    "double64",
    "string",
}
_ENTRY_POINT_PATTERN = re.compile(
    r"^(?P<module>[a-zA-Z_][a-zA-Z0-9_.]*):(?P<attribute>[a-zA-Z_][a-zA-Z0-9_]*)$"
)

# API v2 names are accepted while reading a manifest, but the public catalog
# always exposes the API-v3 name.  This is intentionally one-way: new code has
# one vocabulary while older in-tree fixtures can still fail gracefully.
CAPABILITY_ALIASES = {
    "status_poll": "health",
    "lsl_stream": "lsl_stream_provider",
    "recording": "recording_source",
    "xdf_recording": "recording_worker",
}


class PluginManifestError(ValueError):
    """Raised when one built-in plugin manifest violates the v3 contract."""


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

    root = Path(plugins_dir or DEFAULT_PLUGINS_DIRECTORY).resolve()
    candidates = [_read_candidate(path) for path in _plugin_directories(root)]
    _mark_duplicate_plugin_keys(candidates)
    _mark_duplicate_stream_ids(candidates)
    _mark_conflicting_upload_destinations(candidates)

    entries: list[PluginCatalogEntry] = []
    for candidate in candidates:
        if candidate.errors:
            entries.append(_invalid_entry(candidate))
            continue

        try:
            plugin = _import_plugin(candidate, package_name, module_importer)
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


def validate_and_normalize_manifest(payload: Any, *, directory_name: str) -> dict[str, Any]:
    """Validate a raw ``manifest.json`` and return its stable public shape."""

    if not isinstance(payload, dict):
        raise PluginManifestError("manifest root must be a JSON object")
    if payload.get("api_version") != PLUGIN_API_VERSION:
        raise PluginManifestError(f"api_version must be {PLUGIN_API_VERSION}")

    plugin_key = _required_key(payload, "plugin_key")
    config_key = _required_key(payload, "config_key")
    version = _required_text(payload, "version")
    category = _required_text(payload, "category")
    entry_point = _required_text(payload, "entry_point")
    entry_match = _ENTRY_POINT_PATTERN.fullmatch(entry_point)
    if entry_match is None:
        raise PluginManifestError("entry_point must use 'module:attribute' syntax")
    if entry_match.group("module").startswith(".") or ".." in entry_match.group("module"):
        raise PluginManifestError("entry_point must remain inside its plugin directory")

    ui = payload.get("ui")
    if not isinstance(ui, dict):
        raise PluginManifestError("ui must be a JSON object")
    label = _required_text(ui, "label", prefix="ui.")
    description = _optional_text(ui.get("description"))
    order = _non_negative_int(ui.get("order", 1_000), "ui.order")
    visibility = _normalize_ui_visibility(ui.get("visibility"))
    extensions = _normalize_ui_extensions(ui.get("extensions"))
    assets = _normalize_ui_assets(ui.get("assets"))
    timeline = _normalize_timeline_metadata(ui.get("timeline"))
    icon = _optional_text(ui.get("icon"))
    unexpected_ui = sorted(
        set(ui) - {
            "label", "description", "order", "visibility", "extensions",
            "assets", "timeline", "icon",
        }
    )
    if unexpected_ui:
        raise PluginManifestError(
            "ui contains unsupported fields: " + ", ".join(unexpected_ui)
        )

    capability_config = _normalize_capabilities(payload.get("capabilities"))
    settings = _normalize_settings(payload.get("settings"))
    streams = _normalize_streams(payload.get("streams"))
    _validate_capability_contracts(capability_config, streams, settings)
    lifecycle = _normalize_lifecycle(payload.get("lifecycle"))

    poll_interval_ms = _positive_int(
        payload.get("poll_interval_ms", DEFAULT_POLL_INTERVAL_MS),
        "poll_interval_ms",
    )
    request_timeout_ms = _positive_int(
        payload.get("request_timeout_ms", DEFAULT_REQUEST_TIMEOUT_MS),
        "request_timeout_ms",
    )
    backpressure = _normalize_backpressure(payload.get("backpressure"))
    clock_domain = _optional_text(payload.get("clock_domain")) or _default_clock_domain(streams)
    expected_data_rate = payload.get("expected_data_rate", {})
    if not isinstance(expected_data_rate, dict):
        raise PluginManifestError("expected_data_rate must be a JSON object")

    return {
        "api_version": PLUGIN_API_VERSION,
        "plugin_key": plugin_key,
        "config_key": config_key,
        "version": version,
        "category": category,
        "entry_point": entry_point,
        "directory": directory_name,
        "ui": {
            "label": label,
            "description": description,
            "order": order,
            "visibility": visibility,
            "extensions": extensions,
            "assets": assets,
            "timeline": timeline,
            "icon": icon,
        },
        "capabilities": list(capability_config),
        "capability_config": capability_config,
        "streams": streams,
        "settings": settings,
        # Existing settings/status services consume these compatibility names.
        "runtime_settings": deepcopy(settings["machine"]),
        "study_settings_schema": deepcopy(settings["study"]),
        "card_actions_schema": deepcopy(settings["card_actions"]),
        "poll_interval_ms": poll_interval_ms,
        "request_timeout_ms": request_timeout_ms,
        "clock_domain": clock_domain,
        "expected_data_rate": deepcopy(expected_data_rate),
        "backpressure": backpressure,
        "lifecycle": lifecycle,
    }


def validate_admin_action_payload(
    action: dict[str, Any],
    payload: Any,
) -> dict[str, Any]:
    """Validate one request body against its closed manifest declaration."""

    if not isinstance(payload, dict):
        raise ValueError("plugin action payload must be a JSON object")
    schema = action.get("payload_schema") or {}
    if not isinstance(schema, dict):
        raise ValueError("plugin action payload schema is invalid")
    unexpected = sorted(set(payload) - set(schema))
    if unexpected:
        raise ValueError(
            "plugin action payload contains undeclared fields: " + ", ".join(unexpected)
        )

    normalized: dict[str, Any] = {}
    for name, raw_field in schema.items():
        field = raw_field if isinstance(raw_field, dict) else {}
        if name not in payload:
            if field.get("required"):
                raise ValueError(f"plugin action payload is missing required field: {name}")
            continue
        value = payload[name]
        field_type = field.get("type")
        if field_type == "string":
            if not isinstance(value, str):
                raise ValueError(f"plugin action payload field {name} must be a string")
            if len(value) > int(field.get("max_length", 512)):
                raise ValueError(f"plugin action payload field {name} is too long")
        elif field_type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"plugin action payload field {name} must be an integer")
        elif field_type == "number":
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"plugin action payload field {name} must be a finite number")
        elif field_type == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"plugin action payload field {name} must be boolean")
        else:
            raise ValueError(f"plugin action payload field {name} has an invalid schema")
        if "minimum" in field and value < field["minimum"]:
            raise ValueError(f"plugin action payload field {name} is below its minimum")
        if "maximum" in field and value > field["maximum"]:
            raise ValueError(f"plugin action payload field {name} exceeds its maximum")
        if "enum" in field and value not in field["enum"]:
            raise ValueError(f"plugin action payload field {name} is not an allowed value")
        normalized[name] = deepcopy(value)

    any_of = action.get("any_of_required") or []
    if any_of and not any(
        name in normalized and normalized[name] not in (None, "")
        for name in any_of
    ):
        raise ValueError(
            "plugin action payload requires at least one of: " + ", ".join(any_of)
        )
    return normalized


def _plugin_directories(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        (
            path
            for path in root.iterdir()
            if path.is_dir()
            and not path.name.startswith((".", "_"))
            # Every trusted top-level directory is a plugin candidate.  A
            # compatibility/helper package must opt out explicitly instead of
            # making a missing manifest disappear silently.
            and not (path / PLUGIN_IGNORE_FILENAME).is_file()
        ),
        key=lambda path: path.name,
    )


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
    match = _ENTRY_POINT_PATTERN.fullmatch(entry_point)
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


def _normalize_capabilities(value: Any) -> dict[str, dict[str, Any]]:
    if isinstance(value, list):
        raw_items = [(item, {}) for item in value]
    elif isinstance(value, dict):
        raw_items = list(value.items())
    else:
        raise PluginManifestError("capabilities must be an object or a list")

    normalized: dict[str, dict[str, Any]] = {}
    for raw_name, raw_config in raw_items:
        name = str(raw_name or "").strip()
        if not _KEY_PATTERN.fullmatch(name):
            raise PluginManifestError(f"invalid capability name: {name!r}")
        canonical_name = CAPABILITY_ALIASES.get(name, name)
        if raw_config is True or raw_config is None:
            config: dict[str, Any] = {}
        elif raw_config is False:
            continue
        elif isinstance(raw_config, dict):
            config = deepcopy(raw_config)
        else:
            raise PluginManifestError(f"capability {name!r} must be an object or boolean")
        if canonical_name in normalized:
            raise PluginManifestError(f"duplicate capability: {canonical_name}")
        normalized[canonical_name] = _normalize_capability_config(canonical_name, config)
    if not normalized:
        raise PluginManifestError("capabilities must declare at least one capability")
    return normalized


def _normalize_capability_config(name: str, config: dict[str, Any]) -> dict[str, Any]:
    if name == "acquisition_transport":
        return _normalize_acquisition_transport(config)
    if name == "admin_actions":
        return _normalize_admin_actions(config)
    if name == "participant_actions":
        return _normalize_participant_operation_keys(
            config,
            capability="participant_actions",
            field="actions",
        )
    if name == "participant_ingest":
        return _normalize_participant_operation_keys(
            config,
            capability="participant_ingest",
            field="inputs",
        )
    if name == "readiness":
        return _normalize_readiness(config)
    if name == "upload_destination":
        return _normalize_upload_destination(config)
    if name == "credentials":
        return _normalize_credentials(config)
    return config


def _normalize_credentials(config: dict[str, Any]) -> dict[str, Any]:
    """Validate the one secret field this plugin needs.

    Declaring it here replaces three separate hardcoded maps that used to list
    the same fact -- which config field a secret lives under, which
    environment variable can override it, and whether a study may carry its
    own -- once each, in three different files, guaranteed to agree only by
    someone remembering to keep them in step.
    """

    allowed = {"config_field", "env_var", "per_study"}
    unexpected = sorted(set(config) - allowed)
    if unexpected:
        raise PluginManifestError(
            "credentials contains unsupported fields: " + ", ".join(unexpected)
        )
    config_field = _required_key(config, "config_field", prefix="credentials.")
    env_var = _optional_text(config.get("env_var")) or ""
    per_study = bool(config.get("per_study", False))
    return {"config_field": config_field, "env_var": env_var, "per_study": per_study}


def _normalize_readiness(config: dict[str, Any]) -> dict[str, Any]:
    """Validate optional, manifest-driven runtime-mode platform support."""

    if not config:
        return {}
    allowed = {"mode_setting", "default_mode", "platform_modes"}
    unexpected = sorted(set(config) - allowed)
    if unexpected:
        raise PluginManifestError(
            "readiness contains unsupported fields: " + ", ".join(unexpected)
        )

    mode_setting = _required_key(config, "mode_setting", prefix="readiness.")
    default_mode = _required_key(config, "default_mode", prefix="readiness.")
    raw_platform_modes = config.get("platform_modes")
    if not isinstance(raw_platform_modes, dict) or not raw_platform_modes:
        raise PluginManifestError("readiness.platform_modes must be a non-empty object")
    if "default" not in raw_platform_modes:
        raise PluginManifestError("readiness.platform_modes must declare a default target")

    platform_modes: dict[str, list[str]] = {}
    for raw_target, raw_modes in raw_platform_modes.items():
        target = str(raw_target or "").strip().lower()
        if not _PLATFORM_TARGET_PATTERN.fullmatch(target):
            raise PluginManifestError(
                f"invalid readiness platform target: {raw_target!r}"
            )
        if not isinstance(raw_modes, list) or not raw_modes:
            raise PluginManifestError(
                f"readiness.platform_modes.{target} must be a non-empty list"
            )
        modes: list[str] = []
        for raw_mode in raw_modes:
            mode = str(raw_mode or "").strip()
            if not _KEY_PATTERN.fullmatch(mode):
                raise PluginManifestError(
                    f"readiness.platform_modes.{target} contains an invalid mode"
                )
            if mode in modes:
                raise PluginManifestError(
                    f"readiness.platform_modes.{target} contains duplicate mode {mode!r}"
                )
            modes.append(mode)
        platform_modes[target] = modes

    if default_mode not in platform_modes["default"]:
        raise PluginManifestError(
            "readiness.default_mode must be allowed by readiness.platform_modes.default"
        )
    return {
        "mode_setting": mode_setting,
        "default_mode": default_mode,
        "platform_modes": platform_modes,
    }


def _normalize_upload_destination(config: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "destination",
        "default_enabled",
        "requires_valid_result",
        "publish_on_attention",
        "republish_on_degraded",
        "purge_verified_sources",
        "legacy",
    }
    unexpected = sorted(set(config) - allowed)
    if unexpected:
        raise PluginManifestError(
            "upload_destination contains unsupported fields: " + ", ".join(unexpected)
        )
    destination = _required_key(
        config,
        "destination",
        prefix="upload_destination.",
    )
    normalized: dict[str, Any] = {"destination": destination}
    defaults = {
        "default_enabled": False,
        "requires_valid_result": True,
        "publish_on_attention": False,
        "republish_on_degraded": False,
        "purge_verified_sources": False,
    }
    for name, default in defaults.items():
        value = config.get(name, default)
        if not isinstance(value, bool):
            raise PluginManifestError(f"upload_destination.{name} must be boolean")
        normalized[name] = value

    legacy = config.get("legacy", {})
    if not isinstance(legacy, dict):
        raise PluginManifestError("upload_destination.legacy must be a JSON object")
    legacy_unexpected = sorted(set(legacy) - {"enabled_field", "settings"})
    if legacy_unexpected:
        raise PluginManifestError(
            "upload_destination.legacy contains unsupported fields: "
            + ", ".join(legacy_unexpected)
        )
    enabled_field = _optional_text(legacy.get("enabled_field"))
    if enabled_field and not _KEY_PATTERN.fullmatch(enabled_field):
        raise PluginManifestError(
            "upload_destination.legacy.enabled_field must be a snake_case key"
        )
    raw_settings = legacy.get("settings", {})
    if not isinstance(raw_settings, dict):
        raise PluginManifestError(
            "upload_destination.legacy.settings must be a JSON object"
        )
    legacy_settings: dict[str, str] = {}
    for raw_name, raw_field in raw_settings.items():
        name = str(raw_name or "").strip()
        field = _optional_text(raw_field)
        if not _KEY_PATTERN.fullmatch(name) or not field or not _KEY_PATTERN.fullmatch(field):
            raise PluginManifestError(
                "upload_destination.legacy.settings must map snake_case keys to snake_case fields"
            )
        legacy_settings[name] = field
    normalized["legacy"] = {
        "enabled_field": enabled_field,
        "settings": legacy_settings,
    }
    return normalized


def _normalize_participant_operation_keys(
    config: dict[str, Any],
    *,
    capability: str,
    field: str,
) -> dict[str, list[str]]:
    """Normalize the closed allow-list used by participant-facing routes."""

    unexpected = sorted(set(config) - {field})
    if unexpected:
        raise PluginManifestError(
            f"{capability} contains unsupported fields: " + ", ".join(unexpected)
        )
    raw_keys = config.get(field)
    if not isinstance(raw_keys, list) or not raw_keys:
        raise PluginManifestError(f"{capability}.{field} must be a non-empty list")

    keys: list[str] = []
    for index, raw_key in enumerate(raw_keys, start=1):
        key = str(raw_key or "").strip()
        if not _KEY_PATTERN.fullmatch(key):
            raise PluginManifestError(
                f"{capability}.{field}[{index}] must be a snake_case key"
            )
        if key in keys:
            raise PluginManifestError(f"duplicate {capability} key: {key}")
        keys.append(key)
    return {field: keys}


def _normalize_acquisition_transport(config: dict[str, Any]) -> dict[str, Any]:
    transport = _required_text(config, "transport", prefix="acquisition_transport.")
    delivery = _required_text(config, "delivery", prefix="acquisition_transport.")
    if transport not in ACQUISITION_TRANSPORTS:
        raise PluginManifestError(
            "acquisition_transport.transport must be one of: "
            + ", ".join(sorted(ACQUISITION_TRANSPORTS))
        )
    if delivery not in ACQUISITION_DELIVERIES:
        raise PluginManifestError(
            "acquisition_transport.delivery must be native_lsl or host_lsl_bridge"
        )

    expected_delivery = (
        "native_lsl" if transport in _NATIVE_LSL_TRANSPORTS else "host_lsl_bridge"
    )
    if delivery != expected_delivery:
        raise PluginManifestError(
            f"acquisition transport {transport!r} requires delivery {expected_delivery!r}"
        )

    allowed_keys = {"transport", "delivery"}
    normalized: dict[str, Any] = {"transport": transport, "delivery": delivery}
    if transport == "browser_https":
        allowed_keys.update(_BROWSER_TRANSPORT_REQUIREMENTS)
        allowed_keys.add("source_timestamp_fields")
        for requirement in _BROWSER_TRANSPORT_REQUIREMENTS:
            if config.get(requirement) is not True:
                raise PluginManifestError(
                    f"acquisition_transport.{requirement} must be true for browser_https"
                )
            normalized[requirement] = True
        raw_timestamp_fields = config.get(
            "source_timestamp_fields",
            sorted(_BROWSER_SOURCE_TIMESTAMP_FIELDS),
        )
        if not isinstance(raw_timestamp_fields, list) or not raw_timestamp_fields:
            raise PluginManifestError(
                "acquisition_transport.source_timestamp_fields must be a non-empty list"
            )
        timestamp_fields: list[str] = []
        for raw_field in raw_timestamp_fields:
            field = str(raw_field or "").strip()
            if field not in _BROWSER_SOURCE_TIMESTAMP_FIELDS:
                raise PluginManifestError(
                    "acquisition_transport.source_timestamp_fields may contain only: "
                    + ", ".join(sorted(_BROWSER_SOURCE_TIMESTAMP_FIELDS))
                )
            if field in timestamp_fields:
                raise PluginManifestError(
                    f"duplicate acquisition source timestamp field: {field}"
                )
            timestamp_fields.append(field)
        normalized["source_timestamp_fields"] = timestamp_fields
    unexpected = sorted(set(config) - allowed_keys)
    if unexpected:
        raise PluginManifestError(
            "acquisition_transport contains unsupported fields: " + ", ".join(unexpected)
        )
    return normalized


def _normalize_admin_actions(config: dict[str, Any]) -> dict[str, Any]:
    raw_actions = config.get("actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        raise PluginManifestError("admin_actions.actions must be a non-empty list")
    unexpected = sorted(set(config) - {"actions"})
    if unexpected:
        raise PluginManifestError(
            "admin_actions contains unsupported fields: " + ", ".join(unexpected)
        )

    actions: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    allowed_fields = {
        "key",
        "label",
        "description",
        "confirm",
        "danger",
        "payload_schema",
        "any_of_required",
        "instances",
    }
    for index, raw_action in enumerate(raw_actions, start=1):
        if not isinstance(raw_action, dict):
            raise PluginManifestError(f"admin_actions.actions[{index}] must be a JSON object")
        extra = sorted(set(raw_action) - allowed_fields)
        if extra:
            raise PluginManifestError(
                f"admin_actions.actions[{index}] contains unsupported fields: "
                + ", ".join(extra)
            )
        key = _required_key(
            raw_action,
            "key",
            prefix=f"admin_actions.actions[{index}].",
        )
        if key in seen_keys:
            raise PluginManifestError(f"duplicate admin action key: {key}")
        seen_keys.add(key)
        action: dict[str, Any] = {
            "key": key,
            "label": _required_text(
                raw_action,
                "label",
                prefix=f"admin_actions.actions[{index}].",
            ),
        }
        description = _optional_text(raw_action.get("description"))
        if description:
            action["description"] = description
        for flag in ("confirm", "danger"):
            if flag in raw_action:
                if not isinstance(raw_action[flag], bool):
                    raise PluginManifestError(
                        f"admin_actions.actions[{index}].{flag} must be boolean"
                    )
                action[flag] = raw_action[flag]
        payload_schema = _normalize_admin_action_payload_schema(
            raw_action.get("payload_schema"),
            index=index,
        )
        action["payload_schema"] = payload_schema
        action["any_of_required"] = _normalize_admin_action_any_of(
            raw_action.get("any_of_required"),
            payload_schema,
            index=index,
        )
        instances = _normalize_admin_action_instances(
            raw_action.get("instances"),
            payload_schema,
            index=index,
        )
        if instances:
            action["instances"] = instances
        actions.append(action)
    return {"actions": actions}


def _normalize_admin_action_payload_schema(
    value: Any,
    *,
    index: int,
) -> dict[str, dict[str, Any]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PluginManifestError(
            f"admin_actions.actions[{index}].payload_schema must be a JSON object"
        )
    schema: dict[str, dict[str, Any]] = {}
    allowed = {"type", "required", "minimum", "maximum", "max_length", "enum"}
    for raw_name, raw_field in value.items():
        name = str(raw_name or "").strip()
        if not _KEY_PATTERN.fullmatch(name):
            raise PluginManifestError(
                f"admin_actions.actions[{index}].payload_schema has an invalid field name"
            )
        if not isinstance(raw_field, dict):
            raise PluginManifestError(
                f"admin_actions.actions[{index}].payload_schema.{name} must be an object"
            )
        extra = sorted(set(raw_field) - allowed)
        if extra:
            raise PluginManifestError(
                f"admin_actions.actions[{index}].payload_schema.{name} contains unsupported fields: "
                + ", ".join(extra)
            )
        field_type = _optional_text(raw_field.get("type"))
        if field_type not in {"string", "integer", "number", "boolean"}:
            raise PluginManifestError(
                f"admin_actions.actions[{index}].payload_schema.{name}.type is unsupported"
            )
        field: dict[str, Any] = {
            "type": field_type,
            "required": bool(raw_field.get("required", False)),
        }
        if "required" in raw_field and not isinstance(raw_field["required"], bool):
            raise PluginManifestError(
                f"admin_actions.actions[{index}].payload_schema.{name}.required must be boolean"
            )
        if field_type == "string":
            field["max_length"] = _positive_int(
                raw_field.get("max_length", 512),
                f"admin_actions.actions[{index}].payload_schema.{name}.max_length",
            )
        elif "max_length" in raw_field:
            raise PluginManifestError(
                f"admin_actions.actions[{index}].payload_schema.{name}.max_length requires string"
            )
        for limit in ("minimum", "maximum"):
            if limit not in raw_field:
                continue
            raw_limit = raw_field[limit]
            if (
                field_type not in {"integer", "number"}
                or isinstance(raw_limit, bool)
                or not isinstance(raw_limit, (int, float))
                or not math.isfinite(float(raw_limit))
            ):
                raise PluginManifestError(
                    f"admin_actions.actions[{index}].payload_schema.{name}.{limit} must be numeric"
                )
            field[limit] = raw_limit
        if "enum" in raw_field:
            enum = raw_field["enum"]
            if not isinstance(enum, list) or not enum:
                raise PluginManifestError(
                    f"admin_actions.actions[{index}].payload_schema.{name}.enum must be a non-empty list"
                )
            field["enum"] = deepcopy(enum)
        schema[name] = field
    return schema


def _normalize_admin_action_any_of(
    value: Any,
    schema: dict[str, dict[str, Any]],
    *,
    index: int,
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not value:
        raise PluginManifestError(
            f"admin_actions.actions[{index}].any_of_required must be a non-empty list"
        )
    fields: list[str] = []
    for raw_field in value:
        field = str(raw_field or "").strip()
        if field not in schema:
            raise PluginManifestError(
                f"admin_actions.actions[{index}].any_of_required names an undeclared field"
            )
        if field not in fields:
            fields.append(field)
    return fields


def _normalize_admin_action_instances(
    value: Any,
    schema: dict[str, dict[str, Any]],
    *,
    index: int,
) -> dict[str, Any]:
    if value is None:
        return {}
    if not schema:
        raise PluginManifestError(
            f"admin_actions.actions[{index}].instances requires payload_schema"
        )
    if not isinstance(value, dict):
        raise PluginManifestError(
            f"admin_actions.actions[{index}].instances must be a JSON object"
        )
    extra = sorted(set(value) - {"status_paths", "payload_map", "label_fields"})
    if extra:
        raise PluginManifestError(
            f"admin_actions.actions[{index}].instances contains unsupported fields: "
            + ", ".join(extra)
        )
    status_paths = _normalize_status_paths(
        value.get("status_paths"),
        f"admin_actions.actions[{index}].instances.status_paths",
    )
    raw_map = value.get("payload_map")
    if not isinstance(raw_map, dict) or not raw_map:
        raise PluginManifestError(
            f"admin_actions.actions[{index}].instances.payload_map must be a non-empty object"
        )
    payload_map: dict[str, str] = {}
    for raw_target, raw_source in raw_map.items():
        target = str(raw_target or "").strip()
        if target not in schema:
            raise PluginManifestError(
                f"admin_actions.actions[{index}].instances.payload_map targets an undeclared field"
            )
        source = _normalize_status_path(
            raw_source,
            f"admin_actions.actions[{index}].instances.payload_map.{target}",
        )
        payload_map[target] = source
    label_fields = _normalize_status_paths(
        value.get("label_fields", []),
        f"admin_actions.actions[{index}].instances.label_fields",
        allow_empty=True,
    )
    return {
        "status_paths": status_paths,
        "payload_map": payload_map,
        "label_fields": label_fields,
    }


def _normalize_status_paths(value: Any, name: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        suffix = "a list" if allow_empty else "a non-empty list"
        raise PluginManifestError(f"{name} must be {suffix}")
    paths: list[str] = []
    for raw_path in value:
        path = _normalize_status_path(raw_path, name)
        if path not in paths:
            paths.append(path)
    return paths


def _normalize_status_path(value: Any, name: str) -> str:
    path = _optional_text(value)
    if not path or not _TIMELINE_CHANNEL_PATTERN.fullmatch(path):
        raise PluginManifestError(f"{name} contains an invalid status path")
    return path


def _normalize_settings(value: Any) -> dict[str, dict[str, Any]]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise PluginManifestError("settings must be a JSON object")
    extra = sorted(set(value) - {"machine", "study", "card_actions"})
    if extra:
        raise PluginManifestError("settings contains unsupported sections: " + ", ".join(extra))
    result: dict[str, dict[str, Any]] = {}
    for section_name in ("machine", "study", "card_actions"):
        section = value.get(section_name, {})
        if not isinstance(section, dict):
            raise PluginManifestError(f"settings.{section_name} must be a JSON object")
        result[section_name] = deepcopy(section)
    return result


def _normalize_lifecycle(value: Any) -> dict[str, bool]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise PluginManifestError("lifecycle must be a JSON object")
    unexpected = sorted(set(value) - {"reinitialize_on_disable"})
    if unexpected:
        raise PluginManifestError(
            "lifecycle contains unsupported fields: " + ", ".join(unexpected)
        )
    reinitialize = value.get("reinitialize_on_disable", False)
    if not isinstance(reinitialize, bool):
        raise PluginManifestError("lifecycle.reinitialize_on_disable must be boolean")
    return {"reinitialize_on_disable": reinitialize}


def _normalize_ui_visibility(value: Any) -> dict[str, bool]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise PluginManifestError("ui.visibility must be a JSON object")
    unexpected = sorted(set(value) - set(UI_VISIBILITY_AREAS))
    if unexpected:
        raise PluginManifestError(
            "ui.visibility contains unsupported fields: " + ", ".join(unexpected)
        )
    visibility: dict[str, bool] = {}
    for area in UI_VISIBILITY_AREAS:
        flag = value.get(area, True)
        if not isinstance(flag, bool):
            raise PluginManifestError(f"ui.visibility.{area} must be boolean")
        visibility[area] = flag
    return visibility


def _normalize_ui_extensions(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PluginManifestError("ui.extensions must be a JSON object")
    unexpected = sorted(set(value) - set(UI_EXTENSION_SURFACES))
    if unexpected:
        raise PluginManifestError(
            "ui.extensions contains unsupported fields: " + ", ".join(unexpected)
        )
    extensions: dict[str, str] = {}
    for surface in UI_EXTENSION_SURFACES:
        if surface not in value:
            continue
        extensions[surface] = _normalize_ui_asset_path(
            value[surface],
            f"ui.extensions.{surface}",
        )
    return extensions


def _normalize_ui_assets(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PluginManifestError("ui.assets must be a list")
    assets: list[str] = []
    for index, raw_path in enumerate(value, start=1):
        path = _normalize_ui_asset_path(raw_path, f"ui.assets[{index}]")
        if path in assets:
            raise PluginManifestError("ui.assets contains duplicates")
        assets.append(path)
    return assets


def _normalize_ui_asset_path(value: Any, name: str) -> str:
    path = _optional_text(value)
    if not path or not _UI_ASSET_PATTERN.fullmatch(path):
        raise PluginManifestError(f"{name} must be a relative POSIX .js path")
    pure_path = PurePosixPath(path)
    if pure_path.is_absolute() or any(part in {"", ".", ".."} for part in pure_path.parts):
        raise PluginManifestError(f"{name} must remain inside the plugin directory")
    return pure_path.as_posix()


def _normalize_timeline_metadata(value: Any) -> dict[str, list[str]]:
    if value is None:
        return {"lane_aliases": [], "preferred_channels": []}
    if not isinstance(value, dict):
        raise PluginManifestError("ui.timeline must be a JSON object")
    unexpected = sorted(set(value) - {"lane_aliases", "preferred_channels"})
    if unexpected:
        raise PluginManifestError(
            "ui.timeline contains unsupported fields: " + ", ".join(unexpected)
        )

    result: dict[str, list[str]] = {}
    for field in ("lane_aliases", "preferred_channels"):
        raw_items = value.get(field, [])
        if not isinstance(raw_items, list):
            raise PluginManifestError(f"ui.timeline.{field} must be a list")
        items: list[str] = []
        for index, raw_item in enumerate(raw_items, start=1):
            item = _optional_text(raw_item)
            if not item or not _TIMELINE_CHANNEL_PATTERN.fullmatch(item):
                raise PluginManifestError(
                    f"ui.timeline.{field}[{index}] contains an invalid identifier"
                )
            if item in items:
                raise PluginManifestError(f"ui.timeline.{field} contains duplicates")
            items.append(item)
        result[field] = items
    return result


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


def _normalize_streams(value: Any) -> list[dict[str, Any]]:
    if value is None:
        value = []
    if not isinstance(value, list):
        raise PluginManifestError("streams must be a list")
    streams: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    seen_source_ids: set[str] = set()
    for index, raw_stream in enumerate(value, start=1):
        if not isinstance(raw_stream, dict):
            raise PluginManifestError(f"stream {index} must be a JSON object")
        key = _required_key(raw_stream, "key", prefix=f"streams[{index}].")
        source_id = _required_text(raw_stream, "source_id", prefix=f"streams[{index}].")
        if key in seen_keys:
            raise PluginManifestError(f"duplicate stream key: {key}")
        if source_id in seen_source_ids:
            raise PluginManifestError(f"duplicate stream source_id: {source_id}")
        seen_keys.add(key)
        seen_source_ids.add(source_id)
        rate = raw_stream.get("nominal_rate_hz", 0)
        if (
            isinstance(rate, bool)
            or not isinstance(rate, (int, float))
            or not math.isfinite(float(rate))
            or rate < 0
        ):
            raise PluginManifestError(f"streams[{index}].nominal_rate_hz must be zero or positive")
        channels = raw_stream.get("channels", [])
        if not isinstance(channels, list) or not channels:
            raise PluginManifestError(f"streams[{index}].channels must be a non-empty list")
        if any(not isinstance(channel, str) or not channel.strip() for channel in channels):
            raise PluginManifestError(f"streams[{index}].channels must contain non-empty strings")
        if len({channel.strip() for channel in channels}) != len(channels):
            raise PluginManifestError(f"streams[{index}].channels must be unique")
        channel_format = _optional_text(raw_stream.get("channel_format"))
        if channel_format not in _LSL_CHANNEL_FORMATS:
            raise PluginManifestError(
                f"streams[{index}].channel_format must be one of: "
                + ", ".join(sorted(_LSL_CHANNEL_FORMATS))
            )
        sequence_channel = _optional_text(raw_stream.get("sequence_channel"))
        if sequence_channel and sequence_channel not in channels:
            raise PluginManifestError(
                f"streams[{index}].sequence_channel must name a declared channel"
            )
        channel_units = raw_stream.get("channel_units")
        if not isinstance(channel_units, list) or len(channel_units) != len(channels):
            raise PluginManifestError(
                f"streams[{index}].channel_units must match the channel list"
            )
        if any(not isinstance(unit, str) or not unit.strip() for unit in channel_units):
            raise PluginManifestError(f"streams[{index}].channel_units must contain strings")
        streams.append(
            {
                **deepcopy(raw_stream),
                "key": key,
                "source_id": source_id,
                "nominal_rate_hz": rate,
                "clock_domain": _optional_text(raw_stream.get("clock_domain")) or "server",
                "channel_format": channel_format,
                "sequence_channel": sequence_channel,
                "channels": deepcopy(channels),
                "channel_units": deepcopy(channel_units),
            }
        )
    return streams


def _validate_capability_contracts(
    capabilities: dict[str, dict[str, Any]],
    streams: list[dict[str, Any]],
    settings: dict[str, dict[str, Any]],
) -> None:
    if "lsl_stream_provider" in capabilities and not streams:
        raise PluginManifestError("lsl_stream_provider requires at least one stream")
    if "recording_source" in capabilities and not streams:
        raise PluginManifestError("recording_source requires at least one stream")
    if "recording_source" in capabilities and "lsl_stream_provider" not in capabilities:
        raise PluginManifestError("recording_source requires lsl_stream_provider")
    if "recording_source" in capabilities:
        _reject_canonical_recording_disable_settings(settings)
        recording_source = capabilities["recording_source"]
        primary_stream = _optional_text(recording_source.get("primary_stream"))
        if primary_stream and primary_stream not in {stream["key"] for stream in streams}:
            raise PluginManifestError(
                "recording_source.primary_stream must name a declared stream"
            )
        if "study_sensor" in capabilities and not primary_stream:
            raise PluginManifestError(
                "study_sensor recording sources require recording_source.primary_stream"
            )
        for stream in streams:
            stream["primary"] = bool(primary_stream and stream["key"] == primary_stream)
    if "study_sensor" in capabilities and "recording_source" in capabilities:
        missing = [
            capability
            for capability in ("lsl_stream_provider", "backup_projection")
            if capability not in capabilities
        ]
        if missing:
            raise PluginManifestError(
                "study_sensor recording sources require capabilities: "
                + ", ".join(missing)
            )
    projection = capabilities.get("backup_projection")
    if projection is not None:
        rate_hz = projection.get("rate_hz")
        if isinstance(rate_hz, bool) or not isinstance(rate_hz, (int, float)) or rate_hz <= 0:
            raise PluginManifestError("backup_projection.rate_hz must be positive")
        stale_after_ms = projection.get("stale_after_ms")
        if stale_after_ms is not None and (
            isinstance(stale_after_ms, bool)
            or not isinstance(stale_after_ms, (int, float))
            or stale_after_ms <= 0
        ):
            raise PluginManifestError("backup_projection.stale_after_ms must be positive")
        raw_channels = projection.get("channels")
        if not isinstance(raw_channels, list) or not raw_channels:
            raise PluginManifestError("backup_projection.channels must be a non-empty list")
        streams_by_key = {stream["key"]: stream for stream in streams}
        channels: list[dict[str, str]] = []
        outputs: set[str] = set()
        for index, raw_channel in enumerate(raw_channels, start=1):
            if not isinstance(raw_channel, dict):
                raise PluginManifestError(
                    f"backup_projection.channels[{index}] must be a JSON object"
                )
            output = _required_key(
                raw_channel,
                "output",
                prefix=f"backup_projection.channels[{index}].",
            )
            stream_id = _required_key(
                raw_channel,
                "stream",
                prefix=f"backup_projection.channels[{index}].",
            )
            channel = _required_text(
                raw_channel,
                "channel",
                prefix=f"backup_projection.channels[{index}].",
            )
            if output in outputs:
                raise PluginManifestError(f"duplicate backup projection output: {output}")
            outputs.add(output)
            source_stream = streams_by_key.get(stream_id)
            if source_stream is None:
                raise PluginManifestError(
                    f"backup projection stream is not declared: {stream_id}"
                )
            if channel not in set(source_stream.get("channels") or []):
                raise PluginManifestError(
                    f"backup projection channel {channel!r} is absent from stream {stream_id!r}"
                )
            channels.append({"output": output, "stream": stream_id, "channel": channel})
        projection["channels"] = channels
    study_sensor = capabilities.get("study_sensor")
    if study_sensor is not None:
        for key in ("default_enabled", "default_required"):
            if key in study_sensor and not isinstance(study_sensor[key], bool):
                raise PluginManifestError(f"study_sensor.{key} must be boolean")


def _reject_canonical_recording_disable_settings(
    settings: dict[str, dict[str, Any]],
) -> None:
    for section_name, section in settings.items():
        for field_name, raw_field in section.items():
            field = raw_field if isinstance(raw_field, dict) else {}
            candidates = (str(field_name), str(field.get("path") or field_name))
            if any(_setting_token(candidate) in _CANONICAL_RECORDING_DISABLE_TOKENS for candidate in candidates):
                raise PluginManifestError(
                    "recording_source cannot expose a setting that disables canonical "
                    f"stream recording: settings.{section_name}.{field_name}"
                )


def _setting_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _normalize_backpressure(value: Any) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise PluginManifestError("backpressure must be a JSON object")
    return {
        "max_in_flight": _positive_int(value.get("max_in_flight", 1), "backpressure.max_in_flight"),
        "drop_policy": _optional_text(value.get("drop_policy")) or "latest_status_wins",
    }


def _default_clock_domain(streams: list[dict[str, Any]]) -> str:
    domains = {str(stream.get("clock_domain") or "server") for stream in streams}
    return domains.pop() if len(domains) == 1 else "mixed" if domains else "server"


def _required_key(payload: dict[str, Any], name: str, *, prefix: str = "") -> str:
    value = _required_text(payload, name, prefix=prefix)
    if not _KEY_PATTERN.fullmatch(value):
        raise PluginManifestError(f"{prefix}{name} must use lowercase snake_case")
    return value


def _required_text(payload: dict[str, Any], name: str, *, prefix: str = "") -> str:
    value = _optional_text(payload.get(name))
    if not value:
        raise PluginManifestError(f"{prefix}{name} must be a non-empty string")
    return value


def _optional_text(value: Any) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise PluginManifestError(f"{name} must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise PluginManifestError(f"{name} must be a positive integer") from error
    if result <= 0:
        raise PluginManifestError(f"{name} must be a positive integer")
    return result


def _non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise PluginManifestError(f"{name} must be a non-negative integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise PluginManifestError(f"{name} must be a non-negative integer") from error
    if result < 0:
        raise PluginManifestError(f"{name} must be a non-negative integer")
    return result


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
