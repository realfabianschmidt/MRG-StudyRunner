# Study Runner — the application

Each folder here is one area and carries its own README. Start with the one that
matches what you are looking for.

| Folder | What lives there |
|---|---|
| [`apps/server/`](apps/server/) | Flask app factory, HTTP routes, and server runtime. |
| [`apps/ui/`](apps/ui/) | Browser pages, ES modules, styles, locales, and fonts. No build step. |
| [`data_core/`](data_core/) | Recording host, detached worker, and their pure wire contracts. |
| [`runtime_core/`](runtime_core/) | Study, settings, finalization, and delivery orchestration. |
| [`extensions/`](extensions/) | Built-in sensors, destinations, outputs, and future cards, discovered from manifests. |
| [`plugin_framework/`](plugin_framework/) | The machinery that finds, validates and talks to those plugins. Nothing here is a plugin. |
| [`shared/`](shared/) | Dependency-light utilities used across package boundaries. |
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
