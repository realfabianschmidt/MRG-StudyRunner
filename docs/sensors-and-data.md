# Sensors And Data

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

## Sensor Source Versus Runtime

The lab workspace keeps two intentionally different sensor areas:

| Area | Purpose | Rule |
| --- | --- | --- |
| `../Sensorik/` | Hardware origin, experiments, vendor files, Arduino sketches, TouchDesigner references and notes. | Keep it as a lab reference. Do not assume every file is production-ready app code. |
| `software/study_runner/integrations/` | Curated runtime code used by Study Runner, tests, packaging and releases. | Only this copy is loaded by the server and shipped in releases. |

Current mapping:

| Sensor | Origin/reference | Runtime copy |
| --- | --- | --- |
| MR60 radar | `../Sensorik/MR60BHA2/` | `software/study_runner/integrations/mr60_mini_radar/` |
| BrainBit EEG | `../Sensorik/BrainBit/` | `software/study_runner/integrations/brainbit/` |
| Tablet camera emotion | Browser camera plus server-side DeepFace dependency | `software/study_runner/integrations/tablet_camera_emotion/` and `software/study_runner/integrations/local_emotion_worker/` |

Workflow for new hardware:

1. Put vendor/reference files and rough experiments in `../Sensorik/`.
2. Promote only the tested runtime subset into `software/study_runner/integrations/<sensor_key>/`.
3. Add or update tests, operator docs and dashboard status fields.
4. Release from the integration copy, not from the raw `Sensorik/` experiment folder.

This split keeps the repo usable for non-coders while still preserving hardware
reference material for lab work.

## Research-Grade Boundary

Study Runner is suitable as a research-grade lab tool for local studies. It is
not a medical device, not a diagnostic system, and not validated for GCP, 21 CFR
Part 11, HIPAA or clinical-trial source-data compliance.

Strong current points:

- Participant ID is required before a study can start.
- LSL/XDF is the primary raw-data path for synchronized multimodal recording.
- Study Runner markers identify study, question and stimulus timing.
- JSON result files and sidecars keep card intervals and compact summaries.
- Camera frames can be monitored before the study but are only recorded after study start.

Known limits:

- No immutable audit trail for every operator action yet.
- No electronic signatures or role-based approval workflow.
- BLE, browser and camera timing should be treated as lab timing, not hardware trigger timing.
- BrainBit, MR60 and DeepFace values are research signals, not clinical measurements.
- LabRecorder stream visibility should still be checked manually before critical runs.

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
