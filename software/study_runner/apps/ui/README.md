# Frontend — everything the browser loads

No build step, no bundler, no CDN. These are plain ES modules and plain CSS,
served straight from disk.

```text
apps/ui/
  pages/     the two HTML documents: study.html (participant) and admin.html
  scripts/   the ES modules those two pages load
  styles/    shared, participant, and admin stylesheets
  locales/   en.json and de.json, one flat key set each
  fonts/     the optional Materiability heading font (see fonts/README.md)
  vendor/    third-party assets: Geist and the Iconoir icon set
```

For the shared stylesheet and page styles, see
[`styles/README.md`](styles/README.md). For how a question type ("card")
is built and styled, see
[`scripts/cards/README.md`](scripts/cards/README.md).

## `pages/`

| File | What it does |
|---|---|
| `study.html` | What the participant sees. Just markup — one screen at a time (waiting, questions, done), all behavior loaded from `scripts/participant/study-controller.js`. |
| `admin.html` | The researcher's whole app: hub, editor, dashboard, every settings screen, the session browser. One page with several views shown/hidden by `scripts/admin/admin-controller.js`, not separate pages, so switching views never reloads the browser. |

## `scripts/shared/` — used by more than one page

| File | What it does | In / Out |
|---|---|---|
| `dom-utils.js` | Small helpers every page needs: escaping text for safe HTML, `byId`, and formatting file sizes/dates for people. | text/id in → safe string or DOM node out |
| `api-client.js` | The one `fetch` wrapper: JSON in and out, a request timeout, and a real error (with the HTTP status attached) instead of a silently-empty response. | a URL + payload → parsed JSON or a thrown error |
| `i18n.js` | Loads the active language file and fills in every `data-i18n*` element on the page. `t(key)` gets a translated string from JavaScript. | a language code → translated page text |
| `branding.js` | Fetches the operator's logo manifest and turns a "slot" into an `<img>`, for both the participant slide and the admin hub. | the branding manifest → rendered `<img>` elements |
| `deadline-timer.js` | A countdown that measures real elapsed time, not "ticks" — so a browser tab throttled in the background still reports the correct remaining time the moment it wakes up. | a deadline → tick/deadline callbacks |
| `modal.js` | The one dialog implementation (and the one yes/no confirm dialog) every other script reuses, so every popup looks and behaves the same. | a title/body → an open/close dialog |
| `view-transition.js` | The full-screen sweep animation between two admin views, so a view swap never shows a half-built page mid-load. Admin-only, despite living in `shared/`. | a view-change function → the same result, played behind a wipe animation |
| `qr-code.js` | Draws a QR code as inline SVG (no library, no internet) for the admin/participant links and the tablet certificate setup. | text → an SVG string |
| `reliable-event-queue.js` | Queues a study event locally before sending it, so a page reload or dropped connection can retry it without recording it twice on the server. | an event → guaranteed-once delivery |
| `plugin-catalog.js` | Loads and caches the installed-plugin catalog; loads manifest-declared UI modules and stylesheets with bounded waits and generation-aware caches. | `/api/plugins/catalog` → the catalog + lazily-loaded plugin UI assets |
| `participant-plugin-extensions.js` | Runs every plugin's optional participant-side script with a timeout, so a slow or broken plugin extension can never delay a stimulus or a submission. | plugin catalog → isolated lifecycle calls into each plugin's script |
| `study-settings.js` | The one client-side definition of what a study's settings look like (must mirror the backend's validator exactly, checked by a test). | plugin catalog → defaults and shape for a study's settings object |
| `settings-page.js` | Shared building blocks for a settings screen: a setup-step indicator, a "test connection" result box, and busy-button behavior. | a status/result object → rendered feedback |
| `settings-shell.js` | The shared "left nav, right panel" behavior behind both settings areas (machine settings and study settings): exactly one entry active, exactly one panel visible. | a list of nav entries → rendered nav + active-panel switching |
| `timeline-view-model.js` | Pure math: turns a recording's LSL stream headers into drawable timeline tracks. No DOM, so it is tested directly without a browser. | stream headers → track layout data |
| `finalization-view-model.js` | Pure logic for the "finish saving a session" job: progress percentage, which step needs attention. | a finalization job → progress/status summary |
| `ambient-bubbles.js` | The self-contained moving background blobs on the participant waiting screen. Imports nothing from the rest of the app on purpose. | a container element → animated background, until stopped |

## `scripts/cards/`

See [`scripts/cards/README.md`](scripts/cards/README.md) — the shared card
registry (`index.js`) and the shared card frame (`card-info.js`).

## `scripts/participant/`

| File | What it does | In / Out |
|---|---|---|
| `study-controller.js` | The whole participant experience: loads the study config, steps through cards, submits answers, handles the preview mode (`/?preview=1`, which drops every write to the server). The largest file in this folder. | study config + participant input → rendered questions + submitted results |
| `study-client-heartbeat.js` | Tells the admin dashboard "a tablet is here" every couple of seconds, with a stable per-browser client ID. | — → periodic `/api/study-client/heartbeat` calls |

## `scripts/admin/`

| File | What it does | In / Out |
|---|---|---|
| `admin-controller.js` | The study editor and the whole admin page shell: the question sidebar, drag-to-reorder, the editor overlay, saving the study. The largest file in the whole frontend. | study config ↔ the editor UI |
| `admin-dashboard-controller.js` | The live "Biosignal Dashboard": sensor tiles, plugin controls, study-client status, polled every couple of seconds. | `/api/admin/status` → rendered live tiles |
| `sessions-browser.js` | The completed-sessions list and the full session detail view (answers, files, withdrawal). | session data → the hub list + detail page |
| `session-timeline.js` | Draws the session detail view's timeline: one row per recorded signal, answer markers on top, as inline SVG. Reads `shared/timeline-view-model.js` for what to draw. | a session's recorded streams → an SVG timeline |
| `upload-monitor.js` | The persistent "finish saving a session" monitor: polling, retry, and confirming a partially-failed save. Delegates the actual rendering to `finalization-monitor-view.js`. | finalization jobs → the monitor widget + its actions |
| `finalization-monitor-view.js` | Pure rendering for one finalization job (used by `upload-monitor.js`); no polling or network calls of its own. | one job → its HTML |
| `recovery-panel.js` | The hub banner for a session orphaned by a crash: finalize it normally, or discard it. | crash-recovery candidates → the banner + its two actions |
| `plugin-console.js` | The live diagnostics console for one plugin: a status snapshot plus a line-by-line event stream, gated behind a confirmed operator unlock. | a plugin key → an open console dialog |

## `scripts/settings/`

| File | What it does |
|---|---|
| `machine/machine-settings-panel.js` | The gear-icon settings hub: everything that belongs to *this computer* (never travels with a study) — sensor timeouts, the certificate, the update panel, the desktop shortcut, plus one generated panel per installed plugin. |
| `machine/branding-settings-controller.js` | The branding panel inside the machine hub: upload/remove the group logo and funder logos. |
| `machine/certificate-settings-controller.js` | The certificate panel: setup steps, status, the tablet-pairing QR code, and moving the certificate to another computer. |
| `study/study-settings-panel.js` | The per-study settings shell, reachable only from the study editor: sensors, participant experience, data destinations, and the exported `.study-runner` file. Everything here is saved with the study and travels with it. |

## `locales/`

`en.json` and `de.json` — one flat `key: "text"` map each, read by
`shared/i18n.js`. A test fails the build if the two files' key sets ever
differ, so a translation can go stale but never go missing.

## `fonts/` and `vendor/`

- `fonts/` — empty by default; see [`fonts/README.md`](fonts/README.md) for
  adding the optional Materiability heading font under your own license.
- `vendor/` — third-party, unmodified: Geist (the default font, SIL license)
  and Iconoir (the icon set used throughout, via CSS classes like
  `iconoir-check`).

## Rules that are tested, not just intended

- **The URL prefix is `/static/`, not the folder name.** `static_folder` is set
  once in `apps/server/__init__.py`; renaming this folder does not move a
  single `href`.
- **Nothing loads from a CDN.** `test_pages_do_not_load_from_cdns` fails the
  build if it does.
- **Both locales carry the same keys.** A key added to one and not the other is
  a test failure, not a missing translation at runtime.
- **No core script may name a plugin.** Plugin-specific interface comes from the
  plugin's own `ui/*.js`, declared in its manifest and served through
  `/api/plugins/<key>/assets/…`. `test_web_ui.py` guards this.
- **Pure logic goes in a view model.** `shared/timeline-view-model.js` and
  `shared/finalization-view-model.js` hold no DOM and are tested directly with
  `node --test`; the modules that draw them stay thin.
