# Plan for clearer code

This plan describes how Study Runner can become clearer, safer, and easier to extend step by step.

Important: this is not a plugin architecture plan. The project should stay simple.

## Current status

Status as of April 2026:

- Step 1 is done. Names, structure, and docs are clean and up to date.
- Step 2 is done. Backend is split into small focused modules.
- Step 3 is done. The frontend is fully separated into HTML, CSS, and JavaScript.
  Each question type is a self-contained card module. All styles are in one central
  CSS file with the Materiability font. The admin page has a live preview layout.
  A Stimulus card type was added, enabling timed waiting phases with optional media
  and code triggers anywhere in the study flow.
- Step 4 is partially done. Hardware adapters are in place. Validation is still open.

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
- README updated with a glossary, file explanations, and a step-by-step guide for new types
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
  trial_service.py   Keeps study start/stop logic in one place
```

## Step 3: Make the frontend easier to read

**Status: Done**

Goal:
The frontend should be easy to navigate, extend, and read.

Work done:

- all styles in one central file `static/css/main.css`, including the Materiability font
- old `admin.css` and `study.css` removed
- each question type is a self-contained module in `static/js/cards/`
- `cards/index.js` is the central registry for all question types
- admin page has a two-column layout: left sidebar for editing, right area for live preview
- study page shows questions as individual cards
- old renderer and template files removed

Current file structure:

```text
static/
  css/
    main.css          All styles in one place
  fonts/
    Materiability-Regular.ttf
    Materiability-SemiBold.ttf
    Materiability-Bold.ttf
  js/
    api-client.js     Shared browser helper for API requests
    study-controller.js  Study page: flow, timer, answer submission
    admin-controller.js  Admin page: sidebar, preview, save
    cards/
      index.js        Central registry for all card types
      card-likert.js
      card-semantic.js
      card-choice.js
      card-slider.js
      card-ranking.js
      card-text.js
      card-stimulus.js
```

## Step 4: Improve validation and hardware integration

**Status: Partially done**

Goal:
Errors should be caught earlier, and external tools should be connected more cleanly.

Work done:

- Hardware integrations moved into small adapter files in `app/integrations/`
- LSL event markers via `lsl_adapter.py` (requires pylsl, optional)
- OSC messages to TouchDesigner via `osc_adapter.py` (python-osc, already installed)
- Hardware settings are in `hardware_config.json` — researchers enable integrations
  without touching code
- JS trigger type in stimulus cards lets snippets call Flask API endpoints directly
  via the `study` helper

Work still to do:

- validate config data on the server before saving it
- validate result data on the server before writing it
- return clearer error messages to the browser pages
- BrainBit SDK adapter (add `app/integrations/brainbit_adapter.py` when SDK is available)

Current integration structure:

```text
app/
  integrations/
    __init__.py             Package with usage notes for adding new adapters
    lsl_adapter.py          LSL event markers (optional: pip install pylsl)
    osc_adapter.py          OSC messages to TouchDesigner
hardware_config.json        Researcher-editable settings, read at server startup
```

Benefit:

- each integration is one small, focused file
- adding a new adapter follows the same clear pattern
- the server always starts cleanly even if a library is not installed

## What this plan does not include

- no plugin system
- no heavy framework only for architecture reasons
- no abstract structure without a direct benefit

## How we will know progress is real

- files are small and clearly named
- important terms are explained in the README and docs
- broken input paths produce clearer errors
- new question types can be added by following the step-by-step guide in the README
