# AM Hub Study Runner Integration

This guide explains how the `am_hub` plugin connects to the Parasite AM Hub
(a Raspberry Pi service that also controls the autonomous material) and what
it currently does and does not receive from it.

## How It Connects

The AM Hub exposes an unauthenticated HTTP/SSE "v1" API (`GET
{base_url}/api/v1/stream`), pushing every sensor topic it knows about as it
changes. `adapter.py` opens that stream, caches the latest value per topic,
and republishes one combined sample per declared LSL stream on a fixed
10 Hz tick (the underlying board's own rate) -- the hub sends per-topic
events, not one combined row, so this plugin assembles them itself.

Configure the hub's address under `am_hub.base_url` in
`software/study_content/settings/hardware_settings.json` (e.g.
`http://am-hub.local:8000`). No BLE scan, no device picker: one AM Hub, one
fixed URL.

## Staleness, Not Silent Carry-Forward

The hub pushes per-topic events, not one combined row, so a publisher inside
this adapter assembles a combined sample on a fixed 10 Hz tick regardless of
whether the hub sent anything new in that window. A channel is only included
in that sample while its underlying topic actually updated within the last
`TOPIC_STALE_SECONDS` (2.5 s, matching `manifest.json`'s
`capabilities.backup_projection.stale_after_ms`); otherwise it publishes as
missing (`None` -> `NaN` on the LSL outlet), never the old cached value. The
same real per-topic timestamp -- not the fixed publish tick -- drives
`get_status()`'s `"stale"` escalation, so a stalled hub connection is visible
even while this adapter keeps publishing on schedule.

## What It Currently Carries

The hub today only forwards **presence, position and movement** from its
LD2450/LD2410B board (`/sensor/presence`, `/sensor/personX/Y`,
`/sensor/presMoveEnergy`, ... -- see `CHANNEL_TOPICS` in `adapter.py` for the
full topic map). Position (`personX`/`personY`/`t1..t3`) is recorded raw and
unfiltered in the native XDF; `moveEnergy` (the board's own movement-energy
reading, 0-100) is used as the movement-intensity value elsewhere, so this
plugin does not need its own movement heuristic.

**Heart rate and breathing are not forwarded by the hub yet.** They come from
a separate board ("bio_hub") that, as of this writing, talks directly to the
existing `mr60_mini_radar` plugin over its own BLE link -- entirely bypassing
the AM Hub. The `vitals` stream's channels
(`heartRate`, `breathRate`, `bioDistance`, `bioX`, `bioY`) exist in
`manifest.json` from day one, mapped to the exact legacy topic names the
bio_hub firmware already uses elsewhere (`/sensor/heartBpm`,
`/sensor/breathRate`, `/sensor/bioDist`, `/sensor/bioT1x`, `/sensor/bioT1y`),
so once the hub forwards them, this plugin picks them up with no code
change. Until then, those channels simply stay `NaN` (see `_push_lsl_values`
-- a channel that never reported reads as `NaN`, never a misleading `0.0`).

## Architecture (API v5)

Like every Study Runner plugin, the core process never imports this folder's
Python modules directly -- it only ever starts `driver.py` as a subprocess
(see `docs/file-guide.md`). Inside that subprocess:

- `driver.py` -- the only executable entry point (`run_plugin_driver("am_hub")`).
- `plugin.py` -- status/lifecycle registration.
- `adapter.py` -- the SSE client, topic cache, combined-sample publisher,
  LSL mirror, and result sidecar export.
- `ui/dashboard.js` -- the admin dashboard tile: a status row (toggle
  replaces Start/Stop, an icon button replaces Restart) and two live trend
  graphs (movement energy, position), ported from the BrainBit dashboard's
  auto-scaling trend-graph pattern and generalized from a fixed 0-100% ratio
  to real units.

## Where The Code Comes From

The AM Hub itself lives in a separate repository
(`MRG-ParasiteV2/am_hub`). Its `/api/v1` contract is documented in that
project's `amhub/plugins/logic/parasite/routes_v1.py`; this plugin is simply
an HTTP/SSE client of it, with no shared code between the two repositories.
