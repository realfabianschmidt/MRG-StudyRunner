# Recording and finalization workflow

The server owns the session boundaries. The browser requests a start and submits
the answers; it does not independently decide when LSL recording starts or ends.

## Start

1. `apps/server/routes/study.py` creates or reuses the participant session and
   asks the sensor coordinator to start only the study's selected plugins.
2. A sensor may opt into a status condition through
   `study_sensor.start_condition` in its manifest. The server checks only
   conditions declared by selected required sensors. The worker does not
   interpret device-specific readiness.
3. `RecordingRuntimeService.start_session` freezes the effective plugin,
   stream, and machine wait settings in `meta/recording-plan.json`. The worker
   opens the XDF sources and reports their state. One host gate checks headers
   and real samples according to each recording source's manifest policy.
4. The server records one idempotent `study_start` marker. A failed recording
   start or marker leaves the session available for a retry with the same ID.

Sensors may begin streaming before the participant starts. The two study
markers define the analysis window; the worker keeps every source's original
timestamps and rates. A delayed or missing sample is never invented to fill
the window.

## Plugin modes

The manifest selects the mode without a sensor-name branch in the core:

| Manifest declaration | Start and end behavior |
| --- | --- |
| `study_sensor.start_condition` | Check the named field in this plugin's status before starting; plugins without the declaration have no such status gate. |
| `recording_source.start_sample_policy: all_regular` (default) | Wait for fresh, recorded samples from every regular stream before the start marker. |
| `recording_source.start_sample_policy: primary_only` | Wait for the regular primary stream; report other late regular streams as quality warnings. |
| `recording_source.start_sample_policy: headers_only` | Require XDF headers without a sample gate; suitable for event-only sources. |
| Any source with only 0 Hz event streams | Require XDF headers, but no event at either boundary. |
| `study_sensor` without `recording_source` | Run its session lifecycle callback; no XDF source or sample gate is invented. |
| `upload_destination` | Publish only after local completion; each destination has its own retry state. |

The session store retains the chosen sensor membership across a server restart.
The recording plan retains the exact selected recording streams and their
manifest contracts.

Inside each plugin, `plugin_framework/runtime_contract.py` derives the allowed
commands from the manifest and checks that each declared callback exists before
the child process serves requests. The process host handles crashes and RPC
timeouts; the sensor coordinator handles selected sensor start/stop and reports
initialization failures; `sensor_connection.py` turns device facts into the
shared ready/next-step status. Each adapter owns only its hardware protocol,
sample timestamps, and device-specific reconnection method. Cards and outputs
receive only their declared trial callbacks; destinations receive `publish`
only after local completion.

## End

1. `FinalizationService` commits the submission and runs its ordered core
   steps. `RuntimeRecordingFinalizationAdapter.freeze` writes one `study_end`
   marker, waits for regular streams to pass it for the session's recorded
   `end_tail_wait_seconds`, freezes the XDF writer, then
   sends `session_end` only to plugins recorded for this session. These sensors
   can keep streaming for the next participant.
2. Source validation checks coverage and stream integrity. Quality warnings
   wait for an admin's reason; accepted warnings allow the remaining local
   derivation to finish with `completed_degraded`. Blocking failures can be
   closed as degraded while retaining the available raw data.
3. Merge, card summary, CSV, result manifest, journal archive, and local
   completion marker finish before any destination publishes.
4. Destination steps are discovered from plugin manifests. They queue
   independent upload jobs; `UploadJobService` can execute several due jobs
   concurrently. A remote failure stays visible and retryable without changing
   the local scientific result. Remote publication never runs while the local
   job is `attention_required`.

The participant goes to the configured finish card after submitting. If the
server has not acknowledged the submission, the tablet retains its exact
payload locally and retries with the same submission ID on reconnection and
while the page remains open. The admin view carries the processing and upload
state.

## Clock core and responsibilities

`software/study_runner/clock_core/` is the only code that relates clocks. Each
clock has one owner, and only comparable readings are compared:

| Clock | Owner | Compared with |
| --- | --- | --- |
| A plugin's source timeline (`SourceClock`, on `perf_counter`) | the plugin | nothing directly; mapped to this computer's LSL clock with `SourceToLsl` before publishing |
| An outlet's LSL clock (the timestamps a plugin pushes through `SensorStreams`) | the plugin | the recorder's clock only through the worker's `time_correction` |
| The recorder's LSL clock (`lsl_now`, receipt time `last_receipt_lsl`) | the recording worker | marker times and corrected stream times |
| Marker times (`markers.py`, `WallToLsl`) | the server, on the recording computer | the recorder's clock directly (same computer) |
| The worker's monotonic clock | the worker | sample age and freshness only |
| The participant browser's clock | the participant page | the server only through the clock-sync estimate |

Rules, used by the live barriers and the offline validation alike
(`clock_core/assessment.py`):

- The start barrier waits within its window for each regular stream's first LSL
  correction, then compares the newest sample's corrected timestamp with the
  recorder's clock. A stream still without a correction starts with
  `clock_uncertain_at_start`.
- The end barrier compares a stream's corrected last timestamp with the end
  marker. Without a correction it compares only the recorder's receipt time and
  reports the stream in `receipt_only_streams`.
- Offline, pyxdf's clock-synchronized view is used for coverage and the
  marker-window sample count only when a stream has recorded corrections; raw
  timestamps stay untouched in the files. A stream without corrections is
  `clock_alignment_uncertain`: reviewed by an admin, never counted as data loss.
- Every regular stream's timeline discontinuities (jumps longer than three
  nominal periods) and correction statistics are measured from the XDF itself.
- `meta/clock-report.json` records the declared `timestamp_source` per stream,
  the alignment state, corrections, discontinuities, the marker window, and both
  barrier outcomes. It holds corrections, never a merged time, and is
  reproducible from the XDF.

A plugin follows five rules, enforced for every plugin by
`tests/test_plugin_clock_contract.py`: declare `timing.timestamp_source` per
stream; publish only through `SensorStreams`; never stamp a sample from a host
clock directly; rebuild a callback timeline with `callback_batch_start`,
`SourceClock` and `SourceToLsl`; declare a `correction_channel` for a corrected
arrival time.

## Sensor ownership between participants

`runtime_core/studies/sensor_session_ownership.py` records whose
per-participant state each sensor holds. A session claims its sensors at start;
a contact or calibration action outside the running session claims the sensor
for the next person. Finalization sends `session_end` for a session only to
sensors that session still owns, and a late call for an older session leaves the
current session untouched. A `session_end` that timed out has an unknown
outcome: the sensor refuses setup and a new session (`sensor_reset_pending`)
until the plugin's status (`session_end.last_completed`, kept by
`plugin_framework/driver_runtime.py` for every plugin) confirms it.

