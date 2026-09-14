# Study Runner — the application

Each folder here is one area and carries its own README. Start with the one that
matches what you are looking for.

| Folder | What lives there |
|---|---|
| [`apps/server/`](apps/server/) | Flask app factory, HTTP routes, and server runtime. |
| [`apps/ui/`](apps/ui/) | Browser pages, ES modules, styles, locales, and fonts. No build step. |
| [`contracts/`](contracts/) | Pure data/validation shapes shared across areas — the plugin manifest contract, session lifecycle, quality journal. No Flask, no filesystem I/O. |
| [`data_core/`](data_core/) | Recording host, detached worker, and their pure wire contracts. |
| [`runtime_core/`](runtime_core/) | Study, settings, finalization, and delivery orchestration. |
| [`plugins/`](plugins/) | Built-in sensors, cards, destinations, and outputs, discovered from manifests. |
| [`plugin_framework/`](plugin_framework/) | The machinery that finds, validates and talks to those plugins. Nothing here is a plugin. |
| [`shared/`](shared/) | Dependency-light utilities used across package boundaries. |
| [`updates/`](updates/) | Verifying a signed release and applying it. |

Three files sit directly in this package:

- `app_server.py` provides the stable `study_runner.app_server` import path used
  by `software/server.py`, the desktop launcher, and server restarts. It delegates
  to `apps/server/application.py`.
- `self_check.py` implements the packaging smoke test (`server.py --self-check`).
  It boots the app with disposable storage without starting the HTTP server or
  using real hardware.
- `version.py` is the single source of the version number.

The import-graph checks in `software/tests/support/import_graph.py` assign an
area to modules below `study_runner/<area>/`. Files directly in this package
sit outside those area boundaries, allowing the self-check to exercise the
whole application.

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
