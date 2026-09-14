# Styles

The page stylesheets are loaded through plain `<link>` elements, without a
preprocessor or build step:

- **`main.css`** contains fonts, design tokens, shared controls, form fields,
  card frames, and widgets used on both pages.
- **`study.css`** contains participant layout, branding, navigation, progress,
  and completion styles. `study.html` loads it after `main.css`.
- **`admin.css`** contains the editor, dashboard, settings, session views, and
  view-transition styles. `admin.html` loads it after `main.css`.
- **`ambient-bubbles.css`** styles the animated background used by
  `scripts/shared/ambient-bubbles.js`. Only `study.html` loads it.

## Contents of `main.css`

| Area | What it styles |
|---|---|
| Fonts and design tokens | Materiability/Geist font declarations, colors, spacing, typography, borders, and animation timing |
| Shared controls | Buttons, inputs, labels, switches, and the common card frame |
| Shared card components | Card frame, prompt, instructions, inputs, and reusable switches |
| Shared settings widgets | Plugin selection rows and controls used by the editor and settings pages |

The card registry and shared frame are described in
[`scripts/cards/README.md`](../scripts/cards/README.md). Each card keeps its own
controls next to `card.js` in `plugins/cards/<type>/card.css`.

## Shared components and page-specific behavior

- `.q-card-study`, `.q-type-tag`, `.card-instruction`, `.fi-input`, `.switch`,
  and the related form controls provide the shared card frame. The editor uses
  these building blocks as well as the participant page.
- `.stimulus-toggle-list` and `.stimulus-toggle-row` provide a selection widget
  used by both the Stimulus card and the study-settings sensor/destination
  pickers.
- `.view-sweep` lives in `admin.css` and is driven by
  `scripts/shared/view-transition.js`.
- Responsive and reduced-motion rules stay with the page or card they affect.

## Fonts

`main.css` declares the optional Materiability heading font with a Geist fallback.
See [`fonts/README.md`](../fonts/README.md) for local font setup and licensing.
