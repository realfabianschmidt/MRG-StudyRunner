"""Which clock each reading belongs to, and which readings may be compared.

Ownership:

- A plugin owns its source timeline and declares its kind per stream in the
  manifest (``timing.timestamp_source``). It reaches the LSL clock only
  through ``clock_core.producer`` and publishes only through ``SensorStreams``.
- The recording worker owns the relation between each outlet's LSL clock and
  its own (``time_correction``) and the receipt time of every sample.
- The server's internal sources (study markers, clock diagnostics) push on the
  recording computer's LSL clock, which is the recorder's clock.
- ``clock_core.assessment`` is the only code that compares clocks. The live
  start/end barriers and the offline validation use the same functions.

Two readings are compared only when both are on the recorder's LSL clock,
directly or through a known correction. Without one the result is
``uncertain``: reported for review, never as data loss or as proven alignment.
A merged "global" time is never persisted; reports keep the corrections.
"""
from __future__ import annotations

from study_runner.contracts.sensor_contract import TIMESTAMP_SOURCES

__all__ = [
    "TIMESTAMP_SOURCES",
    "CORRECTED",
    "LOCAL",
    "UNCERTAIN",
    "BASIS_CORRECTED",
    "BASIS_RECEIPT",
    "CORRECTION_INTERVAL_SECONDS",
    "CORRECTION_MAX_AGE_SECONDS",
    "CALLBACK_JITTER_TOLERANCE_SECONDS",
    "CALLBACK_MAX_SLEW_FRACTION",
    "WALL_CLOCK_STEP_TOLERANCE_SECONDS",
    "DISCONTINUITY_PERIODS",
]

# How a recorded stream's timestamps relate to the recorder's LSL clock.
CORRECTED = "corrected"  # an LSL time_correction is known
LOCAL = "local"  # pushed on the recorder's own computer by the server itself
UNCERTAIN = "uncertain"  # no usable correction

# What an end-of-recording coverage claim rests on.
BASIS_CORRECTED = "corrected_timestamp"
BASIS_RECEIPT = "recorder_receipt"

# The worker asks every inlet for its correction at this interval; one that
# missed three rounds no longer describes the outlet's clock.
CORRECTION_INTERVAL_SECONDS = 5.0
CORRECTION_MAX_AGE_SECONDS = 3 * CORRECTION_INTERVAL_SECONDS

# Host callbacks arrive with scheduling jitter. Disagreement beyond this between
# the nominal timeline and the receipt time is a pause, not jitter.
CALLBACK_JITTER_TOLERANCE_SECONDS = 0.1

# Inside that tolerance a reconstructed timeline follows the receipt times by
# stretching or compressing its sample spacing by at most this fraction. A
# device whose real rate differs from its nominal one (BrainBit: ~250.47 Hz
# for a nominal 250 Hz) then never drifts away, and receipt jitter cannot make
# the spacing irregular by more than 0.5 %.
CALLBACK_MAX_SLEW_FRACTION = 0.005

# A wall-clock change larger than this invalidates a wall-to-LSL mapping.
WALL_CLOCK_STEP_TOLERANCE_SECONDS = 0.5

# A regular stream whose consecutive timestamps differ by more than this many
# nominal periods has a timeline discontinuity.
DISCONTINUITY_PERIODS = 3.0
