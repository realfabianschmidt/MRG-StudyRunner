# Plugin framework — the machinery, not the plugins

Nothing in here is a plugin. The plugins themselves (sensors, question cards,
upload destinations, outputs) live under `../plugins/`, organized by
category. This package is what turns one of those folders — a `manifest.json`
plus a `plugin.py` — into something the rest of the application can actually
call: find it, validate it, run it, ask it for status.

`plugins/` is the on-disk bundle layout. `Plugin` is the runtime object
defined in `contracts/plugin_api.py`; this framework builds it from a validated
manifest and uses it to dispatch calls to the plugin runtime.

## Files

| File | What it does |
|---|---|
| `plugin_catalog.py` | Discovery and manifest validation. Scans the trusted plugin folders, validates each `manifest.json` (delegating the actual rule-checking to `contracts/manifest.py`), and builds the `Plugin` objects the rest of the app uses. A broken folder becomes a visible "invalid" catalog entry, never a crashed startup. |
| `registry.py` | The façade almost everything else calls: look a plugin up, get its status, dispatch a runtime/admin/participant action, resolve a UI asset, run the trial-start/stop/marker callbacks across every plugin at once. If a route or service needs a plugin to do something, it goes through here. |
| `process_host.py` | Supervises a plugin's own `driver.py` subprocess: starts it, talks to it over a line-oriented stdio protocol, restarts it (up to a limit) if it dies, and exposes its live console to the admin diagnostics view — with a read-only-during-study gate that needs an explicit, logged operator unlock. |
| `driver_runtime.py` | The other end of that same subprocess: what actually runs *inside* a plugin's `driver.py` once `process_host.py` has started it, reading its own manifest and serving commands until told to stop. |
| `plugin_layout.py` | The one place that knows where plugins physically live (`sensors/`, `cards/`, `destinations/`, `outputs/`) and how to find one by its `plugin_key`. Discovery, UI-asset serving, and driver startup all share this instead of each hardcoding the path. |
| `plugin_secrets.py` | Where a plugin's per-study credentials (a Notion API key, a Nextcloud password) actually live — kept out of the exported study file on purpose, so sharing a study never leaks the key that goes with it. Callable directly from a plugin's own subprocess, not just the host. |
| `card_catalog.py` | Turns the plugin catalog's `card_contract` declarations into the live set of valid question types, cached until the catalog itself changes. |
| `adapter_utils.py` | A couple of tiny, state-free helpers shared by plugin adapters (a formatted timestamp, updating a lock-protected status dict). |
| `history_buffer.py` | Sizes and queries the bounded in-memory sample history every sensor adapter keeps, so a long session can't grow memory without bound. |
| `__init__.py` | States what this package is (and isn't) in one paragraph. |

## The contract

A plugin is discovered because it is a directory under a trusted plugin
category containing a valid `manifest.json` — there is no separate
registration list, and nothing about which plugins load can be influenced by
a web request.

The manifest is the single source of truth for what a plugin is called, what
it records, which settings it exposes, and where in the interface it
appears. `plugin_catalog.py` normalizes it once; everything downstream reads
that one normalized shape.

## Discovery and asset boundaries

- **`plugin_layout.trusted_roots()` is the only place that knows where
  plugins live.** Discovery, UI-asset serving, the self-check, and driver
  startup all share it.
- **Manifest validation is a security boundary, not a convenience.** A
  plugin's code is imported only after its manifest passes validation, and a
  UI asset is only ever served if the manifest declares it and it resolves
  inside that plugin's own directory.
