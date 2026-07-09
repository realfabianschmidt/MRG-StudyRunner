# Data And Terms Explained

## Active Study

The active study is stored in `software/study_content/settings/study_config.json`.

Important fields:

- `study_id`: the study label.
- `questions`: the ordered card list shown to the participant.
- `study_settings`: study-level options such as sensor use and Notion upload target.
- `study_settings.sensors`: saved per-study sensor defaults for BrainBit, MR60, and camera emotion.

Saved presets are stored in `software/study_content/studies/` as `.study-runner` files.

The Admin dashboard can apply temporary runtime overrides for sensor integrations during the
current server session. These overrides do not rewrite the saved study unless the operator
explicitly saves the study settings in the editor.

## Results

Results are stored in `software/saved_results/<study_id>/<participant_id>/`.

The main result file contains:

- `participant_id`
- `study_id`
- `timestamp_start`
- `timestamp_end`
- `answers`
- `answer_events`
- `card_events`
- `answer_details`

Optional plugin sidecar files may be saved next to the main result file, for example BrainBit or MR60 samples.

## Biosignal raw data

The canonical raw biosignal recording format is XDF, written by LabRecorder from continuous LSL
streams during the whole survey session:

- BrainBit EEG and derived streams
- MR60 heart, breath, distance, phase, sequence/drop, and jitter streams
- Study Runner LSL markers for study, question, and stimulus timing

Compact JSON sidecars are secondary exports. They keep sensor-near samples with server timestamps
and copy `card_events` so question and stimulus intervals can be reconstructed without opening XDF.
Question summaries use the card's `shown_at` to `answered_at` interval. Stimulus summaries use
`active_started_at` to `active_ended_at`.

Tablet camera emotion has a live-monitor phase and a recording phase:

- Before Participant ID and study start, frames can update the dashboard live monitor but are not
  written to study results.
- After study start, emotion samples are saved only when camera emotion is effectively enabled and
  the active study/card context allows recording.

## Browser Cards

Question card modules live in `software/study_runner/web/scripts/cards/`. The type string in the study config must match the card registration in `software/study_runner/web/scripts/cards/index.js`.

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
- `Backend`: the Python server in `software/study_runner/backend/`.
- `Frontend`: the browser files in `software/study_runner/web/`.
- `Plugin`: a built-in integration folder under `software/study_runner/integrations/`.
- `Adapter`: code inside a plugin that talks to an external tool or device.
- `Registry`: `software/study_runner/integrations/registry.py`, the explicit list of active built-in plugins.
- `LSL`: Lab Streaming Layer, used for synchronized markers and streams.
- `OSC`: Open Sound Control, used for messages to tools such as TouchDesigner.
- `XDF`: recording format commonly written by LabRecorder.
- `Materiability`: the project font family stored in `software/study_runner/web/fonts/`.
