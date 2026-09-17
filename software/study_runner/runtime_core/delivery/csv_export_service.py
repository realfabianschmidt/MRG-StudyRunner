"""CSV export of the backup grid: a convenience projection for SPSS/R.

Reuses the already-synchronized "slowest grid" backup recording (see
``recording_runtime.py``'s ``_build_backup_contract``/``BackupSampler``)
instead of reinventing multi-sensor synchronization -- every sensor is
already resampled onto one shared, slower time grid for crash-recovery/QC
purposes, so this step is a pure format conversion, not new signal
processing. The raw per-plugin XDF stays the source of truth and can be
reprocessed at any resolution later; this CSV is never authoritative.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from study_runner.runtime_core.studies.card_summary_service import (
    CardSummaryError,
    PyXdfSampleReader,
    SampleReader,
)


class CsvExportError(RuntimeError):
    """The backup grid could not be converted to CSV."""


def write_backup_csv(
    backup_xdf_path: Path,
    target_csv_path: Path,
    *,
    sample_reader: SampleReader | None = None,
) -> dict[str, Any]:
    """Read the backup XDF's synchronized grid and write it as one CSV.

    One row per grid timestamp, one column per channel across every sensor
    -- the same wide, SPSS/R-friendly shape the backup grid already has.
    """

    reader = sample_reader or PyXdfSampleReader()
    try:
        streams = list(reader.read_streams(backup_xdf_path))
    except CardSummaryError as error:
        raise CsvExportError(f"Could not read the backup XDF: {error}") from error

    data_streams = [stream for stream in streams if stream.get("channels")]
    if not data_streams:
        raise CsvExportError("Backup XDF has no channel data to export.")
    # The backup grid is one LSL outlet with every sensor's channels; a
    # second, channel-less stream (e.g. boundaries) is possible but never
    # the one worth exporting.
    stream = max(data_streams, key=lambda candidate: len(candidate["channels"]))

    fieldnames = ["timestamp", *stream["channels"]]
    target_csv_path.parent.mkdir(parents=True, exist_ok=True)
    with target_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for timestamp, sample in zip(stream["timestamps"], stream["samples"]):
            row = {"timestamp": timestamp}
            row.update(sample)
            writer.writerow(row)

    return {
        "path": target_csv_path,
        "row_count": len(stream["timestamps"]),
        "channel_count": len(stream["channels"]),
    }
