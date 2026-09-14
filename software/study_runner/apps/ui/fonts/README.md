# Heading font

This folder is empty by default. Headings are set in Geist
(`../vendor/geist/`, SIL Open Font License 1.1) — the same face used for
body text — because `main.css`'s heading font stack already falls back to
it whenever a face named `Materiability` is not present:

```css
--font-heading: 'Materiability', 'Geist', -apple-system, ...
```

## Adding Materiability locally

Materiability was this project's original heading face. Its rights belong
to the Materiability Research Group, not to this repository, so it does not
ship here by default. If you have your own permission to use it, drop these
three files into this folder and nothing else needs to change — the
`@font-face` rules in `main.css` already point at them:

- `Materiability-Regular.ttf` (weight 400)
- `Materiability-SemiBold.ttf` (weight 600)
- `Materiability-Bold.ttf` (weight 700)

Do this only with your own rights to the font; do not commit it to a public
fork of this repository unless your permission covers that too.

## Why this folder is exempt from the release build

`release_tools/build_source_release.py` forbids `.ttf` files by default, so
a stray font can never ship unnoticed. A folder is exempted only when it
documents its own terms — this README is that document. If you add a font
from someone else, it belongs in `../vendor/` with its own license text and
an entry in `THIRD_PARTY_NOTICES.md`, not in this folder.
