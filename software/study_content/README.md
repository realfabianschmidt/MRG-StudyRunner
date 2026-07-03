# Study Content

This folder contains editable default content.

- `settings/study_config.json`: active default study.
- `settings/hardware_settings.json`: default integration settings.
- `studies/`: reusable `.study-runner` study presets.

In packaged mode with an external data folder, these files are copied into the app-data folder only on first start. Later updates do not overwrite local user studies, local settings, saved results, or local secrets.
