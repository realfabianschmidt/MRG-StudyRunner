# Materiability

- `Materiability-Regular.ttf` (400), `Materiability-SemiBold.ttf` (600),
  `Materiability-Bold.ttf` (700)
- Owner: Materiability Research Group
- License: first-party. These files are part of this repository and are covered by the
  proprietary grant in `LICENSE`. They are **not** third-party components, so they are
  deliberately absent from `THIRD_PARTY_NOTICES.md`.

Materiability is the heading face for the whole interface — every `h1`–`h6`, the screen and
card titles, the sidebar logo, the section labels. Body text is Geist
(`../vendor/geist/`), and tabular values are Geist Mono. `main.css` declares the three
`@font-face` rules against `/static/fonts/…` and needs no change when a weight is replaced,
only when one is added.

## Why this folder is a special case in the release build

`release_tools/build_source_release.py` lists `.ttf` in `FORBIDDEN_SOURCE_SUFFIXES`, so no
font ships by default and a stray face can never ride along unnoticed. A folder is exempted
only when it documents its own terms — this README is that document for Materiability, and
`../vendor/geist/LICENSE` is the equivalent for Geist. Both directories are named in
`LICENSED_FONT_DIRECTORIES`.

If you add a weight here, nothing else needs updating. If you add a font from someone else,
it belongs in `../vendor/` with its upstream licence text and an entry in
`THIRD_PARTY_NOTICES.md`, not in this folder.
