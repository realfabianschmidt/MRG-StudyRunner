# OSC / TouchDesigner plugin

Forwards trial start/stop markers to TouchDesigner (or any other OSC
listener) over UDP, so a visual/generative patch can react live to a
study's stimulus timing. It carries no sensor data of its own
(`sample_delivery: none`) — it only relays the two `trial_events` (`start`,
`stop`) declared in its manifest.

## Architecture

Study Runner plugins are API v4: the core process supervises this plugin as
a subprocess and never imports its Python modules directly (see
`docs/file-guide.md`). This folder follows that same shape:

- `driver.py` — the only executable entry point. A one-line wrapper that
  calls `run_plugin_driver("osc")`; the core never imports anything else in
  this folder.
- `plugin.py` — the plugin logic running inside the supervised subprocess:
  wires trial start/stop into the adapter and reports status.
- `adapter.py` — the actual OSC client: opens one UDP client for the
  configured host/port and sends `/start` and `/stop` messages.
- `manifest.json` — declares the `marker_forwarding` capability, the
  machine settings (`host`, `port`, default `127.0.0.1:8000`), and the card
  action `forward_marker`.

## Where the code comes from

`adapter.py` is a thin wrapper around the **official `python-osc` package**
(`python-osc>=1.8`, pinned in `software/requirements.txt`) —
`SimpleUDPClient(host, port)` and `.send_message(address, value)` from
`pythonosc.udp_client`, used exactly as documented by that library. There is
no vendor example code copied in; the module docstring's explanation of OSC
in general is original text, not sourced from a third-party document.

## Settings

- `host` / `port` (machine settings) — where the OSC listener (TouchDesigner
  or otherwise) is running. Defaults to `127.0.0.1:8000`.

There is no credential and no admin action for this plugin — sending a UDP
packet to a local listener needs neither.
