# Data and terms explained

This document explains the most important data fields and technical terms in the project.

## The configuration file `study_config.json`

This file describes how a study is set up.

- `study_id`: A short label for the study, for example `US1`.
- `questions`: The list of all cards in the study. This includes both question cards and stimulus cards.

Stimulus cards are entries in the `questions` list with `type: stimulus`. They have their own duration, title, subtitle, trigger type, and content fields. The old `stimulus_duration_ms` root key is no longer used.

## Milliseconds explained quickly

- `1000 ms` = `1 second`
- `30000 ms` = `30 seconds`

## Question types in the configuration

Each question type has its own file in `static/js/cards/`. The type string in
`study_config.json` must match the file name without the `card-` prefix.

- `likert`: A rating scale, for example from 1 to 7.
  Fields: `prompt`, `scale` (number of steps), `label_min`, `label_max`.

- `semantic`: Opposing word pairs such as `alive | mechanical`.
  Fields: `prompt`, `pairs` (list of two-word arrays).

- `choice`: Multiple choice. The participant can select more than one option.
  Fields: `prompt`, `options` (list of strings), `multiple` (true).

- `single`: Single choice. The participant selects exactly one option.
  Fields: `prompt`, `options` (list of strings).

- `slider`: A slider from 0 to 100.
  Fields: `prompt`, `label_min`, `label_max`.

- `ranking`: Put items in order using up and down buttons.
  Fields: `prompt`, `options` (list of strings).

- `text`: A free-text answer field.
  Fields: `prompt`.

- `stimulus`: A timed waiting phase. The participant sees a countdown. The card auto-advances when the countdown ends. No participant answer is collected.
  Fields: `title`, `subtitle`, `duration_ms` (in milliseconds), `trigger_type` (`timer` / `image` / `video` / `audio` / `html` / `js`), `trigger_content` (a URL or code string depending on the trigger type), `send_signal` (true or false — whether to send `/api/start` and `/api/stop` signals).

## How card modules work

Each question type lives in its own file in `static/js/cards/`. Every card file
exports the same set of named functions:

- `renderStudy`: Returns the HTML shown to the participant.
- `renderEditor`: Returns the HTML for the admin sidebar editor.
- `collectConfig`: Reads the editor fields and returns the updated question data.
- `collectAnswer`: Reads the participant's answer from the DOM.

The `cards/index.js` file is the central registry. It imports all card modules
and makes them available to the admin and study controllers.

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
  The study runner sends `/study/start` and `/study/stop` messages at each stimulus card.
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
- `Card`: One self-contained question unit. Handles display, editing, and answer collection.
- `Live preview`: The right side of the admin page shows the study exactly as the participant
  will see it. It updates immediately when the editor fields change.
- `Registry`: The `cards/index.js` file. It collects all card modules in one place so that
  the rest of the code does not need to know which files exist.
- `Materiability`: The custom font used throughout the project. Files are in `static/fonts/`.
- `Stimulus card`: A card type that shows a timed countdown with optional content. It fires start/stop signals to the server and auto-advances when done.
- `Trigger type`: The kind of content or action that accompanies a stimulus countdown. Options are `timer` (countdown only), `image`, `video`, `audio`, `html` (inline HTML), or `js` (custom JavaScript snippet). The JS trigger receives a `study` helper for calling Flask API endpoints.
- `LabRecorder`: A free standalone tool that listens on the LSL network and saves all active streams into a single `.xdf` file with aligned timestamps.
- `Hardware adapter`: A small Python file in `app/integrations/` that connects one external tool (LSL, OSC). It initializes once at startup and does nothing if the required library is not installed.

## Why these explanations matter

This project should not only be understandable for developers. When the terms are clear,
it becomes much easier to talk about errors, new ideas, and future changes.
