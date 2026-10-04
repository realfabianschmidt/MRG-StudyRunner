# AM Hub

The `am_hub` plugin records the Parasite AM Hub (`MRG-ParasiteV2/am_hub`): the
radar board (LD2450 + LD2410B), the bio board (MR60) and the valve board, as
the hub forwards them over `GET {base_url}/api/v2/stream`. It follows the
sensor data contract like every sensor (`docs/plugin-recording-architecture.md`):
one numeric stream per measuring device, one text stream for everything else,
all published through the shared `SensorStreams`; the core's 1 Hz backup; and
the core's live view on the dashboard. The hub, the ESPs and their firmware
are not changed. A hub without API v2 is reported as unsupported.

The hub URL is a machine setting; whether a study uses the AM Hub is a study
setting.

## What is recorded

| Stream | One sample per | Channels |
| --- | --- | --- |
| `radar` | radar board frame (~10 Hz) | the 22 radar values in the hub's order (`personDist` ... `presDetDist`), `rssi`, `seq`, `hub_timestamp`, `latency_ms`, `correction_ms` |
| `bio` | bio board frame (~10 Hz) | `heartBpm`, `breathRate`, `bioDist`, `bioT1x`, `bioT1y`, `rssi`, `seq`, `hub_timestamp`, `latency_ms`, `correction_ms` |
| `valves` | valve board frame (on change + heartbeat) | `CH0` ... `CH7`, `rssi`, `seq`, `hub_timestamp`, `latency_ms`, `correction_ms` |
| `hub_events` | any other hub event | `event`: the event's JSON exactly as received |
| `hub_clock` | answered ping (about 1 per second) | `hub_clock_s`, `rtt_ms`, `exchange_offset_s`, `clock_offset_s`, `offset_uncertainty_ms`, `offset_valid`, `offset_steps`, `correction_enabled` |

- **One frame, one sample.** A sample exists only because the board sent a
  frame. There is no fixed tick, nothing is resampled or carried forward, and
  a value a frame does not contain is NaN. The channel names are the hub's
  topic names without their path (`/sensor/heartBpm` -> `heartBpm`).
- **Values as the ESP sends them.** No unit conversion and no cleanup: the
  declared units are the ESP's (positions mm, `bioDist` cm, target speed
  cm/s, energies arbitrary units). The ESP firmware repeats its latest values
  and sets them to 0 after its own timeout, so a 0 or a repeated value is not
  necessarily a new, independent measurement.
- **Parsed, not verbatim wire bytes.** The plugin decodes the AM Hub's v2 JSON
  event and projects its declared fields into typed numeric XDF channels.
  Numeric parsing, channel selection and optional timestamp correction are
  transformations; these streams must not be described as byte-for-byte
  copies of the hub response or of the radar UART frames. `hub_events` keeps
  the status/unknown events and unsupported fields described below; it is not
  a guaranteed duplicate of every board frame.
- **`rssi`** is filled only over WiFi (the board appends it to each frame);
  over BLE it is NaN and the connection strength is in the hub's status
  events. **`seq`** is the hub's frame counter per board and is the stream's
  sequence channel, so gaps show up in the quality review.
  **`hub_timestamp`** is the hub's own clock at the frame's arrival there.
- **`hub_events`** holds status, hello, valves, scene, gap and any unknown
  event types, like BrainBit's `diagnostics`. A frame with a value its board
  stream does not declare (for example `/solenoid/alive` over WiFi, or a new
  firmware field) is written there as well, so nothing the hub sent is lost.
- **Backup:** the standard 1 Hz backup projection (presence, movement energy,
  person distance, heart and breathing rate) is a derived file for quality
  checks and as a fallback -- never a measurement.
- **Card statistics** average the recorded frames of each card. A heart or
  breathing rate or a person distance of exactly 0 (the ESP's "no value") is
  left out of the average and counted in `zero_frames`; the XDF keeps it.

## Timing

By default the XDF timestamp of every sample is the local LSL time when the
event arrived at this computer (`timestamp_source: host_arrival_corrected`
with nothing corrected: `correction_ms` is 0). What separates that arrival
from the moment the board sent the frame is measured and recorded:

| Part | Measured by | How |
| --- | --- | --- |
| board -> hub (radio) | the hub | its ping to each board (BLE ping characteristic / UDP echo), reported as `link_rtt_ms` in the status events; half of it counts |
| hub -> Study Runner, including the hub's own handling | this plugin | the hub stamps `t` when the board's packet reaches it; the pings map `t` onto this computer's LSL clock |

- **The pings.** Once per second the plugin asks `/api/v2/ping`; the answer
  carries the hub's wall clock (`server_now`). Send and receive time on this
  computer give the round trip and one clock-offset estimate (the NTP method,
  `shared/clock_offset.py`): the fastest recent round trip is trusted, aged
  by a drift allowance; a jump of the hub clock (NTP on the hub) is accepted
  after three agreeing pings. Every answered ping is one `hub_clock` sample,
  so the XDF holds the evidence: the raw offset of each exchange
  (`exchange_offset_s`), the estimate in use (`clock_offset_s`, valid after
  four pings and within 25 ms), and its uncertainty.
- **`latency_ms`** per frame = `(arrival - t on this clock) * 1000 +
  link_rtt_ms / 2`. It is NaN while the hub clock is not yet known, during a
  suspected clock step, or when a board reports no radio round trip (older
  firmware). The ESP's own processing before it sends is not included.
  Half a radio or network RTT is only a symmetry-based estimate, not a proven
  one-way transport time. Neither this estimate nor the hub timestamp reveals
  the exact instant of radar or bio acquisition.
- **Correction (machine setting "Correct timestamps by the measured
  latency", off by default).** When on, the timestamp of a frame with a
  plausible latency (0-1000 ms) is `arrival - latency`; `correction_ms` holds
  what was taken off, so `arrival = timestamp + correction_ms / 1000` always
  rebuilds the arrival time. The setting is read when the plugin starts, so
  one run is never half corrected; restart the AM Hub after changing it.
  Timestamps never go backwards within a stream.

Neither SSE nor LSL/XDF is claimed to be loss-free; `seq` gaps, the hub's gap
events and reconnects are the evidence.

## Readiness and display

A required AM Hub is ready only while the hub stream is open **and** both
radar and bio frames arrived within `data_timeout_seconds` (default 5 s). A
frame saying "no person" is still a valid frame. The connection watchdog
replaces a stream that is silent for 2 s (reconnect at once, then after 0.5,
1 and 2 s).

`monitor.py` builds the dashboard view from the events that arrived: latest
values, per-board rate and age, the hub's board status, and person
detection. The graphs are the core's live view of the recorded frames
(manifest `live_view`: movement, vitals, position; mean per 0.5 s over the
last 60 s). The "Timing" row shows whether the hub clock is known, the
median latency per board, and whether timestamps are corrected. Everything
is cleared on every start and stop, so no value outlives its connection or
reaches the next session, and switching the plugin off hides all live
values.

## Files

| File | What it does |
| --- | --- |
| `manifest.json` | Streams, units, timestamp sources, backup projection, live view, settings. |
| `driver.py` | The API-v5 process entry point. |
| `plugin.py` | Lifecycle, auto-reconnect switch, card summaries. |
| `adapter.py` | Hub connection, one LSL sample per frame, `hub_events`, the pings and `hub_clock`, latency and correction, card summaries. |
| `monitor.py` | The dashboard view, derived only from received events. |
| `ui/dashboard.js` | Renders the monitor's view and the timing row. |

## Stimulus start/stop (actuators)

The AM Hub is also an actuator: its manifest declares trial `start` and
`stop`, so stimulus cards list it under "Control actuators". Only a card that
selects it sends, at the start and the stop of its stimulus,

    POST {base_url}/api/v2/stimulus/start
    POST {base_url}/api/v2/stimulus/stop
    {"event_id", "stimulus_id", "study_id", "session_id", "question_index", "source_epoch_ms"}

What the hub does with it (a scene, valves) is decided on the hub; the
endpoint still has to be added there (MRG-ParasiteV2,
`am_hub/amhub/plugins/logic/studyrunner/plugin.py`). Until then the call is
answered with 404: the plugin shows that as `last_stimulus_command`
(`unsupported`) and never holds up the stimulus. The sensing side does not
react to stimuli at all; it records continuously.

## Hardware acceptance still required

With connected hardware, record (1) an empty room, (2) known movement and
(3) a radar/bio interruption. Compare frame counts, `seq`, the hub's gap
reports, board ages and XDF timestamps with what the hub showed, and check
the `hub_clock` stream: a valid offset within a few milliseconds, round trips
of a few milliseconds on the LAN, plausible `latency_ms` per board. Until a
physical event has been timed end to end, this plugin makes no claim about
physical movement accuracy or exact timing.
