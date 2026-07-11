# Operator Guide

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
4. The study lead checks the dashboard, selected sensors, LSL markers and LabRecorder/XDF.
5. A tablet opens the participant page over trusted HTTPS.
6. The participant enters a Participant ID. A study cannot start without it.
7. Sensor recording starts with the study start event.
8. Stimulus cards can send active start and stop events through the plugin registry.
9. Answers are validated by the backend.
10. Results are saved locally in `software/saved_results/`.

## Dashboard Overrides

Study settings define the saved sensor defaults for BrainBit, MR60 radar and
camera emotion. The dashboard can temporarily override those settings for the
current server session. This is intentional for lab setup and diagnostics.

Use the override warning as an operational signal: if a sensor is enabled from
the dashboard, that effective runtime setting wins until the server is restarted
or `Reset to study settings` is clicked. The study file is not rewritten unless
the study is explicitly saved in the editor.

## Camera Emotion And The Emotion Worker

Camera emotion analysis runs in a separate helper process called the
Local Emotion Worker (it loads the DeepFace model). You normally never
start it yourself:

- It starts automatically when a study with camera emotion enabled runs.
- The dashboard "Camera emotion" card shows its state in plain language
  (Running / Starting / Problem, with a suggested fix).
- If the worker crashes, Study Runner restarts it automatically (up to
  3 attempts). If it keeps failing, use the dashboard button
  **Repair DeepFace runtime**: it re-installs the model weights from the
  bundled copy (or downloads them) and restarts the worker. In packaged
  builds the repair never runs pip - it only fixes the model cache.
- The tablet camera needs HTTPS: install and fully trust the Study
  Runner Root CA on the iPad once (the server prints where the
  certificate file lives at startup).

Command line flags of the server (useful for diagnosis):

- `study-runner-server --emotion-worker-self-test --json` - checks that
  the packaged DeepFace runtime can load the model; prints a JSON
  verdict and exits 0/1. Run this after installing on a new machine.
- `study-runner-server --emotion-worker` - runs only the worker process
  (this is what Study Runner launches internally).
- `study-runner-server --apply-update` - applies a staged update
  (used internally by the updater restart).

## If Something Goes Wrong During A Study

- Answers are saved to the server after every card
  (`saved_results/<study>/_partial/`), so a closed tab or a dead tablet
  battery does not lose the session.
- If the final save fails, the raw submission is preserved under
  `saved_results/<study>/_recovery/` and the participant sees a message
  to call you - their answers stay on the screen.
- Each result entry contains `data_warnings` naming any sensor that had
  a gap or dropout while a card was shown.

## Desktop Shortcut

The Admin hub has a `Create desktop shortcut` button.

- In source mode it creates a shortcut that starts the current checkout with the
  active Python interpreter.
- In a packaged release it creates a shortcut to `study-runner-server(.exe)`.
- No shortcut is created automatically on startup.

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
- Build or update the packaged Python server: see `docs/release-and-update.md`.
