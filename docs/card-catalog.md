# Study Card Catalog

This is the operator-facing reference for every card type shipped with Study
Runner. The editor always keeps `participant-id` first and `finish` last.
For answer cards, **Required** blocks Next/Submit until a real interaction has
produced a valid answer; an optional untouched card is omitted from `answers`.
Information, stimulus, participant-ID, and finish cards do not create a normal
`qN` answer.

| Type | Purpose | Main configuration | Recorded answer | Minimal example |
| --- | --- | --- | --- | --- |
| `participant-id` | Identifies the participant and captures permitted participant metadata. | Enabled fields, formatting, and which fields form the ID. | Stored as `participant_id` plus `participant_metadata`, not as `qN`. Always required before a session starts. | `{"type":"participant-id"}` |
| `info` | Shows instructions, context, or a transition page. | Title/text, optional image, image position. | None; it is always navigable. | `{"type":"info","title":"Welcome","text":"Please read the instructions."}` |
| `text` | Collects a free-text response. | Prompt, single/multiline presentation, note/instructions. | String, for example `"I felt calm"`. | `{"type":"text","prompt":"What did you notice?","required":true}` |
| `choice` | Allows several options to be selected. | Prompt and option labels. | Array of selected strings, for example `["Blue","Green"]`. | `{"type":"choice","prompt":"Select all that apply","options":["Blue","Green"]}` |
| `single` | Allows exactly one option to be selected. | Prompt and option labels. | Selected string, for example `"Blue"`. | `{"type":"single","prompt":"Choose one","options":["Blue","Green"]}` |
| `likert` | Captures one labeled ordinal rating. | Prompt, scale size, endpoint labels. | Integer scale position, for example `4`. | `{"type":"likert","prompt":"I felt focused","scale":5}` |
| `slider` | Captures one continuous-looking numeric rating. | Prompt, minimum/maximum, labels and starting value. | Integer value. The visible default is not an answer until the participant moves the slider. | `{"type":"slider","prompt":"How alert are you?","min":0,"max":100}` |
| `multi-slider` | Captures several related numeric ratings. | Prompt and named dimensions, each with its scale labels. | Object keyed by dimension label, for example `{"Calm":72,"Alert":55}`. Every dimension must be touched when required. | `{"type":"multi-slider","prompt":"Rate each state","dimensions":[{"label":"Calm"}]}` |
| `semantic` | Rates one or more opposing adjective pairs. | Prompt, adjective pairs, number of positions. | Object keyed as `left_right`, for example `{"Tense_Relaxed":6}`. Every pair needs a selection when required. | `{"type":"semantic","prompt":"How was the task?","pairs":[["Tense","Relaxed"]]}` |
| `ranking` | Orders a list from first to last. | Prompt and items. | Array in the participant's final order. The initial display order is not an answer until it is changed. | `{"type":"ranking","prompt":"Order by preference","items":["A","B","C"]}` |
| `word-cloud` | Selects one or more words from a visual cloud. | Prompt, words and optional display weights. | Array of selected words. | `{"type":"word-cloud","prompt":"Which words fit?","words":["Calm","Curious"]}` |
| `mood-meter` | Records affect using the classic quadrants, blobs, field, or orbit view. | Prompt, `variant`, words and selection limits. | `classic`/`blobs`: word array. `field`: `{words, pleasantness, energy}`. `orbit` also adds `intensity`; position values are 0–1. | `{"type":"mood-meter","prompt":"How do you feel?","variant":"classic"}` |
| `affect-map` | Places a feeling in a field or orbit, with optional region colors. | Prompt, `field`/`orbit`, word lists, selection limit, four region colors and names (an empty name leaves its region without a caption once any region is named), and show-colors switch. | `{words, pleasantness, energy}`; Orbit also adds `intensity`. All positions are 0–1; colors are never recorded. | `{"type":"affect-map","prompt":"Where are you now?","variant":"field"}` |
| `stimulus` | Shows timed media/code and starts/stops the actuators it selected; sensors only get markers. | Title, warm-up and duration (a minimum when auto-advance is off), trigger type/content, actuators, plugin settings, end sound, auto-advance, overtime (stimulus keeps running, actuators keep running, maximum). | No answer; timing, markers and overtime (`time_up_at`, `overtime_ms`) are stored in `card_events`. | `{"type":"stimulus","title":"Rest","trigger_type":"timer","duration_ms":30000}` |
| `finish` | Shows the post-submission closing page. | Closing title/text and optional media. | None; submission occurs before this card is shown. | `{"type":"finish","title":"Thank you"}` |

## Notes for study authors

- Use `single`, not a one-option `choice`, when exactly one answer is required.
- Required sliders and rankings deliberately require interaction; a plausible
  default must never be recorded silently.
- Keep hardware disabled in portable templates. Enable a sensor only after the
  dashboard confirms the connected device and the readiness check is green.
- Mood Meter scientific background and the precise variant-specific shapes are
  documented in its plugin README; hardware plugins keep their own deeper setup
  and acceptance instructions.
