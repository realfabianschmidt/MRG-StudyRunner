# Plan For Clearer Code

## Current Structure

The app now uses clear top-level folders:

- `study_runner/backend/` for backend app code.
- `study_runner/backend/services/` for small backend services.
- `study_runner/integrations/` for built-in integrations.
- `study_runner/web/` for browser files.
- `study_content/settings/` for editable local settings.
- `study_content/studies/` for presets.
- `saved_results/` for participant output.

## Plugin Model

The plugin registry stays small and explicit.

- `study_runner/integrations/plugin_api.py` defines the shared plugin contract.
- `study_runner/integrations/registry.py` imports each built-in plugin directly.
- Each plugin folder exports `PLUGIN` from `plugin.py`.
- Adapters stay inside their plugin folder.

This project does not use dynamic package discovery or installable third-party plugins.

## Useful Future Work

- Add automated tests for plugin status normalization.
- Add focused tests for result saving and sidecar export.
- Add stricter answer validation for each card type.
- Keep docs updated whenever a folder or file responsibility changes.
