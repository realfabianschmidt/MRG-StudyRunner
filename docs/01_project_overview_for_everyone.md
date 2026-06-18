# Project Overview For Everyone

Study Runner is a local study tool with one admin page, one participant page, and optional built-in plugins for lab integrations.

## Main Parts

- `server.py`: starts the local server.
- `study_runner/backend/`: backend app and backend services.
- `study_runner/integrations/`: built-in integrations such as BrainBit, MR60, camera emotion, LSL, OSC, LabRecorder, and Notion.
- `study_runner/web/`: browser pages, styles, scripts, cards, and fonts.
- `desktop_wrapper/`: optional Tauri desktop launcher for one-click startup.
- `build_tools/pyinstaller/`: PyInstaller build files for the Python server sidecar.
- `study_content/settings/`: editable local settings for the active study and plugins.
- `study_content/studies/`: saved study presets.
- `saved_results/`: participant results and optional sensor sidecar files.
- `docs/`: explanations and project notes.

## How A Study Run Works

1. The study lead opens `/admin`.
2. The admin page loads the active study from `study_content/settings/study_config.json`.
3. The study lead edits cards and saves.
4. A tablet opens the participant page.
5. Stimulus cards can send trial start and stop events through the plugin registry.
6. Answers are validated by the backend.
7. Results are saved locally in `saved_results/`.

## Plugin Flow

```text
study_runner/backend routes -> study_runner/integrations/registry.py -> plugin folder -> adapter or external tool
```

The registry is explicit. A plugin only becomes active when it is imported and listed in `study_runner/integrations/registry.py`.

## Where To Change Common Things

- Change a study: use the admin page or edit `study_content/settings/study_config.json`.
- Change plugin settings: use the dashboard controls or edit `study_content/settings/hardware_settings.json`.
- Add a question card type: add a module in `study_runner/web/scripts/cards/` and register it in the card index.
- Add a plugin: add a folder in `study_runner/integrations/`, export `PLUGIN` from `plugin.py`, and list it in `study_runner/integrations/registry.py`.
- Build the desktop launcher: see `docs/07_desktop_launcher.md`.
