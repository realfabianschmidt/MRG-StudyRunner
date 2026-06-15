# Plan For Clearer Code

## Current Structure

The app now uses clear top-level folders:

- `server_app/` for backend app code.
- `server_app/services/` for small backend services.
- `plugins/` for built-in integrations.
- `web_interface/` for browser files.
- `settings/` for editable local settings.
- `saved_studies/` for presets.
- `saved_results/` for participant output.

## Plugin Model

The plugin registry stays small and explicit.

- `plugins/plugin_api.py` defines the shared plugin contract.
- `plugins/registry.py` imports each built-in plugin directly.
- Each plugin folder exports `PLUGIN` from `plugin.py`.
- Adapters stay inside their plugin folder.

This project does not use dynamic package discovery or installable third-party plugins.

## Useful Future Work

- Add automated tests for plugin status normalization.
- Add focused tests for result saving and sidecar export.
- Add stricter answer validation for each card type.
- Keep docs updated whenever a folder or file responsibility changes.
