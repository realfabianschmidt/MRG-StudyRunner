# AM Hub Study Runner Integration

This guide explains how the `am_hub` plugin connects to the Parasite AM Hub
(`MRG-ParasiteV2/am_hub` - the hub that talks to the radar, bio and solenoid
boards and controls the autonomous material) and what it records.

## How It Connects

The AM Hub exposes an unauthenticated HTTP/SSE API. `adapter.py` opens
`GET {base_url}/api/v2/stream` and falls back to `/api/v1/stream`
automatically when the hub is older (HTTP 404 on v2).

Configure the hub's address under `am_hub.base_url` in
`software/study_content/settings/hardware_settings.json` (e.g.
`http://am-hub.local:8000`). No BLE scan, no device picker: one AM Hub, one
fixed URL. The hub itself decides per board whether it is reached over
Bluetooth or WiFi.

- **v2:** every board packet arrives as one `frame` event with all its values. The adapter writes them into its topic cache under one lock, so the 10 Hz tick never sees half a packet. The stream also carries the hub's per-board status (10 Hz) and valve/scene state.
- **Read timeout:** 10 s on v2, 30 s on v1. v2 sends 10 status events per second, so a silent connection is a dead one (e.g. the hub lost power). It is replaced instead of blocking forever.
- **Restart safety:** every `start()`/`stop()` bumps a generation counter. Threads of an earlier run exit before new ones start, so a restart can never push samples twice.

## Timing Stays In The Study Runner

The combined sample is published on this adapter's own fixed 10 Hz tick with
Study Runner timestamps, exactly as before. Hub timestamps are **never** used
for timing or freshness.

- **Freshness:** a topic counts as fresh from the moment it *arrives here* (`time.time()` in this process). A channel is only included in a tick while its topic arrived within the last `TOPIC_STALE_SECONDS` (2.5 s, matching `manifest.json`'s `capabilities.backup_projection.stale_after_ms`). Otherwise it publishes as missing (`None` -> `NaN`), never as the old value. This also means a Raspberry Pi clock that runs off (no RTC, no NTP in hotspot mode) cannot turn data into `NaN` or keep stale data "live".
- **v1 `init` history:** only the hub-side age of a value (hub time minus hub time) is used, to backdate its arrival.

## Streams

| Stream | Channels | Source |
| --- | --- | --- |
| `presence` | presence, presState, presDist, moveEnergy, staticEnergy, targetCount | radar board |
| `position` | personDist, personX, personY, t1..t3 x/y/speed | radar board |
| `vitals` | heartRate, breathRate, bioDistance, bioX, bioY | bio board |
| `radar_detail` | t1res, t2res, t3res, presDetDist | radar board |
| `valves` | ch0..ch7 (1 = open), sceneActive | solenoid board / hub scene engine |
| `hub_status` | per board (radar, bio, solenoid): Connected, Transport (1 = BLE, 2 = WiFi), Rssi, RateHz, Lost, LinkRttMs, LatencyMs; plus hubRttMs, hubDroppedEvents | hub status + own ping |

All streams are 10 Hz and follow the same NaN rule. `presence`, `position`
and `vitals` are unchanged (frozen contracts). `radar_detail`, `valves` and
`hub_status` are added by this version.

`hub_status` channel meanings:
- **Lost:** packets the hub estimates were lost on the air, from the board's packet timing.
- **hubDroppedEvents:** events the hub had to drop for this client. The hub reports every drop with the exact count, and it should stay 0.

The backup projection's distance output is named `distance_mm`: `personDist` is in millimetres.

## Latency Per Board

`<board>LatencyMs` = radio round trip / 2 + hub-internal delay + this
adapter's round trip to the hub / 2. Each part is measured without comparing
two clocks:

| Part | Measured by |
| --- | --- |
| board ⇄ hub (`LinkRttMs`) | the hub: BLE ping characteristic or UDP echo on port 8890 (needs the current `_ble` firmware) |
| inside the hub | the hub: packet arrival → sent, on its own clock |
| hub ⇄ Study Runner (`hubRttMs`) | this adapter: `GET /api/v2/ping` once per second on its own monotonic clock (separate thread, independent of the tick) |

These are measurements only - they are recorded, never applied to any
timestamp.

## Status

Besides the usual fields, `get_status()` reports:
- `api_version`,
- `hub_boards` (per board link, transport, rate, lost packets, latency parts),
- `hub_rtt_ms`,
- `data_quality` (`frames` per board, `seq_gaps` per board, `hub_dropped_events`).

The hub numbers frames per board (`seq`), so every packet that goes missing
between hub and Study Runner shows up in `seq_gaps`.

## Architecture (API v5)

Like every Study Runner plugin, the core process never imports this folder's
Python modules directly - it only starts `driver.py` as a subprocess (see
`docs/file-guide.md`). Inside that subprocess:

- `driver.py` - the only executable entry point (`run_plugin_driver("am_hub")`).
- `plugin.py` - status/lifecycle registration.
- `adapter.py` - the SSE client, topic cache, combined-sample publisher, hub ping, LSL mirror and result sidecar export.
- `ui/dashboard.js` - the admin dashboard tile: a status row and two live trend graphs (movement energy, position).

## Where The Code Comes From

The AM Hub lives in `MRG-ParasiteV2/am_hub`. Its StudyRunner API (`/api/v1`,
`/api/v2`) is documented in `am_hub/amhub/plugins/logic/studyrunner/README.md`
there; this plugin is simply an HTTP/SSE client of it, with no shared code
between the two repositories.
