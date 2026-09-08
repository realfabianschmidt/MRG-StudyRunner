# Plugin framework — the machinery, not the plugins

Nothing in here is a plugin. Built-ins live below `../extensions/`;
this is what turns a folder with a `manifest.json` into something the rest of
the application can use.

| Module | What it does |
|---|---|
| `../contracts/plugin_api.py` | What a plugin implements: `PluginContext` (the runtime data it is handed) and `Plugin` (the identity and handlers it declares). |
| `plugin_catalog.py` | Discovery and manifest validation. A broken folder becomes a visible invalid entry, never a failed start-up. |
| `registry.py` | The façade the server calls: look a plugin up, ask it for status, dispatch an action, resolve a declared UI asset. |
| `adapter_utils.py` | Small state-free helpers shared by adapters. |
| `dependency_utils.py` | Optional-import probing, so a missing SDK degrades to an unavailable plugin instead of a crash. |
| `history_buffer.py` | Bounded sample history for live views. |

## The contract

A plugin is discovered because it is a directory under a trusted extension
category containing a valid `manifest.json`. There is no registration list or
request-controlled discovery path.

The manifest is the single source of truth for what a plugin is called, what it
records, which settings it exposes, and where in the interface it appears. The
catalog normalises it once; everything downstream reads the normalised shape.

## Two things worth knowing before changing this

- **`extension_layout.trusted_roots()` is the only place that knows where
  extensions live.** Discovery, UI assets, self-check, and drivers share it.
- **Manifest validation is a security boundary, not a convenience.** A plugin is
  imported only after its manifest passes; an asset is served only if the
  manifest declares it and it resolves inside the plugin's own directory.
