# Project Overview For Everyone

Study Runner is a local study tool with one admin page, one participant page, and optional built-in plugins for lab integrations.

## Main Parts

The editable program lives in `software/`; Python-only release tooling lives in `release_tools/`.

- `software/server.py`: starts the local server.
- `software/study_runner/backend/`: backend app and backend services.
- `software/study_runner/integrations/`: built-in integrations such as BrainBit, MR60, camera emotion, LSL, OSC, LabRecorder, and Notion.
- `software/study_runner/web/`: browser pages, styles, scripts, cards, and fonts.
- `software/study_content/settings/`: editable local settings for the active study and plugins.
- `software/study_content/studies/`: saved study presets.
- `software/saved_results/`: participant results and optional sensor sidecar files.
- `release_tools/pyinstaller/`: PyInstaller build files for the packaged Python app.
- `release_tools/`: release checks, version bumping, ZIP packaging, and update manifest tools.
- `docs/`: explanations and project notes.

## How A Study Run Works

1. The study lead opens `/admin`.
2. The admin page loads the active study from `software/study_content/settings/study_config.json`.
3. The study lead edits cards and saves.
4. A tablet opens the participant page.
5. Stimulus cards can send trial start and stop events through the plugin registry.
6. Answers are validated by the backend.
7. Results are saved locally in `software/saved_results/`.

## Plugin Flow

```text
software/study_runner/backend routes -> software/study_runner/integrations/registry.py -> plugin folder -> adapter or external tool
```

The registry is explicit. A plugin only becomes active when it is imported and listed in `software/study_runner/integrations/registry.py`.

## Where To Change Common Things

- Change a study: use the admin page or edit `software/study_content/settings/study_config.json`.
- Change plugin settings: use the dashboard controls or edit `software/study_content/settings/hardware_settings.json`.
- Add a question card type: add a module in `software/study_runner/web/scripts/cards/` and register it in the card index.
- Add a plugin: add a folder in `software/study_runner/integrations/`, export `PLUGIN` from `plugin.py`, and list it in `software/study_runner/integrations/registry.py`.
- Build the packaged Python app: see `docs/07_desktop_launcher.md`.
