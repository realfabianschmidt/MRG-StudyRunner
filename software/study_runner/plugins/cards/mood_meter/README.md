# Mood Meter card

One card, four views, chosen in the card settings (`variant`). All views use
the same word lists and the same two dimensions, **energy** (bottom to top)
and **pleasantness** (left to right).

| View | `variant` | Records | Model |
|---|---|---|---|
| Classic | `classic` (default) | `["Calm", ...]` | Mood Meter of the RULER approach (Brackett et al., 2019) |
| Blobs | `blobs` | `["Calm", ...]` | Mood Meter, shape language of How We Feel |
| Field | `field` | `{"words": [...], "pleasantness": 0.72, "energy": 0.31}` | Affect Grid (Russell, Weiss & Mendelsohn, 1989); shape after Apple's State of Mind |
| Orbit | `orbit` | as field, plus `"intensity": 0.8` | Circumplex model (Russell, 1980), read like the Geneva Emotion Wheel (Scherer, 2005) |

Positions run from 0 to 1, 0.5 is neutral. `intensity` is the distance from
the middle of the wheel (0 middle, 1 rim). The 9-point Affect Grid value is
`1 + 8 * value`.

## Where the words sit

The default lists are Brackett's original 10 x 10 Mood Meter grid, read row
by row with five words per quadrant row: the first red word, "Enraged", is
the top-left corner. `mood-core.js` (`wordCoordinates`) gives every word its
place on the plane from that rule, so all views put a word where the Mood
Meter puts it. Custom lists follow the same row-by-row rule.

## Files

- `card.js` -- the card contract and the editor (view tiles, the "?" help with
  sources). It picks a view and hands it a small context (`state`, `quads`,
  `toggle`, `setPosition`).
- `view-classic.js`, `view-blobs.js`, `view-field.js`, `view-orbit.js` -- one
  view each, with the same interface: `render`, optional `bind`, `onClick`,
  `refresh`, and `records` (`'words'` or `'words+position'`).
- `mood-core.js` -- quadrants, word places, colors, the morphing blob path,
  springs, and the animation loop (kept on the DOM element, stopped on every
  session reset).
- `word-space.js` -- the fullscreen word space used by classic and blobs.
- `plugin.py` -- defaults, `variant` normalization, and the answer rules per view.

No module keeps state of its own: per-session state is in `cardState()`, and
discovery refuses any declared card module with module-level state. Motion
stops for people who ask their device for reduced motion.

## Sources

- Brackett, M. A., Bailey, C. S., Hoffmann, J. D., & Simmons, D. N. (2019). RULER: A theory-driven, systemic approach to social, emotional, and academic learning. *Educational Psychologist, 54*(3), 144-161. https://doi.org/10.1080/00461520.2019.1614447
- Russell, J. A., Weiss, A., & Mendelsohn, G. A. (1989). Affect Grid: A single-item scale of pleasure and arousal. *Journal of Personality and Social Psychology, 57*(3), 493-502. https://doi.org/10.1037/0022-3514.57.3.493
- Russell, J. A. (1980). A circumplex model of affect. *Journal of Personality and Social Psychology, 39*(6), 1161-1178. https://doi.org/10.1037/h0077714
- Scherer, K. R. (2005). What are emotions? And how can they be measured? *Social Science Information, 44*(4), 695-729. https://doi.org/10.1177/0539018405058216
- The Geneva Emotion Wheel: https://www.unige.ch/cisa/gew
- How We Feel, shape language: https://howwefeel.substack.com/p/we-feelgrateful
- Apple, State of Mind: https://support.apple.com/guide/iphone/log-your-state-of-mind-iph6a6decb13/ios
