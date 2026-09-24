# Cards

Three files. None of them draws a question itself — a card type's own drawing
code lives in its extension (`plugins/cards/<type>/card.js`), loaded
through the plugin catalog. This folder only holds what every card shares.

- **`index.js`** — the registry. Reads the installed plugin catalog, finds
  every extension that declares a `card_contract`, and loads its `card.js` on
  demand the first time that question type is actually needed.
  - **In:** the plugin catalog (`/api/plugins/catalog`).
  - **Out:** `CARDS` (type → loaded module) and `CARD_TYPES` (the ordered list
    the editor's "add question" picker shows).
  - **Responsibility:** knowing *that* a card type exists and *where* to load
    it from — never what it looks like or how it behaves.
- **`card-info.js`** — the shared frame every card is composed into: the
  question-text field, the optional instruction/note text, the "required"
  toggle, and, on the participant side, the type tag and optional-tag badge.
  Card modules use these helpers for a consistent frame in the editor and
  participant page.
- **`session-state.js`** — the only place a card may keep state the DOM does
  not hold. `cardState(owner, index, init)` returns this session's state for
  one card; `onSessionReset(fn)` registers cleanup such as closing an overlay.
  The participant page calls `resetAllCardState()` whenever it builds the
  questions and when a session ends, and reloads itself before the next
  participant.

## No data between sessions

A card never keeps participant data in a module-level variable. It reads its
answer back from the rendered DOM, or stores it with `cardState()`. Plugin
discovery refuses a `card.js` with mutable module-level state
(`plugin_framework/card_session_isolation.py`); `export let defaultQuestion`,
set by `configureCard()`, is the one allowed exception.

## Shared container and styles

Cards render inside the `.q-card-study` tile in
[`main.css`](../../styles/main.css), using the shared building blocks from
`card-info.js`: `.q-type-tag`, `.card-instruction`, `.fi-input`, `.fi-textarea`,
`.switch`, and `.editor-toggle`. Card-specific controls live in `card.css`
beside the card module.

| Card | Controls styled in its `card.css` |
|---|---|
| Likert | Scale choices |
| Semantic differential | Paired scale labels and choices |
| Choice and single choice | Selectable chips |
| Slider | VAS slider |
| Ranking | Ordered, draggable choices |
| Stimulus | Centered content, trigger controls, and stimulus phases |
| Mood Meter | Quadrant overview, fullscreen bubbles, and editor controls |
| Multi-Slider | Per-dimension sliders and editor rows |
| Word Cloud | Draggable words and the drop tray |
| Participant-ID | Identity fields and their editor controls |

The `.stimulus-toggle-list` and `.stimulus-toggle-row` widget is also used by the
study-settings sensor and destination pickers. Its styles are shared across
those consumers; see the [stylesheet overview](../../styles/README.md).

## Asset loading

A card's manifest declares its JavaScript entry in `ui.extensions.card` and its
JavaScript and CSS files in `ui.assets`. The catalog loader fetches them through
`/api/plugins/<key>/assets/...`. The card registry waits for the module,
Python-provided defaults, and every declared stylesheet before registering its
question types. A failed stylesheet can be retried; required cards that cannot
load are reported as unavailable.
