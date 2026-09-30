# AM Hub

The `am_hub` plugin records the Parasite AM Hub (`MRG-ParasiteV2/am_hub`): the
radar board (LD2450 + LD2410B), the bio board (MR60) and the valve board, as
the hub forwards them over `GET {base_url}/api/v2/stream`. It is built like the
BrainBit plugin: one numeric stream per measuring device, one text stream for
everything else, a derived 1 Hz backup, and a separate monitor for the
dashboard. The hub, the ESPs and their firmware are not changed. A hub without
API v2 is reported as unsupported.

The hub URL is a machine setting; whether a study uses the AM Hub is a study
setting.

## What is recorded

| Stream | One sample per | Channels |
| --- | --- | --- |
| `radar` | radar board frame (~10 Hz) | the 22 radar values in the hub's order (`personDist` ... `presDetDist`), `rssi`, `seq`, `hub_timestamp` |
| `bio` | bio board frame (~10 Hz) | `heartBpm`, `breathRate`, `bioDist`, `bioT1x`, `bioT1y`, `rssi`, `seq`, `hub_timestamp` |
| `valves` | valve board frame (on change + heartbeat) | `CH0` ... `CH7`, `rssi`, `seq`, `hub_timestamp` |
| `hub_events` | any other hub event | `event`: the event's JSON exactly as received |

- **One frame, one sample.** A sample exists only because the board sent a
  frame. There is no fixed tick, nothing is resampled or carried forward, and
  a value a frame does not contain is NaN. The channel names are the hub's
  topic names without their path (`/sensor/heartBpm` -> `heartBpm`).
- **Values as the ESP sends them.** No unit conversion and no cleanup: the
  declared units are the ESP's (positions mm, `bioDist` cm, target speed
  cm/s, energies arbitrary units). The ESP firmware repeats its latest values
  and sets them to 0 after its own timeout, so a 0 or a repeated value is not
  necessarily a new, independent measurement.
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
- **Card statistics** average the recorded frames of each card, zeros
  included.

## Timing

The XDF timestamp of every sample is the local LSL time when the event
arrived at this computer. The hub's `t` is kept in `hub_timestamp` but belongs
to the hub's clock and is never used for timing. Network, buffering and clock
offsets separate the two. The `/api/v2/ping` round trip and the per-board
latency in the dashboard are diagnostics only. Neither SSE nor LSL/XDF is
claimed to be loss-free; `seq` gaps, the hub's gap events and reconnects are
the evidence.

## Readiness and display

A required AM Hub is ready only while the hub stream is open **and** both
radar and bio frames arrived within `data_timeout_seconds` (default 5 s). A
frame saying "no person" is still a valid frame. The connection watchdog
replaces a stream that is silent for 2 s (reconnect at once, then after 0.5,
1 and 2 s).

`monitor.py` builds the dashboard view from the events that arrived: latest
values, per-board rate and age, the hub's board status and latency, person
detection, and one minute of graph points. It is cleared on every start and
stop, so no value outlives its connection or reaches the next session, and
switching the plugin off hides all live values.

## Files

| File | What it does |
| --- | --- |
| `manifest.json` | Streams, units, backup projection, settings. |
| `driver.py` | The API-v5 process entry point. |
| `plugin.py` | Lifecycle, auto-reconnect switch, card summaries. |
| `adapter.py` | Hub connection, one LSL sample per frame, `hub_events`, card summaries. |
| `monitor.py` | The dashboard view, derived only from received events. |
| `ui/dashboard.js` | Renders the monitor's view. |

## Hardware acceptance still required

With connected hardware, record (1) an empty room, (2) known movement and
(3) a radar/bio interruption. Compare frame counts, `seq`, the hub's gap
reports, board ages and XDF timestamps with what the hub showed. Until then
this plugin makes no claim about physical movement accuracy or exact timing.
