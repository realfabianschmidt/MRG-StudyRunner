# AM Hub Study Runner Integration

This guide explains how the `am_hub` plugin connects to the Parasite AM Hub
(`MRG-ParasiteV2/am_hub` - the hub that talks to the radar, bio and solenoid
boards and controls the autonomous material) and what it records.

## How It Connects

The AM Hub exposes an unauthenticated HTTP/SSE API. `adapter.py` opens
`GET {base_url}/api/v2/stream` and falls back to `/api/v1/stream`
automatically when the hub is older (HTTP 404 on v2).

Configure the hub's address in the Settings Hub (AM Hub → AM Hub URL) or
under `am_hub.base_url` in
`software/study_content/settings/hardware_settings.json` (e.g.
`http://am-hub.local:8000`), and enable the plugin. No BLE scan, no device picker: one AM Hub, one
fixed URL. The hub itself decides per board whether it is reached over
Bluetooth or WiFi.

- **v2:** every board packet arrives as one `frame` event with all its values. The adapter writes them into its topic cache under one lock, so the 10 Hz tick never sees half a packet. The stream also carries the hub's per-board status (10 Hz) and valve/scene state.
- **Instant delivery:** the stream is read chunk by chunk (`iter_content(None)`) with an own SSE parser, so every event is handled the moment its bytes arrive (the earlier `iter_lines` waited for 512-byte blocks).
- **Dead connection in 2 s:** v2 sends 10 status events per second, so 2 s without a byte is a dead connection (WiFi dropout, hub restart). The socket read timeout (2 s on v2, 35 s on v1) ends it on every platform; closing a socket from another thread does not interrupt a blocked read on Windows. TCP keepalive and `TCP_NODELAY` are set on the socket.
- **Back at once:** the next attempt follows immediately, then after 0.5 / 1 / 2 s at most. The stream URL carries `?client=study-runner-<computer>`, so the hub replaces this computer's old, possibly half-open stream instead of keeping it.
- **Honest status:** only the stream reader decides whether the hub is connected (link state and the time of the last event of any kind). The 10 Hz publisher never claims a connection: before the first event the status is `connecting`, after 2 s of silence `stale` (reconnecting).
- **Diagnostics:** `link` in the status (events/s, connected for, reconnects, last loss with reason), `boards` (frames/s and age per board), and the hub's `hub_host.wifi_power_save` from its `hello` event -- the tile warns while it is `on`.
- **Restart safety:** every `start()`/`stop()` bumps a generation counter. Threads of an earlier run exit before new ones start, so a restart can never push samples twice.

## Person Detected

A person counts as detected when **any** fresh source (< 2.5 s) sees one: the
LD2410B presence flag (`/sensor/presence`), a tracked LD2450 target
(`/sensor/targetCount` ≥ 1), or MR60 distance / heart / breathing rate. The
tile names the sources. Before, only the presence flag counted, so the plugin
said "no person" while the hub showed position and vital signs.

## Everything The Hub Sends

- **`hub_events` stream:** every hub event, verbatim, one JSON string per event (`frame` with all values, `seq`, `sender`, hub time; `status`, `valves`, `scene`, `gap`, `hello`), plus the adapter's own `{"type":"link","source":"study_runner","state":"connected"|"lost",...}` events. Nothing is filtered, converted or dropped, and topics this adapter does not know yet are included. Irregular rate; roughly 10 KB/s (about 36 MB per hour), mostly the 10 Hz status.
- **All topics:** every topic address the hub sends lands in the topic cache and in the status (`topics`: raw value and age), unknown ones listed in `unknown_topics` (today the WiFi-only `/sensor/rssiRadar`, `/sensor/rssiBio`, `/solenoid/rssi`, `/solenoid/alive`). The tile lists them all under "All values from the hub".
- The 10 Hz streams below stay the interpreted view (mm, NaN for "no value"); `hub_events` is the raw one.

## Timing Stays In The Study Runner

The combined sample is published on this adapter's own fixed 10 Hz tick with
Study Runner timestamps, exactly as before. Hub timestamps are **never** used
for timing or freshness.

- **Freshness:** a topic counts as fresh from the moment it *arrives here* (`time.time()` in this process). A channel is only included in a tick while its topic arrived within the last `TOPIC_STALE_SECONDS` (2.5 s, matching `manifest.json`'s `capabilities.backup_projection.stale_after_ms`). Otherwise it publishes as missing (`None` -> `NaN`), never as the old value. This also means a Raspberry Pi clock that runs off (no RTC, no NTP in hotspot mode) cannot turn data into `NaN` or keep stale data "live".
- **v1 `init` history:** only the hub-side age of a value (hub time minus hub time) is used, to backdate its arrival.
- **v1 fallback per attempt:** after a v1 connection ends, the next reconnect tries v2 again, so a hub that gains v2 is picked up without a restart.

## Units And Missing Values

`_hub_values_to_contract()` in `adapter.py` is the one place where hub values
become contract values, on every tick:

- **Units:** the bio board sends `bioDistance` in cm and the LD2450 sends target speed in cm/s. Both are multiplied by 10, so the streams stay in mm and mm/s (`CHANNEL_SCALE`). All other distances and positions already arrive in mm.
- **0 means "no value":** the firmware sends 0 when a sensor has been silent for 2 s or sees no target. So `heartRate`, `breathRate` and `bioDistance` of exactly 0 are recorded as missing (`NaN`). So is a target slot at x = y = 0 (with its speed and resolution), `bioX`/`bioY` at 0/0, and `personDist`/`personX`/`personY` while `targetCount` is 0.

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
- **Lost:** packets the hub estimates were lost on the air, from the board's packet timing. The hub counts from its own start; the adapter reports only the packets lost since this session's `start()`.
- **Rssi:** in dBm, but not the same measurement on both transports. On BLE it is the signal at connect time and is not updated while connected. On WiFi it is the board's signal to its router, not to the hub.
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
- `hub_rtt_ms` (empty while pings fail, so no old value stands in),
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
- `ui/dashboard.js` - the admin dashboard tile: a status row, headline tiles for movement, breathing rate and heart rate, and three live trend graphs (movement energy, heart/breathing rate, position), and per-board link, latency and losses under "Acquisition details". All previews are cleared on every start, so no session sees the previous one.

## Where The Code Comes From

The AM Hub lives in `MRG-ParasiteV2/am_hub`. Its StudyRunner API (`/api/v1`,
`/api/v2`) is documented in `am_hub/amhub/plugins/logic/studyrunner/README.md`
there; this plugin is simply an HTTP/SSE client of it, with no shared code
between the two repositories.
