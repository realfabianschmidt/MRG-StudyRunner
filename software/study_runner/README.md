# Study Runner — the application

Each folder here is one area and carries its own README. Start with the one that
matches what you are looking for.

| Folder | What lives there |
|---|---|
| [`backend/`](backend/) | The Flask server. `routes/` decides what a URL means; `services/` does the work behind it, grouped by what part of a study it serves. |
| [`frontend/`](frontend/) | Everything the browser loads: the two pages, their ES modules, styles, locales and fonts. No build step. |
| [`recording/`](recording/) | The host side of recording: session folders, starting and supervising the worker, reading the XDF back. |
| `recording_worker/` | The separate Python process that actually writes XDF. Its native half is `software/recording_worker/native/`. |
| [`plugins/`](plugins/) | One folder per plugin, all equal. A folder with a `manifest.json` is discovered automatically. |
| [`plugin_framework/`](plugin_framework/) | The machinery that finds, validates and talks to those plugins. Nothing here is a plugin. |
| `updates/` | Verifying a signed release and applying it. |

Two loose files: `app_server.py` is the Flask app module used by browser and
packaged mode, and also prepares the per-computer HTTPS certificate the tablet
camera needs; `version.py` is the single source of the version number.

## Running it

```bash
cd software
python server.py
```

## What is not in here

The studies an operator actually edits, and this machine's settings, live in
`software/study_content/`. Results go to `software/saved_results/`. Plugin
runtime logs, DeepFace caches and generated certificates are local state and
stay out of Git.

## The one rule that keeps this extensible

A plugin declares itself in its manifest and the application reads it. No core
module may name a plugin — not a route, not a service, not a frontend script.
While that holds, adding a sensor or an upload destination means adding a
folder. The moment it stops holding, every new plugin becomes a patch spread
across the codebase. Several tests exist only to keep it true.
