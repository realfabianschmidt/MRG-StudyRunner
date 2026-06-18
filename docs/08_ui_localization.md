# UI Text And Languages

The browser UI (admin page and participant page) is localizable. English is the default
language and German is available through a small switcher in the admin header. The choice
is remembered per browser.

This only covers the app's own labels and buttons. Study content you author (questions,
stimulus text) keeps whatever language you typed.

## Where the text lives

- `software/study_runner/web/locales/en.json` — English text (the default).
- `software/study_runner/web/locales/de.json` — German text.
- `software/study_runner/web/scripts/i18n.js` — the small helper that applies the text.

Both locale files must contain the **same keys**. A key is a short name like
`hub.newStudy` that maps to the visible text.

## Add or change a label

1. Open `en.json` and `de.json`.
2. Add the same key to both files with the English and German text.
3. Use the key in the page or in code:
   - In HTML, mark the element:
     `<span data-i18n="hub.newStudy">New study</span>`
     (the text between the tags is the English fallback).
     Other attributes: `data-i18n-placeholder`, `data-i18n-title`, `data-i18n-aria-label`.
   - In JavaScript, call `t('hub.newStudy')` (import `t` from `./i18n.js`).

## Rules

- Keep English as the fallback text directly in the HTML, so the page still reads
  correctly even if a locale file fails to load.
- Always add a key to **both** locale files. A quick check that the keys match and that
  no used key is missing runs as part of the normal review.
- Add a new language by creating another `<code>.json` file with the same keys and adding
  the code to `SUPPORTED_LANGUAGES` in `i18n.js`.
