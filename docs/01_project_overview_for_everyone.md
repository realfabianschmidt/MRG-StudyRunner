# Project overview for everyone

This document is for people who want to understand the project without reading the code first.

## What this project does

Study Runner is a small local web app for user studies.

- One person on the team sets up the study.
- A participant answers the questions on an iPad.
- The server saves the answers as local files.
- The server can trigger optional external tools such as LSL marker streams, TouchDesigner,
  or a repo-local BrainBit process.
- Stimulus cards can include an optional warm-up phase before the active phase begins.

## Who uses which page

- `Admin page`: Used by the person who prepares the study. The left sidebar holds the settings,
  question list, and question editor. The right side shows a live preview of the study as the
  participant will see it.
- `Study page`: Used by the participant on the iPad. Questions appear as individual cards,
  one after the other on a single screen.
- `Server`: Runs on the lab computer and connects both pages.

## Simple flow

1. The computer starts `server.py`.
2. The study lead opens `/admin` and checks the configuration in the left sidebar.
3. The study lead edits or adds cards. Changes appear instantly in the right preview.
4. The participant opens the study page on the iPad.
5. A stimulus card can first show a warm-up instruction phase.
6. Then the active stimulus phase begins and optional signals or trigger actions are fired.
7. The remaining question cards are answered one by one.
8. The answers are validated and saved in the `data/` folder.

## What each file group is for

- `server.py`: Small startup file for the Flask app.
- `app/`: Backend logic split by responsibility.
- `app/integrations/`: One file per hardware integration (LSL, OSC, BrainBit). Each file is
  self-contained and does nothing if the required library is not installed.
- `app/validation.py`: Checks whether incoming config and result data can be saved safely.
- `hardware_config.json`: Researcher-editable settings for hardware integrations.
  Set `enabled: true` to activate LSL markers, OSC messages, or the BrainBit integration.
  Restart the server after changes.
- `study_config.json`: Stores the current study configuration.
- `brainbit/`: Stores the repo-local BrainBit script, BrainBit notes, and the TouchDesigner example file.
- `static/admin.html` and `static/study.html`: The page structure.
- `static/css/main.css`: All visual styles in one central file, including the Materiability font.
- `static/fonts/`: The Materiability font files.
- `static/js/`: Browser logic split into small modules.
- `static/js/cards/`: One file per card type. Each file handles rendering, editing,
  and answer collection for its type.
- `data/`: Stores one folder per participant with JSON results and optional XDF files.
- `docs/`: Stores simple explanations, plans, and project rules.

## How the parts talk to each other

```text
Admin page  ->  Browser JavaScript  ->  Flask routes  ->  config and result services
Study page  ->  Browser JavaScript  ->  Flask routes  ->  config and result services
Flask app   ->  trial_service.py    ->  optional tools such as LSL or TouchDesigner
Flask app   ->  brainbit_adapter.py ->  repo-local BrainBit CLI -> TouchDesigner / optional LSL
```

## Common questions from non-coders

- "Where do I change the questions?"
  In the admin page sidebar, or directly in `study_config.json`.

- "Where can I find the saved answers?"
  In the `data/` folder.

- "Where do I change the visual design?"
  All styles are in `static/css/main.css`.

- "Where would a new hardware integration be added?"
  Create a new file in `app/integrations/` following the pattern in `lsl_adapter.py` or
  `osc_adapter.py`. For a background process like BrainBit, follow `brainbit_adapter.py`.

- "How do I add a new question type?"
  Create a new file in `static/js/cards/`, then register it in `cards/index.js`.
  The README explains the full step-by-step process.

## What the recent cleanup improved

- All styles live in one central CSS file (`main.css`) with the custom Materiability font.
- Each card type is a self-contained module in `static/js/cards/`.
- Stimulus cards support an optional warm-up phase before the active phase starts.
- Config and result payloads are validated before they are saved.
- Clearer separation between startup, routes, config logic, result logic, and trial control.
- Better orientation for people who are not familiar with large all-in-one files.

The remaining open steps are described in `docs/03_plan_for_clearer_code.md`.
