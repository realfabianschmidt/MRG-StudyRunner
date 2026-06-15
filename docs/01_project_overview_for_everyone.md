# Project Overview For Everyone

Study Runner is a local study tool with one admin page, one participant page, and optional built-in plugins for lab integrations.

## Main Parts

- `server.py`: starts the local server.
- `server_app/`: backend app and backend services.
- `plugins/`: built-in integrations such as BrainBit, MR60, camera emotion, LSL, OSC, LabRecorder, and Notion.
- `web_interface/`: browser pages, styles, scripts, cards, and fonts.
- `desktop_app/`: optional Tauri desktop launcher for one-click startup.
- `packaging/`: PyInstaller build files for the Python server sidecar.
- `settings/`: editable local settings for the active study and plugins.
- `saved_studies/`: saved study presets.
- `saved_results/`: participant results and optional sensor sidecar files.
- `docs/`: explanations and project notes.

## How A Study Run Works

1. The study lead opens `/admin`.
2. The admin page loads the active study from `settings/study_config.json`.
3. The study lead edits cards and saves.
4. A tablet opens the participant page.
5. Stimulus cards can send trial start and stop events through the plugin registry.
6. Answers are validated by the backend.
7. Results are saved locally in `saved_results/`.

## Plugin Flow

```text
server_app routes -> plugins/registry.py -> plugin folder -> adapter or external tool
```

The registry is explicit. A plugin only becomes active when it is imported and listed in `plugins/registry.py`.

## Where To Change Common Things

- Change a study: use the admin page or edit `settings/study_config.json`.
- Change plugin settings: use the dashboard controls or edit `settings/hardware_settings.json`.
- Add a question card type: add a module in `web_interface/scripts/cards/` and register it in the card index.
- Add a plugin: add a folder in `plugins/`, export `PLUGIN` from `plugin.py`, and list it in `plugins/registry.py`.
- Build the desktop launcher: see `docs/07_desktop_launcher.md`.
