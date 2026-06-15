# Data And Terms Explained

## Active Study

The active study is stored in `settings/study_config.json`.

Important fields:

- `study_id`: the study label.
- `questions`: the ordered card list shown to the participant.
- `study_settings`: study-level options such as sensor use and Notion upload target.

Saved presets are stored in `saved_studies/` as `.study-runner` files.

## Results

Results are stored in `saved_results/<study_id>/<participant_id>/`.

The main result file contains:

- `participant_id`
- `study_id`
- `timestamp_start`
- `timestamp_end`
- `answers`
- `answer_events`
- `answer_details`

Optional plugin sidecar files may be saved next to the main result file, for example BrainBit or MR60 samples.

## Browser Cards

Question card modules live in `web_interface/scripts/cards/`. The type string in the study config must match the card registration in `web_interface/scripts/cards/index.js`.

Common card types include:

- `participant-id`
- `stimulus`
- `likert`
- `semantic`
- `choice`
- `single`
- `slider`
- `ranking`
- `multi-slider`
- `text`
- `word-cloud`
- `mood-meter`
- `finish`

## Terms

- `API`: a fixed backend route used by the browser, for example `/api/config`.
- `Backend`: the Python server in `server_app/`.
- `Frontend`: the browser files in `web_interface/`.
- `Plugin`: a built-in integration folder under `plugins/`.
- `Adapter`: code inside a plugin that talks to an external tool or device.
- `Registry`: `plugins/registry.py`, the explicit list of active built-in plugins.
- `LSL`: Lab Streaming Layer, used for synchronized markers and streams.
- `OSC`: Open Sound Control, used for messages to tools such as TouchDesigner.
- `XDF`: recording format commonly written by LabRecorder.
- `Materiability`: the project font family stored in `web_interface/fonts/`.
