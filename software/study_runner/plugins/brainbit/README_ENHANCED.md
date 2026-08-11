# BrainBit acquisition: advanced notes

Use `README.md` for operator setup and `OUTPUT_REFERENCE.md` for the current
line protocol. This file records the implementation constraints that matter
when extending or reproducing the integration.

## Runtime layers

1. `driver.py` is the thin API-v4 plugin-process entry point.
2. `plugin.py` configures the BrainBit adapter inside that process.
3. `adapter.py` supervises `brainbit_realtime_cli.py`, keeps low-rate state,
   rotates diagnostics, and publishes timestamped chunks to LSL.
4. `brainbit_realtime_cli.py` owns NeuroSDK Bluetooth access and
   `pyem_st_artifacts` processing.

Only one process or manufacturer application may own the BLE sensor at a time.
Close the vendor application before starting Study Runner or the standalone
CLI.

## Supported SDK packet contracts

- Classic BrainBit and BrainBit Black provide named `O1`, `O2`, `T3`, and `T4`
  fields.
- BrainBit2, Pro, and Flex provide `Samples` arrays. The decoder uses each
  `EEGChannelInfo.Num` from `sensor.supported_channels`; list order is not used
  as an implicit mapping.
- The raw LSL contract is created only after `CHANNEL_MAP` and contains every
  device channel in the exact SDK `Num` order. No four-channel outlet is guessed
  before discovery.
- O1, O2, T3, and T4 are required only for the bipolar EmotionalMath path. A
  device without all four continues recording raw EEG and reports
  `DERIVED_DISABLED`; it is not left permanently in a warming-up state.
- Duplicate labels or indices remain fatal because there is then no safe way to
  identify a scientific channel.

## Raw versus preview data

NeuroSDK signal values arrive in volts. The `EEG_BATCH.samples` values are only
scaled to the configured output unit (`uV` by default); they are not detrended,
notched, normalized, or rounded before LSL publication.

The optional DC detrending and 50/60 Hz notch are applied only to the
`EEG_BATCH.preview` and OSC output. This separation keeps visualization useful
without irreversibly modifying the scientific raw stream.

NeuroSDK supplies packet numbers but no source timestamp per sample. The CLI
reconstructs timestamps at the reported sampling rate, preserves observable
`PackNum` gaps in the timeline, and publishes gap/reset counters. It uses a
monotonic clock for durations so an operating-system clock correction cannot
shorten calibration or a measurement stage. The adapter converts Unix sample
timestamps to the local LSL clock domain and calls `push_chunk` with all sample
timestamps.

## EmotionalMath contract

The deployed API uses:

- `EmotionalMath(MathLibSetting, ArtifactDetectSetting,
  MentalAndSpectralSetting)`;
- `push_bipolars(samples)`;
- lowercase result attributes such as `delta`, `theta`, and
  `inst_attention`;
- `set_squared_spectrum(True)` after construction.

There is no native force-finish calibration function. `CALIB STALLED` means raw
EEG remains recordable but derived values are not ready.

## Standalone diagnostic run

From `software/`:

```powershell
python study_runner\plugins\brainbit\brainbit_realtime_cli.py --scan-seconds 10 --resist-seconds 10 --signal-seconds 30 --pretty --debug --no-osc
```

Useful evidence, in order:

1. `SCAN` and `DEVICE_SELECTED` identify the exact family.
2. `CHANNEL_MAP` validates array devices.
3. `RESIST` and normalized `QUALITY` distinguish open contacts.
4. `STREAM eeg START` followed by `EEG_BATCH` proves raw acquisition.
5. `CALIB FINISHED`, `BANDS_BATCH`, and `MENTAL_BATCH` prove the optional derived path.
6. `CALLBACK_ERROR` or `STREAM_ERROR` identifies code/SDK failures and results
   in a non-zero process exit.

Do not use terminal preview values as the study recording. Verify the stable
manifest source ID `study_runner.brainbit.eeg` in the resulting XDF.

## Sequential NeuroSDK / BrainFlow A-B check

`diagnose_backends.py` runs a bounded raw-signal check and writes comparable
reports. The same band cannot be owned by both backends simultaneously, so run
them one after another and close Study Runner and the manufacturer application
first. See `README.md` for exact commands. BrainFlow is diagnostic-only and is
never selected silently as a production fallback.
