# Vendored Geist

- Sans: `Geist-Regular.woff2` (400), `Geist-SemiBold.woff2` (600), `Geist-Bold.woff2` (700)
- Mono: `GeistMono-Regular.woff2` (400), `GeistMono-Medium.woff2` (500), `GeistMono-SemiBold.woff2` (600)
- Version: 1.7.2
- Source: https://registry.npmjs.org/geist/-/geist-1.7.2.tgz (`dist/fonts/geist-sans/`, `dist/fonts/geist-mono/`)
- License: SIL Open Font License 1.1, Vercel in collaboration with basement.studio

Why vendored: Geist is the body face for the whole interface. It also stands behind
Materiability in the heading stack, so a checkout whose font files have not been
fetched still renders in a named font instead of falling straight through to Segoe
UI — and Geist is close enough to Materiability that the layout does not shift.

Geist Sans is the body face. Geist Mono is used only where characters have to
line up as values change - URLs, fingerprints, counters, the timeline readouts -
so a digit never changes width mid-update. Headings use Materiability.

Only the weights the UI actually asks for are vendored; the upstream package
also carries italics and a variable axis, and neither is used.

To update: download the new tarball from the URL pattern above, copy the three
`geist-sans` and three `geist-mono` weights plus `LICENSE.txt` into this folder,
and update the version here. `main.css` links to `/static/vendor/geist/…` and needs no change.
