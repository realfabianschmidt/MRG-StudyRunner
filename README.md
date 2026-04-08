# Study Runner

Study Runner is a small local web app for user studies.

The project is intentionally simple:

- one admin page for setting up the study
- one study page for the participant
- one Python server that keeps everything together locally
- local result files in the `data/` folder

This README is intentionally written so that people without a coding background can still use it.

## Quick start

Install once:

```bash
pip install -r requirements.txt
```

Start the server:

```bash
python server.py
```

The terminal will then show the local address.

- Admin page: `http://localhost:3000/admin`
- Study page on the iPad: `http://<ip>:3000`

`<ip>` means the IP address of the computer in the local network.

## Typical workflow

1. The study lead opens the admin page.
2. The left sidebar shows the settings: study ID and question list.
3. The right side of the admin page shows a live preview of each question card, exactly as the participant will see it.
4. The study lead clicks a card or a list item to open its editor in the sidebar.
5. Changes in the editor update the preview immediately.
6. The participant opens the study page on the iPad.
7. Stimulus cards run their countdown automatically and advance to the next card.
   Any number of stimulus cards can appear anywhere in the study flow.
8. The questionnaire appears as individual question cards.
9. The answers are saved as a local file.

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
|   |   Builds result file names and writes result files.
|   |-- trial_service.py
|   |   Sends start/stop signals to all active hardware integrations.
|   `-- integrations/
|       One file per hardware integration. Each file is self-contained.
|       |-- __init__.py
|       |-- lsl_adapter.py      Sends LSL event markers (requires pylsl)
|       `-- osc_adapter.py      Sends OSC messages to TouchDesigner
|-- hardware_config.json
|   Researcher-editable settings for hardware integrations (LSL, OSC).
|-- study_config.json
|   Stores the current study configuration.
|-- requirements.txt
|   Lists the required Python packages.
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
|       |   Participant page: manages the study flow, timer, and answer submission.
|       |-- admin-controller.js
|       |   Admin page: manages the sidebar, question list, live preview, and saving.
|       `-- cards/
|           One file per question type. Each file is fully self-contained.
|           |-- index.js
|           |   The central registry. Lists all card types and their defaults.
|           |-- card-likert.js       Likert scale (1 to 7)
|           |-- card-semantic.js     Semantic differential (opposing word pairs)
|           |-- card-choice.js       Multiple choice and single choice
|           |-- card-slider.js       Visual analog slider (0 to 100)
|           |-- card-ranking.js      Ranking (put items in order)
|           |-- card-text.js         Free-text answer
|           `-- card-stimulus.js     Stimulus / countdown card
|-- data/
|   Stores saved answer files as JSON.
`-- docs/
    Stores simple explanations, rules, and plans for the project.
```

## How question card files work

Each file in `static/js/cards/` handles one question type. To add a new question type, follow this order:

1. Create a new file `card-yourtype.js` in `static/js/cards/`
2. Define the default question data (`defaultQuestion`)
3. Write the study-side rendering (`renderStudy`) — how the participant sees it
4. Write the admin editor rendering (`renderEditor`) — how the study lead edits it
5. Write config collection (`collectConfig`) — reads the editor fields back into a question object
6. Write answer collection (`collectAnswer`) — reads the participant's answer from the DOM
7. Register the new type in `cards/index.js`
8. Update this README and `docs/02_data_and_terms_explained.md`

## Set up a new study

1. Open `/admin`
2. Change the study ID in the left sidebar, for example to `US2`
3. Use the type picker to add a Stimulus card wherever you need a timed waiting phase.
   Set its duration, title, subtitle, and trigger type in the editor overlay.
4. Add or edit question cards using the left sidebar
5. Save

No code changes are needed for this step.

## Privacy note

- All data stays local in the `data/` folder.
- No direct personal details such as full name or email address should be stored.
- Only an anonymous `Participant-ID` is saved.
- If data is exported later, it should still be checked for anonymity first.

## Hardware integrations: LSL, OSC, and .xdf

Hardware support is configured in `hardware_config.json` at the project root.
Set `"enabled": true` for each integration you want to use, then restart the server.

```json
{
  "lsl": { "enabled": true, "stream_name": "StudyRunner", "stream_type": "Markers" },
  "osc": { "enabled": true, "host": "127.0.0.1", "port": 9000,
           "address_start": "/study/start", "address_stop": "/study/stop" }
}
```

### LSL markers and .xdf recording (BrainBit / EEG)

When `lsl` is enabled, the server sends a `"start"` marker over LSL at the beginning
of each stimulus card and a `"stop"` marker at the end.

**LabRecorder** (a free standalone tool) listens on the LSL network and records
all active streams — EEG data from BrainBit and the markers from this server — into
a single `.xdf` file with aligned timestamps.

After the study, you can analyse the recording:
1. `pyxdf` reads the `.xdf` container and separates the streams.
2. `MNE-Python` processes the EEG stream (transpose the data, convert units µV → V).
3. The `participant_id` and `timestamp_start` in the JSON result file link the answers
   to the EEG recording.

Install pylsl to enable this:

```bash
pip install pylsl
```

### OSC messages (TouchDesigner)

When `osc` is enabled, the server sends a `/study/start` message at the beginning
of each stimulus and `/study/stop` at the end. The host, port, and message addresses
are all configurable in `hardware_config.json`.

`python-osc` is already included in `requirements.txt`.

### Custom JavaScript trigger

Stimulus cards support a JS trigger type. The snippet receives a `study` helper that
can POST to any Flask endpoint:

```js
// Example snippet — calls a custom route you add to routes.py:
study.call('/api/osc/send', { address: '/td/record', value: 1 });
study.call('/api/lsl/marker', { value: 'custom_event' });
```

All hardware logic stays on the server side. The snippet only sends a request.

### Adding a new integration

1. Create a new file in `app/integrations/`, for example `brainbit_adapter.py`.
2. Add an `initialize()` function and the action functions you need.
3. Call `initialize()` from `app/__init__.py` (following the pattern for lsl and osc).
4. Call the action functions from `app/trial_service.py`.

## Terms and abbreviations

- `API`: Application Programming Interface. In this project, this means fixed web addresses such as `/api/config`.
- `HTML`: The structure of a web page.
- `CSS`: The visual styling of a web page. All styles are in `static/css/main.css`.
- `JavaScript` or `JS`: The logic that runs in the browser.
- `JSON`: A simple text format used for settings and saved data.
- `Card`: One self-contained question unit. It includes how the question looks, how the answer is collected, and how the study lead edits it.
- `Live preview`: The right side of the admin page shows exactly how each question card will look to the participant. Changes in the editor update this view immediately.
- `SDK`: Software Development Kit. An extra package provided by a hardware or software vendor, for example BrainBit.
- `PII`: Personally Identifiable Information. Data that can directly identify a person, such as a full name or email address.
- `Participant-ID`: The anonymous ID entered for the study participant.
- `Materiability`: The custom font used throughout the project, loaded from `static/fonts/`.
- `Stimulus card`: A card that shows a countdown and optional content (image, video, audio, HTML, or custom JavaScript). It auto-advances when the countdown ends. No participant input is collected.

## Additional documents

- `PROJECT_RULES.md`
  The project-specific working rules in clear English.

- `docs/01_project_overview_for_everyone.md`
  A short overview for team members who do not want to start with the code.

- `docs/02_data_and_terms_explained.md`
  Explains data fields, question types, and important terms.

- `docs/03_plan_for_clearer_code.md`
  Describes the current status and remaining steps for cleaner code.
