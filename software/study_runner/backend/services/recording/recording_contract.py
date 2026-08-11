"""Immutable recording contracts persisted with each native XDF session.

The plugin catalog describes what must be recorded, but it is mutable deployment
state: a plugin can be upgraded or removed between acquisition, crash recovery,
and final validation.  A recording therefore snapshots the exact manifests,
stream declarations, and backup projections it started with.  The SHA-256 digest
makes accidental or partial mutation fail closed instead of silently weakening
the scientific checks.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import hmac
import json
import math
from typing import Any, Iterable, Mapping


RECORDING_CONTRACT_SCHEMA = "study-runner/recording-contract/v1"
RECORDING_CONTRACT_VERSION = 1
RECORDING_CONTRACT_HASH_ALGORITHM = "sha256"


class RecordingContractError(ValueError):
    """Raised when a persisted recording contract is absent-mindedly mutated."""


def build_recording_contract(
    recording_plugins: Iterable[str],
    required_source_keys: Iterable[str],
    manifests: Mapping[str, Mapping[str, Any]],
    backup_contract: Mapping[str, Any],
    *,
    resolved_streams_by_source: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Return a JSON-safe, versioned snapshot of the acquisition contract.

    ``source_manifests`` always contains the exact catalog declaration which
    was installed at session start.  ``streams_by_source`` may instead contain
    a device-confirmed runtime contract (for example the channels reported by
    a BrainBit-2 family device).  Keeping both prevents a mutable manifest from
    weakening recovery while still recording the hardware that was actually
    connected.
    """

    selected = _unique_keys(recording_plugins, field="recording_plugins")
    required = _unique_keys(required_source_keys, field="required_source_keys")
    missing_required = sorted(set(required) - set(selected))
    if missing_required:
        raise RecordingContractError(
            "required recording sources are not selected: " + ", ".join(missing_required)
        )

    resolved_streams_by_source = resolved_streams_by_source or {}
    unknown_resolved = sorted(set(resolved_streams_by_source) - set(selected))
    if unknown_resolved:
        raise RecordingContractError(
            "runtime stream contracts reference unselected sources: "
            + ", ".join(unknown_resolved)
        )

    source_manifests: dict[str, dict[str, Any]] = {}
    source_descriptors: dict[str, dict[str, Any]] = {}
    streams_by_source: dict[str, list[dict[str, Any]]] = {}
    for source_key in selected:
        manifest = manifests.get(source_key)
        if not isinstance(manifest, Mapping):
            raise RecordingContractError(
                f"recording source {source_key!r} has no manifest to snapshot"
            )
        manifest_snapshot = deepcopy(dict(manifest))
        raw_streams = manifest_snapshot.get("streams")
        if not isinstance(raw_streams, list) or not all(
            isinstance(stream, Mapping) for stream in raw_streams
        ):
            raise RecordingContractError(
                f"recording source {source_key!r} has an invalid stream contract"
            )
        source_manifests[source_key] = manifest_snapshot
        resolved_streams = resolved_streams_by_source.get(source_key)
        if resolved_streams is None:
            stream_snapshot = [deepcopy(dict(stream)) for stream in raw_streams]
            stream_origin = "manifest"
        else:
            try:
                stream_snapshot = [deepcopy(dict(stream)) for stream in resolved_streams]
            except (TypeError, ValueError) as error:
                raise RecordingContractError(
                    f"recording source {source_key!r} returned an invalid runtime stream contract"
                ) from error
            if not stream_snapshot or not all(isinstance(stream, dict) for stream in stream_snapshot):
                raise RecordingContractError(
                    f"recording source {source_key!r} returned an empty or invalid runtime stream contract"
                )
            stream_origin = "runtime"
        _validate_stream_contract(source_key, stream_snapshot)
        streams_by_source[source_key] = stream_snapshot
        source_descriptors[source_key] = {
            "plugin_version": str(manifest_snapshot.get("version") or ""),
            "api_version": int(manifest_snapshot.get("api_version") or 0),
            "manifest_sha256": _canonical_sha256(manifest_snapshot),
            "stream_contract_origin": stream_origin,
        }

    backup = deepcopy(dict(backup_contract))
    projections = backup.get("projections")
    if not isinstance(projections, list) or not all(
        isinstance(item, dict) for item in projections
    ):
        raise RecordingContractError("recording backup projections must be a JSON object list")
    for projection in projections:
        plugin_key = str(projection.get("plugin_key") or "").strip()
        if not plugin_key or plugin_key not in selected:
            raise RecordingContractError(
                f"backup projection references unselected source {plugin_key!r}"
            )

    contract: dict[str, Any] = {
        "schema": RECORDING_CONTRACT_SCHEMA,
        "version": RECORDING_CONTRACT_VERSION,
        "hash_algorithm": RECORDING_CONTRACT_HASH_ALGORITHM,
        "selected_source_keys": selected,
        "required_source_keys": required,
        "source_manifests": source_manifests,
        "source_descriptors": source_descriptors,
        "streams_by_source": streams_by_source,
        "backup": backup,
    }
    contract["sha256"] = recording_contract_sha256(contract)
    validated = load_recording_contract(
        {
            "recording_plugins": selected,
            "required_source_keys": required,
            "recording_contract": contract,
        }
    )
    assert validated is not None
    return validated


def load_recording_contract(plan: Mapping[str, Any]) -> dict[str, Any] | None:
    """Validate and copy a plan's snapshot; return ``None`` for legacy plans."""

    raw_contract = plan.get("recording_contract")
    if raw_contract is None:
        return None
    if not isinstance(raw_contract, Mapping):
        raise RecordingContractError("recording_contract must be a JSON object")

    contract = deepcopy(dict(raw_contract))
    if contract.get("schema") != RECORDING_CONTRACT_SCHEMA:
        raise RecordingContractError("recording contract has an unsupported schema")
    if contract.get("version") != RECORDING_CONTRACT_VERSION:
        raise RecordingContractError("recording contract has an unsupported version")
    if contract.get("hash_algorithm") != RECORDING_CONTRACT_HASH_ALGORITHM:
        raise RecordingContractError("recording contract has an unsupported hash algorithm")

    recorded_digest = str(contract.get("sha256") or "").strip().lower()
    expected_digest = recording_contract_sha256(contract)
    if len(recorded_digest) != 64 or not hmac.compare_digest(
        recorded_digest, expected_digest
    ):
        raise RecordingContractError("recording contract SHA-256 does not match its contents")

    selected = _contract_key_list(contract, "selected_source_keys")
    required = _contract_key_list(contract, "required_source_keys")
    if set(required) - set(selected):
        raise RecordingContractError("recording contract requires an unselected source")

    manifests = contract.get("source_manifests")
    descriptors = contract.get("source_descriptors")
    streams_by_source = contract.get("streams_by_source")
    backup = contract.get("backup")
    if not isinstance(manifests, dict) or set(manifests) != set(selected):
        raise RecordingContractError("recording contract manifests do not match selected sources")
    if not isinstance(descriptors, dict) or set(descriptors) != set(selected):
        raise RecordingContractError("recording contract descriptors do not match selected sources")
    if not isinstance(streams_by_source, dict) or set(streams_by_source) != set(selected):
        raise RecordingContractError("recording stream contracts do not match selected sources")
    if not isinstance(backup, dict):
        raise RecordingContractError("recording backup contract must be a JSON object")
    projections = backup.get("projections")
    if not isinstance(projections, list) or not projections or not all(
        isinstance(item, dict) for item in projections
    ):
        raise RecordingContractError("recording backup projections must be a JSON object list")

    for source_key in selected:
        manifest = manifests.get(source_key)
        descriptor = descriptors.get(source_key)
        streams = streams_by_source.get(source_key)
        if not isinstance(manifest, dict) or not isinstance(descriptor, dict) or not isinstance(streams, list) or not all(
            isinstance(stream, dict) for stream in streams
        ):
            raise RecordingContractError(
                f"recording source {source_key!r} has an invalid persisted contract"
            )
        expected_manifest_hash = _canonical_sha256(manifest)
        if descriptor.get("manifest_sha256") != expected_manifest_hash:
            raise RecordingContractError(
                f"recording source {source_key!r} manifest SHA-256 does not match"
            )
        if descriptor.get("plugin_version") != str(manifest.get("version") or ""):
            raise RecordingContractError(
                f"recording source {source_key!r} plugin version does not match"
            )
        if descriptor.get("api_version") != int(manifest.get("api_version") or 0):
            raise RecordingContractError(
                f"recording source {source_key!r} API version does not match"
            )
        origin = descriptor.get("stream_contract_origin")
        if origin not in {"manifest", "runtime"}:
            raise RecordingContractError(
                f"recording source {source_key!r} stream contract origin is invalid"
            )
        if origin == "manifest" and manifest.get("streams") != streams:
            raise RecordingContractError(
                f"recording source {source_key!r} manifest stream snapshot disagrees"
            )
        _validate_stream_contract(source_key, streams)

    for projection in projections:
        plugin_key = str(projection.get("plugin_key") or "").strip()
        if not plugin_key or plugin_key not in selected:
            raise RecordingContractError(
                f"recording backup projection references unselected source {plugin_key!r}"
            )
    active_plugins = backup.get("active_plugins")
    if active_plugins != selected:
        raise RecordingContractError(
            "recording backup active_plugins do not match selected sources"
        )
    if not isinstance(backup.get("source_rates_hz"), dict):
        raise RecordingContractError("recording backup source_rates_hz must be a JSON object")
    channel_names = backup.get("channel_names")
    if not isinstance(channel_names, list) or not all(
        isinstance(name, str) and name for name in channel_names
    ):
        raise RecordingContractError("recording backup channel_names must be a list")
    quality_channels = backup.get("quality_channels")
    if not isinstance(quality_channels, list) or not all(
        isinstance(name, str) and name for name in quality_channels
    ):
        raise RecordingContractError("recording backup quality_channels must be a list")
    try:
        rate_hz = float(backup.get("rate_hz"))
    except (TypeError, ValueError) as error:
        raise RecordingContractError("recording backup rate_hz must be numeric") from error
    if not math.isfinite(rate_hz) or rate_hz <= 0:
        raise RecordingContractError("recording backup rate_hz must be positive and finite")
    if not str(backup.get("artifact_role") or "").strip():
        raise RecordingContractError("recording backup artifact_role is missing")
    if not str(backup.get("resampling_strategy") or "").strip():
        raise RecordingContractError("recording backup resampling_strategy is missing")

    plan_selected = plan.get("recording_plugins")
    plan_required = plan.get("required_source_keys")
    if plan_selected != selected or plan_required != required:
        raise RecordingContractError(
            "recording plan source keys disagree with its immutable contract"
        )
    return contract


def recording_contract_sha256(contract: Mapping[str, Any]) -> str:
    """Hash every contract field except the digest itself in canonical JSON."""

    payload = deepcopy(dict(contract))
    payload.pop("sha256", None)
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise RecordingContractError(
            f"recording contract is not canonical JSON: {error}"
        ) from error
    return hashlib.sha256(encoded).hexdigest()


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise RecordingContractError(f"manifest is not canonical JSON: {error}") from error
    return hashlib.sha256(encoded).hexdigest()


def _validate_stream_contract(source_key: str, streams: Iterable[Mapping[str, Any]]) -> None:
    stream_keys: set[str] = set()
    source_ids: set[str] = set()
    for stream in streams:
        key = str(stream.get("key") or "").strip()
        source_id = str(stream.get("source_id") or "").strip()
        channels = stream.get("channels")
        units = stream.get("channel_units")
        if not key or not source_id:
            raise RecordingContractError(
                f"recording source {source_key!r} has a stream without key/source_id"
            )
        if key in stream_keys or source_id in source_ids:
            raise RecordingContractError(
                f"recording source {source_key!r} has duplicate stream identities"
            )
        if not isinstance(channels, list) or not channels or not all(
            isinstance(channel, str) and channel.strip() for channel in channels
        ):
            raise RecordingContractError(
                f"recording source {source_key!r} stream {key!r} has invalid channels"
            )
        if not isinstance(units, list) or len(units) != len(channels) or not all(
            isinstance(unit, str) and unit.strip() for unit in units
        ):
            raise RecordingContractError(
                f"recording source {source_key!r} stream {key!r} has invalid channel units"
            )
        try:
            rate_hz = float(stream.get("nominal_rate_hz"))
        except (TypeError, ValueError) as error:
            raise RecordingContractError(
                f"recording source {source_key!r} stream {key!r} has invalid rate"
            ) from error
        if not math.isfinite(rate_hz) or rate_hz < 0:
            raise RecordingContractError(
                f"recording source {source_key!r} stream {key!r} has invalid rate"
            )
        stream_keys.add(key)
        source_ids.add(source_id)


def _unique_keys(values: Iterable[str], *, field: str) -> list[str]:
    keys: list[str] = []
    for value in values:
        key = str(value or "").strip()
        if not key:
            raise RecordingContractError(f"{field} contains an empty source key")
        if key not in keys:
            keys.append(key)
    return keys


def _contract_key_list(contract: Mapping[str, Any], field: str) -> list[str]:
    values = contract.get(field)
    if not isinstance(values, list):
        raise RecordingContractError(f"recording contract {field} must be a list")
    keys = _unique_keys(values, field=field)
    if len(keys) != len(values):
        raise RecordingContractError(f"recording contract {field} contains duplicate keys")
    return keys
