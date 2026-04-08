# Data and terms explained

This document explains the most important data fields and technical terms in the project.

## The configuration file `study_config.json`

This file describes how a study is set up.

- `study_id`: A short label for the study, for example `US1`.
- `questions`: The list of all cards in the study. This includes both question cards and stimulus cards.

## Question types in the configuration

Each question type has its own file in `static/js/cards/`. The type string in
`study_config.json` must match the file name without the `card-` prefix.

- `likert`: A rating scale, for example from 1 to 7.
  Fields: `prompt`, `scale` (number of steps), `label_min`, `label_max`.

- `semantic`: Opposing word pairs such as `alive | mechanical`.
  Fields: `prompt`, `pairs` (list of two-word arrays).

- `choice`: Multiple choice.
  Fields: `prompt`, `options` (list of strings).

- `single`: Single choice.
  Fields: `prompt`, `options` (list of strings).

- `slider`: A slider from 0 to 100.
  Fields: `prompt`, `label_min`, `label_max`.

- `ranking`: Put items in order using up and down buttons.
  Fields: `prompt`, `options` (list of strings).

- `text`: A free-text answer field.
  Fields: `prompt`.

- `stimulus`: A timed card with an optional warm-up phase before the active phase begins.
  Fields: `title`, `subtitle`, `warmup_duration_ms`, `duration_ms`, `trigger_type`,
  `trigger_content`, `send_signal`.

## Milliseconds explained quickly

- `1000 ms` = `1 second`
- `30000 ms` = `30 seconds`

## How card modules work

Each card type lives in its own file in `static/js/cards/`. Every card file
exports the same set of named functions:

- `renderStudy`: Returns the HTML shown to the participant.
- `renderEditor`: Returns the HTML for the admin sidebar editor.
- `collectConfig`: Reads the editor fields and returns the updated question data.
- `collectAnswer`: Reads the participant's answer from the DOM.

The `cards/index.js` file is the central registry. It imports all card modules
and makes them available to the admin and study controllers.

## Stimulus card phases

A stimulus card can have two phases:

- `Warm-up phase`: Optional preparation time before the actual timed task starts.
  During warm-up, the participant sees the instruction view.
- `Active phase`: The real timed phase. Signals, media triggers, and custom JS start here.

Stimulus cards are intentionally replayable if the participant navigates back to them.
Each replay starts the warm-up and active phase again from the beginning.

## Trigger types in stimulus cards

- `timer`: Countdown only.
- `image`: Shows an image during the active phase.
- `video`: Shows a video during the active phase.
- `audio`: Plays audio during the active phase.
- `html`: Shows researcher-provided inline HTML during the active phase.
- `js`: Runs researcher-provided JavaScript during the active phase.

The JS trigger receives a `study` helper with:

- `study.call(path, data)`: Sends a POST request to a local Flask route.
- `study.onCleanup(callback)`: Registers cleanup work when the card ends or is left early.

Important: `html` and `js` trigger content is treated as trusted researcher-authored lab content.
It should never come from participant input.

## What a saved result file contains

One JSON file is saved in the `data/` folder for each study run.

- `participant_id`: The anonymous ID of the participant.
- `study_id`: The label of the study.
- `timestamp_start`: The start time of the run.
- `timestamp_end`: The end time of the run.
- `answers`: The actual answers. Each answer is stored under a key like `q0`, `q1`, and so on.

## Important abbreviations

- `API`: Application Programming Interface. Here this means fixed web addresses such as `/api/config`.
- `HTML`: The structure of a web page.
- `CSS`: The visual styling of a web page. All styles are in `static/css/main.css`.
- `JavaScript` or `JS`: The logic that runs in the browser.
- `JSON`: A simple text format for settings and data.
- `LSL`: Lab Streaming Layer. A network protocol for synchronizing time-stamped data streams.
  The study runner sends event markers; BrainBit or similar devices send EEG data; LabRecorder
  captures both into one `.xdf` file.
- `OSC`: Open Sound Control. Short network messages used to control tools like TouchDesigner.
  The study runner sends `/study/start` and `/study/stop` messages at each active stimulus phase.
- `XDF`: Extensible Data Format. A container file written by LabRecorder. It holds EEG data,
  event markers, and timestamps in one place. Use `pyxdf` to read it; use `MNE-Python` to
  process the EEG stream inside.
- `SDK`: Software Development Kit. An extra package provided by a vendor, here for BrainBit.
- `PII`: Personally Identifiable Information. Data that can directly identify a person,
  such as a full name or email address.

## Terms used in this project

- `Admin`: The page used by the person who prepares the study.
- `Participant-ID`: The anonymous ID entered for the participant.
- `Handler`: A small function that reacts to a click or browser request.
- `Service`: Clear business logic such as "save the config" or "write a result file".
- `Adapter`: A small bridge to external hardware or software.
- `Card`: One self-contained study unit. Handles display, editing, and answer collection.
- `Live preview`: The right side of the admin page shows the study exactly as the participant
  will see it. It updates immediately when the editor fields change.
- `Registry`: The `cards/index.js` file. It collects all card modules in one place so that
  the rest of the code does not need to know which files exist.
- `Materiability`: The custom font used throughout the project. Files are in `static/fonts/`.
- `Stimulus card`: A card type with an optional warm-up phase and an active timed phase.
- `LabRecorder`: A free standalone tool that listens on the LSL network and saves all active streams into a single `.xdf` file with aligned timestamps.
- `Hardware adapter`: A small Python file in `app/integrations/` that connects one external tool (LSL, OSC). It initializes once at startup and does nothing if the required library is not installed.

## Why these explanations matter

This project should not only be understandable for developers. When the terms are clear,
it becomes much easier to talk about errors, new ideas, and future changes.
