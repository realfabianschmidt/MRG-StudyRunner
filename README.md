# Study Runner

Study Runner is a small local web app for user studies. It is designed so researchers and designers can understand the project layout without being software developers.

## Quick Start

Install the required Python packages once:

```bash
pip install -r requirements.txt
```

Start the local server:

```bash
python server.py
```

Optional runtime settings:

```bash
STUDY_RUNNER_HOST=0.0.0.0
STUDY_RUNNER_PORT=3000
STUDY_RUNNER_DATA_DIR=/path/to/writable/study-runner-data
```

For tablet selfie-camera access, browsers usually require HTTPS. For local testing you can start the server with a temporary self-signed certificate:

```powershell
$env:STUDY_RUNNER_HTTPS='1'
python server.py
```

The terminal prints the local addresses.

- Admin page: `http://localhost:3000/admin`
- Participant page: `http://<computer-ip>:3000`

No internet connection is required during a study run. The computer and tablet only need to be on the same local WiFi network.

The admin hub also shows an Access card with copyable admin and participant links.

## Folder Map

```text
Software/
|-- server.py
|   Starts the local Flask server.
|-- server_app/
|   The backend web app.
|   |-- routes.py
|   |   Defines pages and API routes.
|   `-- services/
|       Small backend services for study settings, saved results, validation,
|       secrets, trial events, and admin status.
|-- plugins/
|   Built-in integration plugins. Each plugin has its own folder.
|   |-- registry.py
|   |   Explicitly lists the active built-in plugins.
|   |-- plugin_api.py
|   |   Defines the shared plugin contract.
|   |-- brainbit/
|   |-- mr60_mini_radar/
|   |-- tablet_camera_emotion/
|   |-- local_emotion_worker/
|   |-- lsl_markers/
|   |-- osc_touchdesigner/
|   |-- labrecorder_xdf/
|   `-- notion_upload/
|-- web_interface/
|   Browser files served under the `/static/...` URL.
|   |-- pages/
|   |   Admin and participant HTML pages.
|   |-- styles/
|   |   Visual styling.
|   |-- scripts/
|   |   Browser JavaScript, including card modules.
|   `-- fonts/
|       Materiability font files.
|-- settings/
|   Editable local settings.
|   |-- study_config.json
|   |   The active study.
|   `-- hardware_settings.json
|       Plugin and hardware settings.
|-- saved_studies/
|   Saved study presets.
|-- saved_results/
|   Local participant results, sidecar sensor files, upload queues, and optional XDF files.
|-- docs/
|   Human-readable project notes and guides.
`-- requirements.txt
    Required Python packages.
```

## Typical Workflow

1. Open `/admin`.
2. Edit the study ID, study settings, and question cards.
3. Save the study. The active file is updated in `settings/study_config.json`, and a preset copy is written to `saved_studies/`.
4. Open the participant page on the tablet.
5. Run the study. Results are written to `saved_results/<study_id>/<participant_id>/`.

## Plugins

Plugins are built-in integration folders, not an external marketplace. This keeps the project readable and avoids hidden runtime discovery.

To add a new integration:

1. Create a folder such as `plugins/my_new_sensor/`.
2. Add `__init__.py`, `plugin.py`, and `adapter.py`.
3. In `plugin.py`, export one `PLUGIN: IntegrationPlugin`.
4. Add an explicit import and tuple entry in `plugins/registry.py`.
5. Add the matching settings section in `settings/hardware_settings.json`.

The public integration keys stay stable:

- `brainbit`
- `mini_radar`
- `camera_emotion`
- `emotion_worker`
- `lsl`
- `osc`
- `labrecorder`
- `notion`

## Important Settings

- `settings/study_config.json`
  Stores the currently active study.

- `settings/hardware_settings.json`
  Stores plugin and hardware settings such as enabled flags, BrainBit paths, LSL settings, OSC target, Notion upload, and LabRecorder pickup.

- `settings/local_secrets.json`
  Optional backend-local secret file. It is ignored by Git. Use it for tokens such as the Notion API key.

## Saved Data

Each study gets its own folder in `saved_results/`. Each participant run gets its own participant folder.

The backend validates incoming data before saving. File and folder names are sanitized so unsafe study or participant labels cannot escape the saved-results folder.

Privacy rule: saved results should not contain direct personal details. The participant ID should be anonymous or pseudonymized.

## More Docs

- `docs/01_project_overview_for_everyone.md`
- `docs/02_data_and_terms_explained.md`
- `docs/03_plan_for_clearer_code.md`
- `docs/05_integration_plugin_guide.md`
- `docs/07_desktop_launcher.md`
