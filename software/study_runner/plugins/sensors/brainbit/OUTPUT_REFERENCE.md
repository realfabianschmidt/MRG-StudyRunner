# BrainBit CLI output contract

This file documents the line protocol emitted by `brainbit_realtime_cli.py`.
Values below are schematic examples, not captured participant data.

Every machine-readable line has a short tag, one space, and a strict JSON
object. Non-finite SDK values such as an open-circuit resistance are encoded as
`null`, never the non-standard JSON token `Infinity`.

## Device discovery

```text
SCAN {"index":0,"name":"BrainBit","family":"LEBrainBit","address":"...","serial":"...","pairing_required":false,"rssi":-55}
DEVICE_SELECTED {"index":0,"name":"BrainBit","family":"LEBrainBit","address":"...","serial":"...","selection_source":"serial_number"}
DEVICE {"family":"LEBrainBit","name":"BrainBit","address":"...","serial_number":"...","fs_hz":250,"scale":"uV","raw_processing":"unit_scale_only","supported_channels":[]}
```

Configured identities are strict. If the configured band is absent, another
band is not substituted:

```text
DEVICE_TARGET_MISSING {"message":"Configured BrainBit target not found: serial_number '...' not found","target":{"serial_number":"..."},"fallback":null}
```

The process exits with code 6.

BrainBit2, Pro, and Flex additionally report their SDK array mapping. `index`
is `EEGChannelInfo.Num`, the position used in `SignalChannelsData.Samples`:

```text
CHANNEL_MAP {"channels":[{"label":"O1","index":0,"id":"EEGChIdO1","type":"EEGChTypeDifferential"},{"label":"O2","index":1,"id":"EEGChIdO2","type":"EEGChTypeDifferential"},{"label":"T3","index":2,"id":"EEGChIdT3","type":"EEGChTypeDifferential"},{"label":"T4","index":3,"id":"EEGChIdT4","type":"EEGChTypeDifferential"}],"raw_channels":["O1","O2","T3","T4"],"raw_channel_count":4,"fs_hz":250,"derived_rate_hz":25,"units":"uV","derived_required_channels":["O1","O2","T3","T4"],"derived_enabled":true,"missing_derived_channels":[]}
```

Every valid raw channel is retained in SDK order. Missing O1/O2/T3/T4 disables
only the derived streams; duplicate labels or indices are configuration
failures because their identity is ambiguous.

## Resistance and contact quality

```text
STREAM {"stream":"resist","event":"START"}
RESIST {"ts":1786000000.1,"pack":12,"O1":180000.0,"O2":220000.0,"T3":null,"T4":195000.0,"units":"Ohm","packet_shape":"classic_fields","open_channels":["T3"]}
QUALITY {"O1":0.932,"O2":0.917,"T3":0.0,"T4":0.927,"units":"ratio","resistance_upper_ohm":2666000.0,"quality_model":"linear_diagnostic_only"}
STREAM {"stream":"resist","event":"STOP"}
```

`QUALITY` is a convenience ratio from 0 to 1, not a manufacturer-validated
quality metric. The raw `RESIST` values and open-channel flags remain visible;
do not use the linear ratio as a scientific contact threshold.

## Raw EEG chunks

```text
STREAM {"stream":"eeg","event":"START","fs_hz":250}
EEG_BATCH {"ts":1786000001.000,"end_ts":1786000001.012,"sample_interval_sec":0.004,"sample_count":4,"channels":["O1","O2","T3","T4"],"samples":[[12.1,-5.2,8.0,-3.0],[12.4,-5.0,8.2,-2.9],[12.7,-4.8,8.3,-2.7],[12.9,-4.7,8.5,-2.6]],"timestamps":[1786000001.000,1786000001.004,1786000001.008,1786000001.012],"packs":[101,102,103,104],"markers":[0,0,0,0],"packet_gap_frames":0,"packet_gap_frames_total":0,"packet_counter_reset_total":0,"packet_counter_events":[],"packet_shapes":["classic_fields"],"units":"uV","source_units":"V","processing":"unit_scale_only","timestamp_source":"host_callback_reconstructed","preview":{"O1":0.8,"O2":-0.2,"T3":0.5,"T4":-0.1},"measured_hz":249.87,"queue_overflow_dropped_total":0}
```

- `samples` is the canonical raw stream: only a reversible unit scale is
  applied. It is forwarded to LSL as one chunk with explicit timestamps.
- `preview` is detrended and optionally mains-notched for OSC/operator display.
  It must not be used as the scientific raw recording.
- `packs` and `markers` preserve the SDK packet metadata for dropout and
  device-marker diagnosis.
- Timestamps are reconstructed at the nominal sample interval from host
  callback arrival because NeuroSDK does not provide a timestamp per sample.
  Observable packet-counter gaps reserve the missing intervals rather than
  compressing time silently.
- `measured_hz` is samples emitted divided by elapsed wall time since the
  stream started (the achieved rate, not the nominal 250 Hz); `None` until
  at least one sample has been emitted.
- `queue_overflow_dropped_total` counts samples ever dropped because the
  in-process pending-EEG queue exceeded its 10-second cap (a stalled
  consumer). Should stay `0`; a rising count means the host is not reading
  fast enough, not that the sensor itself is misbehaving.

Malformed frames, counter gaps, duplicates, or resets are visible separately:

```text
DATA_WARNING {"phase":"signal_integrity","discarded_frames":1,"packet_gap_frames":2,"packet_gap_frames_total":2,"packet_counter_reset_total":0,"packet_counter_events":[{"gap_before":2,"counter_event":"gap","previous_pack":101,"current_pack":104}]}
```

A stalled consumer that forced the queue cap to drop samples reports the
same way, tagged `eeg_queue_overflow`:

```text
DATA_WARNING {"phase":"eeg_queue_overflow","dropped_samples":37,"dropped_samples_total":37}
```

The CLI buffers roughly 100 ms before writing an `EEG_BATCH`, avoiding 250
flushed terminal and disk writes per second.

## Calibration and derived values

```text
CALIB {"event":"START","target_sec":6}
CALIB {"progress_percent":42.0}
CALIB {"event":"FINISHED"}
BANDS_BATCH {"ts":1786000007.012,"end_ts":1786000007.052,"sample_count":2,"channels":["delta","theta","alpha","beta","gamma"],"samples":[[0.12,0.25,0.31,0.22,0.10],[0.11,0.26,0.32,0.21,0.10]],"timestamps":[1786000007.012,1786000007.052]}
MENTAL_BATCH {"ts":1786000007.012,"end_ts":1786000007.052,"sample_count":2,"channels":["Inst_Attention","Inst_Relaxation","Rel_Attention","Rel_Relaxation"],"samples":[[0.64,0.36,0.61,0.39],[0.65,0.35,0.62,0.38]],"timestamps":[1786000007.012,1786000007.052]}
```

The deployed `pyem_st_artifacts` API receives bipolar samples through
`push_bipolars`. Current result fields are lowercase internally and are mapped
to the stable output names above.

Derived arrays are emitted and forwarded to LSL as timestamped batches. This
retains a 25 Hz backlog without performing one flushed terminal write per
result inside the native SDK callback. The dashboard keeps only the latest row;
the full-rate rows remain in LSL/XDF and the sidecar is an explicit 1 Hz backup.

EmotionalMath has no force-finish function. A stalled calibration is reported
honestly while raw EEG continues:

```text
CALIB {"event":"STALLED","reason":"timeout","last_progress_percent":4.0}
```

No derived values are labeled valid until the native library reports that
calibration actually finished.

## Failures

Before any device scan or connection begins, the CLI checks that the pinned
`pyem-st-artifacts` wheel actually exposes `EmotionalMath.push_bipolars`.
Exit code 2 either way, but this check fails at startup rather than only on
the first EEG batch of an already-running session:

```text
SETUP_FAIL {"missing_api":"EmotionalMath.push_bipolars","message":"Pinned pyem-st-artifacts wheel does not expose EmotionalMath.push_bipolars; refusing to start."}
```

Callback exceptions cannot propagate out of NeuroSDK's ctypes callback. The
CLI catches them, reports exactly one structured failure, stops the stream, and
exits non-zero:

```text
CALLBACK_ERROR {"phase":"signal","error_type":"AttributeError","error":"..."}
STREAM_ERROR {"error_type":"RuntimeError","error":"signal callback failed: ...","callback_failure":true}
```

Relevant exit codes:

| Code | Meaning |
|---:|---|
| 0 | intentional or duration-limited stop |
| 2 | missing dependency |
| 5 | no compatible device found |
| 6 | configured target missing |
| 7 | callback/data-processing failure |
| 8 | stream or device configuration failure |
| 103 | Bluetooth unavailable |

The adapter health model reports log output, raw EEG, derived metrics, data
integrity, contact, and successful LSL publication separately. Battery lines,
warnings, outlet existence, and tracebacks do not count as fresh recorded EEG.
