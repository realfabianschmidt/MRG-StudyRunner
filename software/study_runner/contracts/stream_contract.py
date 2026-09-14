"""Turn one manifest-declared stream contract into XDF header fields.

Package 5d (docs/archive/architecture-1.0-umbau.md): every LSL-producing module
calls this before creating its outlet, so the ``desc/study_runner/...``
namespace in the recorded XDF carries one single-source-of-truth
projection of the stream's identity and timing provenance, instead of a
hand-copied version of it in five different adapters. Read back by
``card_summary_service.py::_study_runner_metadata``.

Deliberately a flat string dict: ``pylsl``'s ``desc()`` tree only stores
text, and keeping the mapping here (not duplicated per adapter) is what
makes the header schema change in one place, together with T8's fixture
update, rather than six.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def stream_contract_desc_fields(stream: Mapping[str, Any]) -> dict[str, str]:
    """Flat fields for one ``desc/study_runner`` XDF header block.

    ``stream`` is a manifest-shaped stream dict (see
    ``contracts.manifest._normalize_streams``): must have ``source_id`` and
    ``clock_domain``, and a ``timing.capture_delay_ns`` block that is always
    present after normalization (honestly ``source: "unknown"`` when no
    adapter has ever measured it -- see ``_normalize_stream_timing``'s own
    docstring for why that default is deliberate, not a placeholder to fill
    in later).
    """
    timing = stream.get("timing") or {}
    delay = timing.get("capture_delay_ns") or {}
    fields = {
        "source_id": str(stream.get("source_id") or ""),
        "clock_domain": str(stream.get("clock_domain") or ""),
        "capture_delay_source": str(delay.get("source") or "unknown"),
    }
    if delay.get("min_ns") is not None:
        fields["capture_delay_min_ns"] = str(delay["min_ns"])
    if delay.get("max_ns") is not None:
        fields["capture_delay_max_ns"] = str(delay["max_ns"])
    if delay.get("reference"):
        fields["capture_delay_reference"] = str(delay["reference"])
    return fields


def apply_stream_contract_desc(info: Any, stream: Mapping[str, Any]) -> None:
    """Append the ``study_runner`` desc block to a ``pylsl.StreamInfo``.

    Takes the already-constructed ``StreamInfo`` rather than a ``pylsl``
    type directly, so this module has no import-time dependency on
    ``pylsl`` itself -- callers that already imported it (every LSL
    producer already does) just pass the object through.
    """
    node = info.desc().append_child("study_runner")
    for key, value in stream_contract_desc_fields(stream).items():
        node.append_child_value(key, value)


def load_own_stream_contracts(adapter_file: str) -> dict[str, dict[str, Any]]:
    """Load and normalize the ``manifest.json`` beside ``adapter_file``.

    A v4 plugin's own subprocess never sees the host's already-parsed
    manifest -- only the host validates manifests for discovery
    (``plugin_catalog.py``). An adapter that wants its own frozen stream
    contract for the XDF header (this module's whole purpose) loads and
    normalizes its manifest independently, the same way
    ``data_core/host/markers.py`` already does for itself. Pass
    ``__file__`` from the calling adapter module.
    """
    from study_runner.contracts.manifest import validate_and_normalize_manifest

    manifest_path = Path(adapter_file).resolve().parent / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = validate_and_normalize_manifest(payload, directory_name=manifest_path.parent.name)
    return {stream["key"]: stream for stream in manifest["streams"]}
