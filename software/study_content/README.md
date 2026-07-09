# Study Content

This folder contains editable default content.

- `settings/study_config.json`: active default study.
- `settings/hardware_settings.json`: default integration settings.
- `studies/`: reusable `.study-runner` study presets.

Study settings store the intended sensor defaults for a study. During a lab
session, the Admin dashboard can apply temporary sensor overrides without
changing these files. Use the dashboard reset action to return to saved study
settings.

In packaged mode with an external data folder, these files are copied into the app-data folder only on first start. Later updates do not overwrite local user studies, local settings, saved results, or local secrets.
