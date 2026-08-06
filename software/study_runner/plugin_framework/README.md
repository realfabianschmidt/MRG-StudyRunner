# Plugin framework — the machinery, not the plugins

Nothing in here is a plugin. The plugins are one folder over in `../plugins/`;
this is what turns a folder with a `manifest.json` into something the rest of
the application can use.

| Module | What it does |
|---|---|
| `plugin_api.py` | What a plugin implements: `PluginContext` (the runtime data it is handed) and `Plugin` (the identity and handlers it declares). |
| `plugin_catalog.py` | Discovery and manifest validation. A broken folder becomes a visible invalid entry, never a failed start-up. |
| `registry.py` | The façade the backend calls: look a plugin up, ask it for status, dispatch an action, resolve a declared UI asset. |
| `adapter_utils.py` | Small state-free helpers shared by adapters. |
| `dependency_utils.py` | Optional-import probing, so a missing SDK degrades to an unavailable plugin instead of a crash. |
| `history_buffer.py` | Bounded sample history for live views. |

## The contract

A plugin is discovered because it is a directory under `../plugins/` containing
a valid `manifest.json`. There is no registration list, no ordering, and no
upload path — `DEFAULT_PLUGINS_DIRECTORY` is resolved from this file, never from
a request.

The manifest is the single source of truth for what a plugin is called, what it
records, which settings it exposes, and where in the interface it appears. The
catalog normalises it once; everything downstream reads the normalised shape.

## Two things worth knowing before changing this

- **`DEFAULT_PLUGINS_DIRECTORY` is the only place that knows where plugins
  live.** Discovery and the UI asset resolver both used to walk from their own
  `__file__` independently, and both broke when this package moved.
- **Manifest validation is a security boundary, not a convenience.** A plugin is
  imported only after its manifest passes; an asset is served only if the
  manifest declares it and it resolves inside the plugin's own directory.
