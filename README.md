# Study Runner

## What is this?

Study Runner is a small local web app for user studies.

The project is intentionally simple:

- one admin page for setting up the study
- one study page for the participant
- one small Python server app that keeps everything together locally
- local result files in the `data/` folder

This README is intentionally written so that people without a coding background can still use it.

## Quick start

Install once:

```bash
pip install -r requirements.txt
```

Optional hardware packages can now auto-install themselves when the related integration
is enabled in `hardware_config.json`.

Start the server:

```bash
python server.py
```

The terminal will then show the local addresses.

- Admin page: `http://localhost:3000/admin`
- Study page on the iPad: `http://<ip>:3000`

`<ip>` means the IP address of the computer in the local network.

No internet connection is required during a study run. The computer and the iPad only need to be in the same local WiFi network.

## Typical workflow

1. The study lead opens the admin page.
2. The left sidebar shows the settings and question list.
3. The right side of the admin page shows a live preview of each card, exactly as the participant will see it.
4. The study lead clicks a card or a list item to open its editor in the sidebar overlay.
5. Changes in the editor update the preview immediately.
6. The question list in the sidebar can be reordered with the drag handle.
7. The participant opens the study page on the iPad.
8. Stimulus cards can appear anywhere in the study flow.
9. A stimulus card may begin with an optional warm-up phase. When the warm-up ends, the active timer begins automatically.
10. Question cards appear one at a time.
11. At the end, the answers are validated and saved as a local JSON file.

## What each file is for

```text
study-runner/
|-- server.py
|   Starts the app and runs the local Flask server.
|-- app/
|   |-- __init__.py
|   |   Creates the Flask app, reads hardware_config.json, and starts active integrations.
|   |-- routes.py
|   |   Defines the web pages and API routes.
|   |-- config_service.py
|   |   Loads and saves the study configuration.
|   |-- results_service.py
|   |   Builds safe result file names and writes result files.
|   |-- validation.py
|   |   Validates incoming config and result payloads before saving.
|   |-- trial_service.py
|   |   Sends start and stop signals to all active hardware integrations.
|   `-- integrations/
|       One file per hardware integration. Each file is self-contained.
|       |-- __init__.py
|       |-- dependency_utils.py Optional dependency checks and auto-install helper
|       |-- lsl_adapter.py      Sends LSL event markers
|       |-- osc_adapter.py      Sends OSC messages to TouchDesigner
|       `-- brainbit_adapter.py Starts the repo-local BrainBit CLI and optional LSL mirroring
|-- hardware_config.json
|   Researcher-editable settings for hardware integrations (LSL, OSC, BrainBit).
|-- study_config.json
|   Stores the current study configuration.
|-- requirements.txt
|   Lists the required Python packages.
|-- brainbit/
|   Repo-local BrainBit helper files, docs, and the TouchDesigner example project.
|-- static/
|   |-- admin.html
|   |   The admin page structure. Uses a two-column layout:
|   |   left sidebar for settings and editing, right area for the live preview.
|   |-- study.html
|   |   The participant page structure. Questions appear as individual cards.
|   |-- css/
|   |   `-- main.css
|   |       All visual styles in one central file.
|   |       Also loads the Materiability font from static/fonts/.
|   |-- fonts/
|   |   Materiability font files used throughout the project.
|   `-- js/
|       |-- api-client.js
|       |   Shared browser helper for API requests.
|       |-- study-controller.js
|       |   Participant page: manages study flow, timers, triggers, and answer submission.
|       |-- admin-controller.js
|       |   Admin page: manages the sidebar, question list, drag-and-drop sorting,
|       |   live preview, and saving.
|       `-- cards/
|           One file per card type. Each file is self-contained.
|           |-- index.js
|           |   The central registry. Lists all card types and their defaults.
|           |-- card-likert.js       Likert scale
|           |-- card-semantic.js     Semantic differential
|           |-- card-choice.js       Multiple choice and single choice
|           |-- card-slider.js       Slider from 0 to 100
|           |-- card-ranking.js      Ranking with up/down controls inside the card
|           |-- card-text.js         Free-text answer
|           `-- card-stimulus.js     Stimulus / countdown card
|-- data/
|   Stores participant output folders with JSON results and optional XDF files.
`-- docs/
    Stores simple explanations, rules, and plans for the project.
```

## How the app is split

### Server side

The server runs on macOS or Windows and handles all backend work.

- `Flask` hosts the pages and API routes.
- `config_service.py` reads and writes `study_config.json`.
- `validation.py` checks whether config and result payloads are complete enough to save.
- `results_service.py` writes result files into `data/` using safe file names.
- `trial_service.py` triggers active hardware integrations.
- `lsl_adapter.py` can send LSL markers when enabled.
- `osc_adapter.py` can send OSC messages to TouchDesigner or another OSC host.
- `brainbit_adapter.py` can start the repo-local BrainBit CLI from `brainbit/`,
  keep it running in the background, and optionally mirror selected BrainBit values into LSL.

The current built-in hardware path is:

- optional LSL event markers from this server
- optional OSC start and stop messages from this server
- optional BrainBit EEG / bands / mental-state OSC data from the repo-local BrainBit CLI
- optional BrainBit-to-LSL mirroring for LabRecorder
- optional LabRecorder workflow for synchronized `.xdf` files

### Browser side

The browser side runs in Safari on the iPad and in the admin browser on the lab computer.

- The participant sees one card at a time.
- The admin sees a two-column page: left for settings and editing, right for live preview.
- Browser code sends and receives data through the local API.
- All styling comes from `static/css/main.css`.
- Each card type has its own JavaScript file in `static/js/cards/`.

Important: `jsPsych` is not part of the current version. The current flow is handled by the project's own JavaScript modules.

## Current card types

The study flow is card-based. A study can contain any mix of these card types:

- `stimulus`
  Countdown card with an optional warm-up phase before the active phase.

- `likert`
  Rating scale, for example 1 to 7.

- `semantic`
  Opposing word pairs such as `alive | mechanical`.

- `choice`
  Multiple choice.

- `single`
  Single choice.

- `slider`
  Slider from 0 to 100.

- `ranking`
  Reorder items with up and down buttons inside the question card.

- `text`
  Free-text answer.

## Stimulus cards and triggers

Stimulus cards can appear anywhere in the study flow and are intentionally replayable if the participant navigates back to them.

Each stimulus card has its own:

- title
- subtitle
- warm-up duration
- active duration
- trigger type
- trigger content
- Study Runner signal on/off setting
- BrainBit -> LSL on/off setting
- BrainBit -> TouchDesigner on/off setting

A stimulus card can have two phases:

- Warm-up phase
  Optional preparation time. This phase only shows the instruction view.

- Active phase
  The real stimulus phase. The active timer begins here. Signals, media triggers, and custom JavaScript begin here.

The current trigger types are:

- `timer`
  Countdown only.

- `image`
  Show an image during the active phase.

- `video`
  Show a video during the active phase.

- `audio`
  Play audio during the active phase.

- `html`
  Show researcher-provided inline HTML during the active phase.

- `js`
  Run researcher-provided JavaScript in the browser during the active phase.

If `send_signal` is enabled for that stimulus card, the server sends the Study Runner
start/stop actions at the beginning and end of the active phase:

- LSL `start` / `stop` markers from Study Runner
- OSC `/study/start` / `/study/stop` from Study Runner

If `brainbit_to_lsl` is enabled for that stimulus card, BrainBit EEG-related values are mirrored
into the BrainBit LSL streams only during that active phase.

If `brainbit_to_touchdesigner` is enabled for that stimulus card, BrainBit values are forwarded
to TouchDesigner only during that active phase.

The JS trigger receives a small `study` helper with two functions:

- `study.call(path, data)`
  Sends a POST request to a local Flask route.

- `study.onCleanup(callback)`
  Registers cleanup logic that runs when the participant leaves that stimulus card or when the active phase ends.

Important: custom `html` and `js` trigger content is treated as trusted researcher-authored content. It should never come from participant input.

## Set up a new study

1. Open `/admin`
2. Change the study ID, for example to `US2`
3. Add, remove, or reorder cards with the drag handle in the admin list
4. Edit the card content in the sidebar overlay
5. Save

No code changes are needed for a normal study setup.

## What gets saved

One participant folder is saved in `data/` for each study run.

Inside that folder, Study Runner saves:

- `<participant_id>/<participant_id>.json`
- optionally `<participant_id>/<participant_id>.xdf` if `labrecorder` pickup is configured

If files with that exact name already exist for the same participant, Study Runner keeps the
folder and adds a numeric suffix such as `_2` instead of overwriting older runs.

Each saved JSON result file contains:

- `participant_id`
  Anonymous participant label entered at the start.

- `study_id`
  Study label from `study_config.json`.

- `timestamp_start`
  Start time of the run.

- `timestamp_end`
  End time of the run.

- `answers`
  The recorded answers for all non-stimulus cards.

The server validates the payload before saving it. Folder and file names use a safe version of the
participant ID so that broken or unsafe names do not escape the `data/` folder.

## Privacy note

- All data stays local in the `data/` folder.
- No direct personal details such as full name or email address should be stored.
- Only an anonymous `Participant-ID` is saved.
- If data is exported later, it should still be checked for anonymity first.

## Hardware integrations: LSL, OSC, BrainBit, and `.xdf`

### Current state

- OSC support is already built in through `app/integrations/osc_adapter.py`.
- LSL marker support is already built in through `app/integrations/lsl_adapter.py`.
- BrainBit support is built in through `app/integrations/brainbit_adapter.py`.
- The BrainBit adapter launches the repo-local script `brainbit/brainbit_realtime_cli_OSC_15.py`.
- The recommended current EEG sync path is BrainBit LSL mirroring plus Study Runner markers in LabRecorder.

### Enable hardware integrations

Hardware support is configured in `hardware_config.json` at the project root. Set `"enabled": true` for each integration you want to use, then restart the server.

```json
{
  "lsl": {
    "enabled": true,
    "auto_install": true,
    "stream_name": "StudyRunner",
    "stream_type": "Markers"
  },
  "osc": {
    "enabled": true,
    "auto_install": true,
    "host": "127.0.0.1",
    "port": 8000,
    "address_start": "/study/start",
    "address_stop": "/study/stop"
  },
  "brainbit": {
    "enabled": true,
    "script_path": "brainbit/brainbit_realtime_cli_OSC_15.py",
    "working_dir": "brainbit",
    "log_dir": "brainbit/logs",
    "python_executable": {
      "windows": "",
      "macos": ""
    },
    "osc_host": "127.0.0.1",
    "osc_port": 8000,
    "scan_seconds": 5,
    "resist_seconds": 6,
    "signal_seconds": 0,
    "pretty": false,
    "debug": false,
    "quiet_output": true,
    "open_monitor_terminal": true,
    "monitor_refresh_ms": 1000,
    "disconnect_timeout_ms": 5000,
    "lsl": {
      "enabled": true,
      "auto_install": true,
      "stream_prefix": "BrainBit"
    }
  },
  "labrecorder": {
    "enabled": false,
    "xdf_source_dir": "brainbit/recordings",
    "move_xdf": false,
    "lookback_minutes": 120,
    "lookahead_minutes": 120
  }
}
```

For path-like BrainBit fields you can use either:

- one plain string for all systems
- or an object with OS-specific values such as `windows` and `macos`

Study Runner resolves the correct value automatically at startup. Relative paths are resolved
from the project root, so `brainbit/brainbit_realtime_cli_OSC_15.py` works on both Windows
and macOS inside the same repository checkout.

If `labrecorder.enabled` is true, Study Runner also looks for a matching `.xdf` file in
`xdf_source_dir` and copies or moves it into the participant folder under the participant ID.
This path may also be relative to the project root, for example `brainbit/recordings`.

### LSL markers and `.xdf` recording

LSL stands for Lab Streaming Layer.

When `lsl` is enabled, the server sends a `start` marker at the beginning of an active stimulus
phase and a `stop` marker at the end, if `send_signal` is enabled for that stimulus card.

If `brainbit.lsl.enabled` is also true, the BrainBit adapter mirrors selected BrainBit data into
additional LSL streams:

- `BrainBit_EEG`
- `BrainBit_BANDS`
- `BrainBit_MENTAL`
- `BrainBit_QUALITY`
- `BrainBit_BATTERY`

`LabRecorder` is a free standalone tool that listens on the LSL network and records all active
streams into a single `.xdf` file with aligned timestamps.

Typical workflow:

1. Enable `lsl` in `hardware_config.json`
2. Enable `brainbit` and `brainbit.lsl` if you want BrainBit mirrored into LSL
3. Start the Study Runner server
4. Let the server auto-install `pylsl` if needed, or install it manually
5. Start LabRecorder
6. Record the BrainBit streams and the Study Runner marker stream together
7. Use `pyxdf` and later `MNE-Python` for analysis

Install `pylsl` manually only if you do not want to rely on auto-install:

```bash
pip install pylsl
```

### OSC messages for TouchDesigner

Two OSC paths now exist:

- Study Runner can send `/study/start` and `/study/stop` during stimulus cards through
  `app/integrations/osc_adapter.py`.
- The BrainBit adapter forwards continuous `/BrainBit/...` messages to TouchDesigner while the
  BrainBit device is running.

The host, port, and message addresses all come from `hardware_config.json`. If your TouchDesigner
patch listens on a single OSC port, both Study Runner and BrainBit can target that same port.

The provided TouchDesigner project from `brainbit/HelloEEG_HelloMYO_01.3.toe`
is expected to listen for the BrainBit OSC namespace on port `8000`.

`python-osc` is included in `requirements.txt`, and Study Runner can auto-install it if the OSC
integration is enabled and the package is missing.

### BrainBit integration

When `brainbit.enabled` is true, Study Runner starts the repo-local BrainBit CLI at server startup.

What the adapter does:

- launches `brainbit/brainbit_realtime_cli_OSC_15.py`
- keeps reading its console output in the background
- forwards parsed BrainBit values to TouchDesigner through the configured OSC port
- optionally mirrors selected BrainBit values into LSL for LabRecorder
- writes the full BrainBit output to `brainbit_runtime.log`
- writes the latest parsed values to `brainbit_state.json`
- can open a second terminal window with a stable live monitor instead of spamming the server console
  on both Windows and macOS
- stops the BrainBit process automatically when the server exits

Important notes:

- BrainBit is not started and stopped per stimulus card. It is managed once at server startup.
- Study Runner still controls the stimulus markers (`/study/start`, `/study/stop`) separately.
- The repo-local BrainBit script already auto-installs its own SDK-related packages:
  `pyneurosdk2`, `python-osc`, and `pyem-st-artifacts`.
- Recommended console setup:
  `pretty: false`, `quiet_output: true`, `open_monitor_terminal: true`

### Custom JavaScript trigger

Stimulus cards support a JS trigger type. The snippet runs in the browser during the active phase. It can call a route that already exists, or a custom route that you add yourself later.

```js
// Example snippet. These routes are examples you would add yourself in routes.py.
study.call('/api/osc/send', { address: '/td/record', value: 1 });
study.call('/api/lsl/marker', { value: 'custom_event' });
```

All hardware logic stays on the server side. The snippet only sends a request.

### Adding a new integration

1. Create a new file in `app/integrations/`, for example `brainbit_adapter.py`
2. Add an `initialize()` function and the action functions you need
3. Call `initialize()` from `app/__init__.py` following the pattern for LSL and OSC
4. Call the action functions from `app/trial_service.py`

## How question card files work

Each file in `static/js/cards/` handles one question type. To add a new question type, follow this order:

1. Create a new file `card-yourtype.js` in `static/js/cards/`
2. Define the default question data (`defaultQuestion`)
3. Write the study-side rendering (`renderStudy`) so the participant can see it
4. Write the admin editor rendering (`renderEditor`) so the study lead can edit it
5. Write config collection (`collectConfig`) to read the editor fields back into a question object
6. Write answer collection (`collectAnswer`) to read the participant's answer from the DOM
7. Register the new type in `cards/index.js`
8. Update this README and `docs/02_data_and_terms_explained.md`

## Terms and abbreviations

- `API`
  Application Programming Interface. In this project, this means fixed web addresses such as `/api/config`.

- `HTML`
  The structure of a web page.

- `CSS`
  The visual styling of a web page. All styles are in `static/css/main.css`.

- `JavaScript` or `JS`
  The logic that runs in the browser.

- `JSON`
  A simple text format used for settings and saved data.

- `Flask`
  The Python web framework used by this project.

- `LSL`
  Lab Streaming Layer. A system for time-synchronized data streams and markers.

- `OSC`
  Open Sound Control. Lightweight network messages used by tools such as TouchDesigner.

- `XDF`
  A recording file format often used with LabRecorder for synchronized streams.

- `SDK`
  Software Development Kit. An extra package provided by a hardware or software vendor, for example BrainBit.

- `PII`
  Personally Identifiable Information. Data that can directly identify a person, such as a full name or email address.

- `Participant-ID`
  The anonymous ID entered for the study participant.

- `Card`
  One self-contained unit in the study flow, for example a stimulus card or a question card.

- `Live preview`
  The right side of the admin page shows exactly how each card will look to the participant.

- `Materiability`
  The custom font used throughout the project, loaded from `static/fonts/`.

## Additional documents

- `PROJECT_RULES.md`
  The project-specific working rules in clear English.

- `docs/01_project_overview_for_everyone.md`
  A short overview for team members who do not want to start with the code.

- `docs/02_data_and_terms_explained.md`
  Explains data fields, question types, and important terms.

- `docs/03_plan_for_clearer_code.md`
  Describes the current status and remaining steps for cleaner code.
