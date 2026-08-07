"""The study's own event-marker LSL outlet.

Every recording carries this stream unconditionally: it is how the study
lifecycle and card events get a timestamp inside the XDF, not something a study
author turns on or off. It lives here, in `recording/`, rather than under
`plugins/`, because it fails the test that defines a plugin --
`tests/test_plugin_removability.py` -- there is no software left to run
without it.

`markers.manifest.json` sits beside this file and is loaded through
`plugin_catalog.validate_and_normalize_manifest`, the exact function real
plugin manifests go through. That keeps the declared stream, its capabilities,
and this module's own constants from a single source instead of two hand-kept
copies -- but the manifest is never discovered from a directory scan and never
imported through an entry point, because there is nothing here to remove.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from study_runner.plugin_framework.dependency_utils import ensure_requirements
from study_runner.plugin_framework.plugin_catalog import validate_and_normalize_manifest


def _load_manifest() -> dict[str, Any]:
    path = Path(__file__).resolve().parent / "markers.manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return validate_and_normalize_manifest(payload, directory_name="markers")


MANIFEST = _load_manifest()
SOURCE_KEY = str(MANIFEST["plugin_key"])
CONFIG_KEY = str(MANIFEST["config_key"])
LSL_SOURCE_IDS = {stream["key"]: stream["source_id"] for stream in MANIFEST["streams"]}
LSL_CHANNEL_UNITS = {stream["key"]: tuple(stream["channel_units"]) for stream in MANIFEST["streams"]}

_outlet: Any = None


def initialize(hardware_config: Mapping[str, Any] | None = None) -> None:
    """Create the LSL outlet. Called once at server startup."""
    global _outlet
    config = hardware_config.get(CONFIG_KEY) if isinstance(hardware_config, Mapping) else None
    config = config if isinstance(config, Mapping) else {}
    stream_name = str(config.get("stream_name") or "StudyRunner")
    stream_type = str(config.get("stream_type") or "Markers")
    auto_install = bool(config.get("auto_install", True))
    if not ensure_requirements(
        [("pylsl", "pylsl")],
        auto_install=auto_install,
        label="LSL",
    ):
        return
    try:
        from pylsl import StreamInfo, StreamOutlet

        info = StreamInfo(
            name=stream_name,
            type=stream_type,
            channel_count=1,
            nominal_srate=0,  # irregular rate - markers are event-driven
            channel_format="string",
            source_id=LSL_SOURCE_IDS["markers"],
        )
        channel = info.desc().append_child("channels").append_child("channel")
        channel.append_child_value("label", "event")
        channel.append_child_value("unit", LSL_CHANNEL_UNITS["markers"][0])
        _outlet = StreamOutlet(info)
        print(f"[MARKERS] Outlet ready: {stream_name} ({stream_type})")
    except ImportError:
        print("[MARKERS] pylsl import failed after dependency check.")


def stop() -> None:
    """Release the module-level LSL outlet."""
    global _outlet
    _outlet = None


def send_marker(value: str) -> None:
    """Push a string marker to the LSL outlet. Does nothing if LSL is not active."""
    if _outlet is not None:
        _outlet.push_sample([value])
        print(f"[MARKERS] Marker sent: {value}")


def status() -> dict[str, Any]:
    return {
        "status": "enabled" if _outlet is not None else "waiting",
        "runtime_enabled": _outlet is not None,
        "device_label": "Study markers",
    }
