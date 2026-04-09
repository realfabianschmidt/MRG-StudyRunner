# Plan for clearer code

This plan describes how Study Runner can become clearer, safer, and easier to extend step by step.

Important: this is not a plugin architecture plan. The project should stay simple.

## Current status

Status as of April 2026:

- Step 1 is done. Names, structure, and docs are clean and up to date.
- Step 2 is done. Backend is split into small focused modules.
- Step 3 is done. The frontend is fully separated into HTML, CSS, and JavaScript.
  Each card type is a self-contained card module. All styles are in one central
  CSS file with the Materiability font. The admin page has a live preview layout.
  Stimulus cards now support an optional warm-up phase before the active phase starts.
- Step 4 is mostly done. Hardware adapters and server-side validation are in place.
  Remaining work is mainly deeper automated testing and future refinement.

## Target picture

In the end, the project should:

- be easier to read for non-programmers
- have clearer file responsibilities
- have better comments and documents
- make new question types easier to add
- connect external tools in a cleaner way

## The plan in four steps

## Step 1: Clean up names, structure, and docs

**Status: Done**

Goal:
Everyone on the team should understand more quickly where things live.

Work done:

- clear document names throughout
- README updated with a glossary, file explanations, and workflow instructions
- one simple project rules document for day-to-day work
- all three overview documents in `docs/` kept up to date

## Step 2: Split backend work into small jobs

**Status: Done**

Goal:
`server.py` should not do everything at once.

Work done:

```text
app/
  routes.py          Web pages and API routes
  config_service.py  Loads and saves the study configuration
  results_service.py Builds file names and writes result files
  validation.py      Validates incoming config and result payloads
  trial_service.py   Keeps study start/stop logic in one place
```

## Step 3: Make the frontend easier to read

**Status: Done**

Goal:
The frontend should be easy to navigate, extend, and read.

Work done:

- all styles in one central file `static/css/main.css`, including the Materiability font
- each card type is a self-contained module in `static/js/cards/`
- `cards/index.js` is the central registry for all card types
- admin page has a two-column layout: left sidebar for editing, right area for live preview
- study page shows questions as individual cards
- stimulus cards support a separate warm-up phase before the active timer and trigger phase
- stimulus media is cleaned up when a card ends or is left early

## Step 4: Improve validation and hardware integration

**Status: Mostly done**

Goal:
Errors should be caught earlier, and external tools should be connected more cleanly.

Work done:

- hardware integrations moved into small adapter files in `app/integrations/`
- LSL event markers via `lsl_adapter.py` (optional auto-install of `pylsl`)
- OSC messages to TouchDesigner via `osc_adapter.py` (optional auto-install of `python-osc`)
- BrainBit process integration via `brainbit_adapter.py`
- optional BrainBit-to-LSL mirroring for LabRecorder
- hardware settings are in `hardware_config.json`
- config payloads are validated before saving
- result payloads are validated before writing
- validation errors now return clear messages to the browser pages
- safe result filenames prevent accidental path traversal through `study_id`

Work still to do:

- broader automated tests instead of only syntax and manual checks
- optional stricter answer-shape validation per card type in the backend
- optional finer-grained BrainBit lifecycle control if studies later need per-session start/stop

## What this plan does not include

- no plugin system
- no heavy framework only for architecture reasons
- no abstract structure without a direct benefit

## How we will know progress is real

- files are small and clearly named
- important terms are explained in the README and docs
- broken input paths produce clearer errors instead of silent success
- new question types can be added by following the step-by-step guide in the README
