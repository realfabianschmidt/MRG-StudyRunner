"""Push synthetic samples for a sensor extension's declared LSL stream.

Testing a new sensor extension usually needs the real device plugged in.
This script reads the stream contract straight out of the extension's own
`manifest.json` -- the same values `validate` already checked -- and
publishes plausible-looking samples on LSL, the way the real device would.
It does not invent a second description of the stream; it reads the one
the manifest already has.

Usage:
    python tools/synthetic_lsl_source.py <plugin_dir> [--stream KEY]
                                          [--count N] [--rate-hz HZ]

Needs `pylsl`, already a pinned project dependency (see
software/constraints/py312-common.txt).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SOFTWARE_ROOT = REPO_ROOT / "software"
if str(SOFTWARE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOFTWARE_ROOT))

from study_runner.contracts.manifest import validate_and_normalize_manifest  # noqa: E402

DEFAULT_SAMPLE_COUNT = 50
DEFAULT_RATE_HZ = 10.0


def _load_streams(plugin_dir: Path) -> list[dict[str, Any]]:
    manifest_path = plugin_dir / "manifest.json"
    raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = validate_and_normalize_manifest(raw_manifest, directory_name=plugin_dir.name)
    return manifest["streams"]


def _pick_stream(streams: list[dict[str, Any]], stream_key: str | None) -> dict[str, Any]:
    if not streams:
        raise ValueError("this manifest declares no streams -- it is not a sensor extension")
    if stream_key is None:
        if len(streams) > 1:
            keys = ", ".join(stream["key"] for stream in streams)
            raise ValueError(f"more than one stream declared ({keys}) -- pass --stream")
        return streams[0]
    for stream in streams:
        if stream["key"] == stream_key:
            return stream
    raise ValueError(f"no stream named {stream_key!r} in this manifest")


def _synthetic_value(channel_format: str, channel_index: int, sample_index: int) -> Any:
    """One plausible-looking value per channel -- not real data, just something
    that moves, so a dashboard or recording shows visible signal instead of
    a flat line."""
    if channel_format == "string":
        return f"sample-{sample_index}"
    # A slow sine wave per channel, offset so channels don't overlap exactly.
    phase = channel_index * 0.6
    value = math.sin(sample_index * 0.2 + phase)
    if channel_format in {"int8", "int16", "int32", "int64"}:
        return int(round(value * 100))
    return float(value)


def push_synthetic_samples(
    plugin_dir: Path,
    *,
    stream_key: str | None = None,
    count: int = DEFAULT_SAMPLE_COUNT,
    rate_hz: float | None = None,
) -> str:
    """Publish `count` synthetic samples for one of a manifest's streams.

    Returns the stream key that was used, so a caller (or this module's own
    CLI) can report exactly what happened.
    """
    import pylsl

    stream = _pick_stream(_load_streams(plugin_dir), stream_key)
    channels: list[str] = stream["channels"]
    channel_format = stream["channel_format"]
    effective_rate = rate_hz or stream["nominal_rate_hz"] or DEFAULT_RATE_HZ

    info = pylsl.StreamInfo(
        stream["key"],
        stream.get("type") or "SYNTHETIC",
        len(channels),
        effective_rate,
        getattr(pylsl, f"cf_{channel_format}"),
        stream["source_id"],
    )
    channel_descriptions = info.desc().append_child("channels")
    for label, unit in zip(channels, stream["channel_units"]):
        channel = channel_descriptions.append_child("channel")
        channel.append_child_value("label", label)
        channel.append_child_value("unit", unit)

    outlet = pylsl.StreamOutlet(info)
    delay = 1.0 / effective_rate if effective_rate > 0 else 0.0
    for sample_index in range(count):
        sample = [
            _synthetic_value(channel_format, index, sample_index)
            for index in range(len(channels))
        ]
        outlet.push_sample(sample)
        if delay:
            time.sleep(delay)
    return stream["key"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plugin_dir", type=Path)
    parser.add_argument("--stream", default=None, help="Stream key, if the manifest declares more than one")
    parser.add_argument("--count", type=int, default=DEFAULT_SAMPLE_COUNT)
    parser.add_argument("--rate-hz", type=float, default=None, help="Default: the stream's own nominal_rate_hz")
    args = parser.parse_args(argv)

    try:
        used_key = push_synthetic_samples(
            args.plugin_dir.resolve(),
            stream_key=args.stream,
            count=args.count,
            rate_hz=args.rate_hz,
        )
    except Exception as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"OK: pushed {args.count} synthetic samples on stream {used_key!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
