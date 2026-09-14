# Backend — the server

This is Study Runner's Flask backend: the program that both the participant
tablet and the operator's admin dashboard talk to over the network. Everything
here is Python; the browser pages themselves live in `apps/ui/`.

Two things at the top level, then one folder:

- **`__init__.py`** — the *app factory*. This runs exactly once, when Study
  Runner starts: it reads the settings files, creates every long-lived piece
  (sensor coordinator, session store, recording runtime, upload queue, ...),
  turns on the installed plugins, registers every route file below, and
  installs a rule that stops a tablet from ever showing a stale, cached
  version of the interface.
  - **In:** settings files and environment variables on disk.
  - **Out:** a fully wired, ready-to-run Flask application.
- **`application.py`** — the part that actually *runs* the server (started
  from `software/server.py`). It decides HTTP vs. HTTPS, prepares or finds the
  local certificate, refuses to start if the port is already taken by another
  program, opens the admin page in a browser automatically for packaged
  installs, and keeps the terminal log quiet (only failed requests print, not
  every single one).
  - **In:** the app object from `__init__.py`, plus environment variables
    (host, port, HTTPS on/off).
  - **Out:** nothing — it keeps the process alive until Study Runner is closed.
- **`routes/`** — one file per area of the HTTP surface, described below.
  A route's job is small on purpose: read the incoming request, ask one
  service elsewhere in the codebase to do the actual work, and turn the
  answer into JSON. The real logic — reading/writing files, talking to
  plugins, checking a study's readiness — lives in `runtime_core/`,
  `data_core/`, and `plugin_framework/`, not here.


## `routes/`, file by file

| File | What it does | Who it's for |
|---|---|---|
| `helpers.py` | Shared toolbox for the other route files: reading the live hardware/sensor config, starting and stopping sensor plugins for a study session, and tracking which sensors are temporarily on/off. Not a route file itself — no URLs live here. | internal only |
| `pages.py` | Serves the two HTML pages: `/` (participant/tablet) and `/admin` (operator dashboard). No logic, just hands over files. | tablet + operator |
| `study.py` | Everything the participant tablet does during a study: load/save the active study, start/stop/resume a session, start/stop a trial (a stimulus), send timing markers, heartbeat, and clock sync. This is the tablet's entire vocabulary. | tablet |
| `results.py` | Saving a participant's finished answers. The most protected file in the server: if saving fails for *any* reason, a raw backup copy is written to disk first, so an answer is never silently lost. Also stores incremental "partial" snapshots after every question in case the tablet dies mid-study. | tablet |
| `admin.py` | Operator actions: health check, restart the server, list/load/delete saved studies, start/stop a study run, manage a study's own credentials (API keys), check whether the study is actually ready to run. | operator |
| `sensors.py` | Hardware settings and generic on/off/settings control for any plugin (sensor, camera, ...), plus deprecated fixed sensor/camera URLs that forward to plugin handlers or return 410 when the plugin is absent. | operator + tablet |
| `update.py` | The four "Check for update / download / install / status" actions behind the admin dashboard's Update panel. | operator |
| `notion.py` | Old Notion-only URLs, kept only for clients built before the generic plugin system existed. Nothing current uses these. | (legacy only) |
| `plugins.py` | The one doorway every plugin (sensor, question card, upload destination) uses: its catalog listing, admin actions, participant actions, receiving participant data, and a live debug console for developers. | operator + tablet + plugins |
| `sessions.py` | Browsing already-finished sessions: the list, one session's detail view, its sensor signal graphs, and withdrawing (permanently deleting) a session's data. | operator |
| `certificate.py` | HTTPS certificate status, and moving the local "root of trust" certificate to another computer so a new laptop can be trusted by the same tablet. | operator |
| `branding.py` | Uploading and serving the small logo images shown on the participant waiting screen. | operator + tablet |
| `uploads.py` | Status and manual retry for background uploads (e.g. to Notion), and "open this session's folder" for the operator. | operator |
| `recovery.py` | Rescuing a session that was interrupted by a crash: list what can be recovered, finish saving it properly, or discard it. | operator |
| `finalization.py` | Status and control for the durable "finish saving a session" background job: progress, retry a failed step, confirm a partially-failed save, open its folder. | operator |

## Where the boundaries actually are

- A route may not do file I/O, talk to a plugin, or hold state directly — that
  belongs in a service under `runtime_core/`, `data_core/`, or
  `plugin_framework/`.
- A service may not import Flask, read `current_app`, or raise HTTP errors. It
  raises its own exception type and the route decides on a status code.
- Neither a route nor a service may name a specific plugin. Plugin behaviour
  comes from `plugin_framework/registry.py`, driven entirely by manifests.
- Anything long-lived (sensor coordinator, session store, recording runtime)
  is built once in `__init__.py` and read from `app.config`, never built again
  per request.

## The HTTP surface is checked, not just trusted

Adding, renaming, or removing a route here also means touching
`software/tests/test_route_inventory.py` — see
[`software/tests/README.md`](../../../tests/README.md) for why that test
exists and what it actually checks.
