# Study Runner Roadmap 0.5 — Planning Document

Status: **draft for contribution** · Created 2026-07-30 · Authors: Fabian (owner), Claude Fable 5 (research + design), GPT 5.6 Sol (review + input, pending)

Baseline: version **0.4.0** — local checkout `main` == `origin/main` == commit `56b6a28` == latest published GitHub release `app-v0.4.0` (2026-07-12). All paths below are relative to the repository root (`Software/`).

---

## How to use this document

- Each topic (T1–T9) has: **Current state** (verified facts with file references), **Approach** (the decided design), **Work items** (with size, risk, and implementer tier), **Open questions** (marked ❓), and a **GPT 5.6 Sol input** block.
- **GPT 5.6 Sol:** please write your contributions ONLY inside the `> GPT 5.6 Sol:` blocks (challenge the design, add missed risks, propose alternatives). Do not rewrite the decided sections — decisions in the Decision Log are settled unless Fabian reopens them.
- Implementer tiers for later execution:
  - **hard** → strongest model (Fable 5): architectural, subtle, or touching the most protected code paths.
  - **medium** → Sonnet 5 / GPT: well-scoped features with clear patterns to follow.
  - **easy** → any model: mechanical, low-risk.
- Sizes: **S** (hours), **M** (a day-ish), **L** (multiple days / needs iteration).

## Decision log (Fabian, 2026-07-30)

1. Document language: **English** (repo standard).
2. Nextcloud upload: **public share link** mode ("freigegebener Ordner").
3. Network reality: **the server machine always has internet.** The tablet sits in the same WLAN and only talks to the server. (Earlier "fully offline lab" assumption is obsolete; the tablet-side no-CDN rule stays — the participant page must never depend on the internet.)
4. The laptop where BrainBit failed ran the **packaged .exe** from GitHub.
5. Certificates (revised 2026-07-30, supersedes two earlier positions): **keep the local root CA and make it effortless** — a Certificate settings page inside Study Runner, built exactly like the Notion/Nextcloud settings pages, with a QR code whose only job is the tablet download, plus CA export/import so a replacement server computer reuses the CA the tablet already trusts. No separate guide page. Let's Encrypt with automatic DNS is explicitly **not** pursued: a 90-day certificate needs automation that can fail mid-study, while the local CA is valid for 10 years. See T8 for the full rationale.
6. Standing requirements for every topic: reuse existing layouts/styles instead of inventing new ones, keep it understandable for non-coders and coders, and make everything work on Windows and macOS alike. Written out as the three rules below.
7. No video is ever recorded — camera frames are analyzed for emotion and discarded (verified in code). What gets mirrored to Nextcloud is the local session folder (JSON + XDF).

## Ground rules for every implementer

These are enforced by guard tests — breaking them fails CI:

| Rule | Enforced by |
|---|---|
| Any route added/removed/renamed → update `EXPECTED_ROUTES` in the same commit | `software/tests/test_route_inventory.py` |
| Every new/moved `.py`/`.js` file → row in `docs/file-guide.md` | `software/tests/test_file_guide.py` |
| Every UI string → identical keys in `web/locales/en.json` AND `de.json` | `software/tests/test_web_ui.py` |
| Slide toggles only, never checkbox rows; no `alert()` in `study-controller.js`; no CDN URLs | `software/tests/test_web_ui.py` |
| Updater wire format is byte-frozen — never touch `update_crypto.py` payload shape | `test_route_inventory.py::UpdaterWireFormatTests` |

Additional conventions: plain-language operator messages (technical detail goes to logs only); atomic writes (`backend/services/atomic_io.py`) for anything a crash could corrupt; new background threads must respect a new `STUDY_RUNNER_DISABLE_BACKGROUND=1` flag (mirroring `STUDY_RUNNER_DISABLE_HARDWARE`) so tests stay deterministic.

### Three rules that apply to every topic, always

These are Fabian's standing requirements (2026-07-30). They are not per-topic wishes — check every work item against them.

1. **Reuse, never re-invent.** A new settings page copies the existing settings-page shell, not a new layout. A new status display uses the existing status-card and step-list markup. A new dialog uses the shared modal. A new toggle uses `.switch`. If a pattern exists, extend it; if a pattern is nearly right, generalize it once (see T2-A) and then reuse it everywhere. Three pages that look and behave the same are worth more than three clever pages.
2. **Understandable for non-coders and coders alike.** The operator UI never asks anyone to understand certificates, ACME, WebDAV, PEM files, or JSON. The code, file names, and folder structure stay obvious enough that a non-specialist can find their way, and every file keeps its one-line entry in `docs/file-guide.md`.
3. **Windows and macOS are equals, every time.** Every feature must work on both, in a source checkout *and* in a packaged build. Avoid OS-specific mechanisms wherever a portable one exists: prefer browser download/upload over native file pickers, stdlib over shelling out, forward-slash-safe path handling via `pathlib`. Where a difference is unavoidable, handle both explicitly and say so in the plan. T1 is the cautionary tale: BrainBit was broken in every packaged release because packaging was never verified.

---

## T1 — BrainBit works in packaged releases (bug fix)

**In plain terms:** the connector could not start on the laptop because the installed package literally does not contain it. This is a packaging gap, not a driver problem.

### Current state (root cause, verified)

- Packaged releases (exe/zip) **do not ship** `software/study_runner/integrations/brainbit/brainbit_realtime_cli.py`: it is not in the PyInstaller `datas` (`release_tools/pyinstaller/study_runner_server_common.py`), and the adapter launches it as a *file path with a separate Python interpreter* — `_default_python_executable()` (`brainbit/adapter.py`) deliberately returns `""` when frozen → status `not_configured`, silent no-op. Verified empirically: no brainbit/neurosdk files in a local `dist/` build.
- Second trap: the committed `software/study_content/settings/hardware_settings.json` pins the dev machine's headset (serial `04030072`, MAC `DA:AC:A1:99:7B:BA`). The CLI resolves serial → address → name → index and **exits (code 4) instead of falling back** when the pinned device is absent.
- The watchdog (`adapter.py`, section 6) only detects *silence* and ends its thread when the child has exited — it never restarts a fast-exiting CLI.
- The CLI runs `pip install` at **import time** (ignores `STUDY_RUNNER_DISABLE_RUNTIME_PIP`).
- Packaged builds use `console=False`, so every `print()` diagnostic vanishes; only `integrations/brainbit/logs/brainbit_runtime.log` and `brainbit_state.json` survive.
- Precedent for the fix: the DeepFace emotion worker re-invokes the frozen exe via the `--emotion-worker` dispatch in `software/server.py`.

### Approach

1. **`--brainbit-cli` dispatch:** add to `software/server.py` (mirror `--emotion-worker`). Refactor `brainbit_realtime_cli.py` to expose `main(argv)`; move the pip self-install from import time into `main()`, gated OFF when frozen (bundled deps) and by `STUDY_RUNNER_DISABLE_RUNTIME_PIP`.
2. **Adapter command building:** new `_build_cli_command(config)` — frozen: `[sys.executable, "--brainbit-cli", *args]`; source mode: unchanged `[python, script_path, *args]`. `initialize()` stops requiring a script path / interpreter when frozen.
3. **PyInstaller bundling:** hiddenimports + `collect_submodules` + `collect_dynamic_libs` for `neurosdk` and `em_st_artifacts`; pin `pyneurosdk2` and `pyem-st-artifacts` in `software/requirements.txt` and the build requirements. ❓ exact importable module names must be verified against the installed wheels during implementation.
4. **Device pin becomes optional:** if no device is pinned → scan and connect to the first/only BrainBit found. If a pinned device is *not* found → fall back to scanning and emit a machine-parsable `STATUS pinned_device_missing` line that the adapter turns into a dashboard notice. Remove the committed pin from `hardware_settings.json` (fields stay, empty).
5. **Watchdog fast-exit fix:** on child exit, record exit code + restart with backoff (5 s / 15 s / 60 s; give up after 3 fast exits within 2 min → state `error`). Map exit codes to plain-language i18n keys (`brainbit.error.device_not_found`, `.crashed`, `.driver_missing`, `.restarting`), rendered by the dashboard.

Tests: new `software/tests/test_brainbit_command.py` (command building under fake `sys.frozen`; watchdog backoff state machine as pure functions). Final proof needs a real packaged build + physical headset.

### Work items

| Item | Size | Risk | Tier |
|---|---|---|---|
| CLI refactor (`main()`, pip gating, scan fallback) | M | must not change OSC/stdout output format (TouchDesigner + adapter parse it) | hard |
| Adapter command building + init relaxation | M | status regressions in source mode | hard |
| PyInstaller hiddenimports/binaries + version pins | S | only verifiable with real frozen build + device | medium |
| Watchdog backoff + exit-code mapping | M | restart loops if backoff logic is wrong | medium |
| Remove committed device pin, dashboard notice, i18n | S | low | easy |

> **GPT 5.6 Sol input:**
> **Backend implemented (2026-07-30):** the packaged executable can dispatch the bundled BrainBit CLI itself, required SDK modules/native libraries are included in PyInstaller, and runtime pip installation is disabled in frozen mode. Empty device pins now auto-select a discovered headset; a missing saved headset falls back with an explicit status instead of exiting. Fast exits and data silence use bounded restart/backoff state with plain-language error codes, and log/state paths are resolved to writable runtime locations. Command construction, dependency gating, fallback selection, watchdog limits, and frozen-path behavior have regression coverage. Remaining acceptance gate: build the real Windows and macOS packages and smoke-test them with a physical BrainBit.

---

## T2 + T3 — Structure, naming, and code quality (incremental, no big-bang)

**In plain terms:** tidy the house room by room while people live in it — shared helpers first, then splitting the biggest files, cosmetic renames last. Never a rewrite.

### Current state (measured)

- God files: `brainbit/adapter.py` 1173 lines (6 banner-marked sections — seams drawn, not cut), `services/validation.py` 1063, `local_emotion_worker/plugin.py` 809, `notion_upload/adapter.py` 762, `mr60_mini_radar/adapter.py` 744, `web/scripts/study-controller.js` 1716 (84 functions), `admin-controller.js` 1315, `web/styles/main.css` 3084.
- Duplication: `escapeHtml` defined in **17** JS files; `_timestamp`/`_set_state`/`_config_section` re-implemented per adapter; packaged-mode detection exists 3×; two `requirements.txt` with divergent numpy pins.
- Naming: integration folder ≠ plugin key in 7 of 8 integrations (e.g. `local_emotion_worker` → key `emotion_worker`; two plugins share `config_key camera_emotion`); kebab-case `.py` scripts in `release_tools/`; flat `backend/services/` (15 files) and flat `web/scripts/`.

### Approach — three sub-phases

**A. Shared utilities (FIRST — unblocks T1/T4/T6):**
- `integrations/adapter_utils.py`: shared `timestamp()`, `set_state()`, `config_section()`.
- One `is_frozen()` / packaged-mode detector in `backend/services/runtime_config.py`; delete the three copies.
- `web/scripts/lib/dom-utils.js`: one exported `escapeHtml` (ES modules are already in use — pure import swap in 17 files).
- `web/scripts/lib/modal.js`: **shared modal component** wrapping the existing `.modal-backdrop`/`.settings-modal` CSS (open/close, Escape, backdrop click, focus handling, and a minimize hook — T4's mini widget needs it). Consolidate duplicated modal CSS in `main.css`.
- **Generalize the settings-page building blocks** (this is what makes rule 1 possible for T6 and T8). The Notion settings page already contains every pattern a second and third settings page needs, but under Notion-specific class names in `web/pages/admin.html` and `main.css`:

  | Today (Notion-specific) | Becomes (reusable) | What it is |
  |---|---|---|
  | `.notion-settings-page` + `.dashboard-hero` + `.dashboard-grid` | `.settings-page` shell | kicker, title, subtitle, "Back to hub" button, card grid |
  | `.notion-status-grid` / `.notion-status-card` | `.status-grid` / `.status-card` | label + value + hint tiles |
  | `.notion-help-step` / `.notion-help-step-num` / `.notion-step-state` | `.setup-step` / `.setup-step-num` / `.setup-step-state` | numbered setup guide with a per-step state marker |
  | `notion-settings-controller.js` | keep, but extract the shared load/save/test/status-poll skeleton into `web/scripts/lib/settings-page.js` | one page controller pattern for Notion, Nextcloud, Certificate |

  Keep the old class names as aliases in CSS during the rename so the Notion page cannot regress, and cover the shell with a `test_web_ui` assertion that all three settings pages use the shared classes.
- Align numpy pins across the two requirements files.

**B. File splits (after the features that touch them):**
- `brainbit/adapter.py` → package-internal `process_control.py` / `line_parser.py` / `history.py` / `watchdog.py` with `adapter.py` as a stable facade (plugin.py untouched). **After T1.**
- `mr60_mini_radar/adapter.py` → `serial_link.py` / `ble_link.py` / `frame_decoder.py` + facade.
- `local_emotion_worker/plugin.py` → `runtime_installer.py` / `asset_downloader.py` / `worker_monitor.py`.
- `routes/helpers.py`: session handling moves to `services/session_store.py` **as part of T7** (T7 rewrites that code anyway).
- `study-controller.js` → extract `web/scripts/study/answer-state.js` + `submit-flow.js`; keep the filename `study-controller.js` (guard test greps it). **After T9.**
- New subfolders (`lib/`, `study/`, `admin/`) for NEW files only — no mass moves of existing files in 0.5 (HTML script-tag + file-guide churn for near-zero user value).
- Caution for all adapter splits: these modules hold module-level state — keep state in exactly one module per package.

**C. Cosmetics (LAST):**
- Do **not** rename integration folders. Instead add `software/tests/test_plugin_registry.py` asserting the documented folder → key → config_key mapping (freezes today's mismatches deliberately, catches new accidents) and document the mapping table in `docs/file-guide.md`.
- Rename kebab-case `release_tools/*.py` to snake_case (scripts, not imports; update `release.ps1` + `release_tools/release-study-runner.mjs` + CI workflow references).

### Work items

| Item | Size | Risk | Tier |
|---|---|---|---|
| `adapter_utils` + single frozen-detection | S | subtle mode-detection behavior differences | medium |
| `dom-utils.js` escapeHtml dedupe (17 files) | S | missed call sites | easy |
| Shared modal component + CSS consolidation | M | regressions in existing settings/QR modals | medium |
| Generalize settings-page shell/status-card/setup-step + `lib/settings-page.js` | M | Notion page must look and behave exactly as before | medium |
| brainbit split (facade) | M | import cycles, module-level state | hard |
| mr60 + emotion-worker splits (facades) | M each | same, less entangled | medium |
| study-controller extraction | M | participant-flow regressions | medium |
| registry mapping test, script renames, pin alignment | S | low | easy |

> **GPT 5.6 Sol input:**
> **Implemented (2026-07-30):**
> - T2-A backend utilities: added the state-free `adapter_utils.py` for config lookup, locked state updates, and consistent timestamps; migrated all plugin config helpers plus the MR60/camera state implementations. Packaged-mode decisions now use the single `runtime_config.is_frozen()` / `get_app_mode()` source, including the Local Emotion Worker. Focused regression tests cover fallback lookup, state mutation, timestamps, and frozen behavior.
> - T2-C compatibility cleanup: documented and guard-tested the exact integration folder → plugin key → config key mapping, including the intentional shared `camera_emotion` config. Renamed all top-level `release_tools/*.py` scripts to snake_case and updated CI, release automation, docs, runtime messages, and maintainer commands; a guard rejects new kebab-case Python scripts. Aligned both runtime numpy requirements to `numpy>=1.26,<2.0`. The updater payload wire format was not changed.
> - T2-B file splits remain deliberately pending until their prerequisite feature work is complete.

> **Claude Sonnet 5 (UI implementation, 2026-07-30):**
> **T2-A frontend closed out:** the `escapeHtml` dedupe was actually still pending in all 17 files despite the shared `lib/dom-utils.js` existing — every local copy (verified byte-identical logic first) is now a plain import from there, including `card-finish.js`'s public re-export for backward compatibility. The Notion settings page's `<section>` still used `.notion-settings-page` instead of the generalized `.settings-page` shell; switched it over and deleted the now-redundant duplicate CSS rule (`.notion-settings-page .dashboard-card .field` was byte-identical to `.settings-page`'s). Verified with `node --check` on every touched file, a static import/export cross-check across all 31 web/scripts files (123 named imports, zero unresolved), the full pytest suite, and a real browser smoke test.

---

## T4 — Instant submit, background uploads, completion modal

**In plain terms:** the participant taps "Submit" and is done instantly; all uploading happens invisibly in the background; the operator gets a pop-up on the dashboard showing what was recorded and where it is being uploaded, which shrinks to a little progress widget when closed.

### Current state (verified)

- `POST /api/results` (`backend/routes/results.py`) runs the **Notion upload inline, synchronously** — the tablet's `await` does not resolve until the whole Notion sequence finished/failed. The participant is genuinely waiting on Notion today.
- Offline queue `DATA_DIR/notion_upload_queue.jsonl` is flushed only at server start or manual `POST /api/notion/flush-queue`; no backoff, no attempt counter.
- **Known bug:** `PARTICIPANT_NOTION_PROPERTIES` (5 keys, `notion_upload/adapter.py`) vs `PARTICIPANT_FIELD_ORDER` (8 keys, `validation.py`) → `KeyError` when `gender`, `birth_place`, or `birth_date` is enabled+stored → the session lands in the queue forever.
- Crash-safety of the local save (recovery files, atomic writes, `_partial` snapshots) is good and **must not change**.

### Approach

**Backend — new `backend/services/upload_jobs_service.py`:**
- Persistent job journal: append-only JSONL events (`created` / `attempt` / `done` / `failed` / `retry_scheduled`) at `DATA_DIR/upload_jobs.jsonl`; large payloads in `DATA_DIR/upload_jobs/<job_id>.json`. State rebuilt by replay on boot — a crash never loses a job.
- Job schema: `{job_id, kind: "notion"|"nextcloud", study_id, participant_id, session_id, label, created_at, attempts, next_attempt_at, status: queued|running|done|failed, last_error, steps: [{key, status}]}` — `steps[]` drives the modal's check marks.
- One daemon worker thread (behind `STUDY_RUNNER_DISABLE_BACKGROUND`), backoff 30 s / 2 m / 10 m / 30 m → hourly, capped at 48 h → `failed` (manual retry always possible).
- Boot migration: drain legacy `notion_upload_queue.jsonl` into jobs, delete it. The Notion adapter's own queue-write path is removed — on failure it raises and the job service owns retry. `POST /api/notion/flush-queue` stays (route inventory) but now means "retry all failed Notion jobs".
- `routes/results.py`: after `save_results_payload()` succeeds → enqueue jobs → **return immediately**. The inline Notion block moves into the Notion job executor. Recovery-file semantics untouched.
- Fix the Notion properties bug: complete the map (`gender`/`birth_place`/`birth_date`) and switch lookups to `.get()` with skip-and-log so future mismatches degrade instead of raising. Regression test.

**New routes** (each +1 tuple in `EXPECTED_ROUTES`):
- `GET /api/uploads/status` — jobs of the last N days, grouped by session.
- `POST /api/uploads/retry` — `{job_id}` or `{all_failed: true}`.
- `POST /api/admin/system/open-results-folder` — `{study_id, participant_id}`; opens the session folder on the server machine (pattern: existing shortcut service).

**Frontend:**
- New `web/scripts/upload-monitor.js` (admin page): polls `GET /api/uploads/status` every 3 s; when a fresh session appears → **completion modal** (built on `lib/modal.js`): what was recorded (answer count, sensor file list from job metadata), one row per destination with pending/spinner/✓/error + retry, "Open files" button. Close → **bottom-right mini widget** ("Step x/y" + progress bar); click reopens.
- CSS additions `.upload-widget` / `.upload-progress` in `main.css`; i18n group `uploads.*` (en+de).

Tests: `tests/test_upload_jobs_service.py` — journal replay after simulated crash, backoff schedule, retry route, legacy queue migration.

### Work items

| Item | Size | Risk | Tier |
|---|---|---|---|
| `upload_jobs_service` (journal, replay, backoff, worker) | L | data loss if replay is wrong; thread lifecycle in tests | hard |
| `results.py` decoupling + Notion executor move | M | the most protected code path in the app | hard |
| Notion properties bugfix + regression test | S | low | easy |
| 3 new routes | S | low | easy |
| Completion modal + mini widget | M | UI only | medium |

> **GPT 5.6 Sol input:**
> **Backend implemented (2026-07-30):** completed `PARTICIPANT_NOTION_PROPERTIES` for `gender`, `birth_place`, and `birth_date` (real Notion date property), replacing brittle direct lookups with skip-and-log behavior.
>
> The central upload-job backend is now implemented: result files remain the unchanged commit point, after which Notion/Nextcloud jobs are persisted as atomic payload files plus an fsynced append-only event journal and `/api/results` returns without network I/O. Replay recovers interrupted attempts; retry uses 30 s / 2 m / 10 m / 30 m / hourly backoff with the planned 48-hour automatic cutoff. The legacy Notion JSONL queue migrates idempotently, and the Notion adapter no longer owns a competing retry queue. Status, manual retry, legacy flush compatibility, and guarded Windows/macOS result-folder opening routes are registered and inventory-tested. Job payloads redact backend credentials. Remaining T4 work is the completion modal/mini-widget owned by the UI implementer; real Notion/Nextcloud round-trips remain manual acceptance gates.

> **Claude Sonnet 5 (UI implementation, 2026-07-30):**
> **Completion modal + mini widget built** (`web/scripts/admin/upload-monitor.js`, wired into `admin-controller.js`): polls `/api/uploads/status` every 3 s; the first poll only establishes a baseline (so jobs already queued before the admin page was opened never pop a surprise modal), and a session appearing afterward opens the modal automatically. The modal shows the recorded-answer count, sensor file chips from job metadata, one row per destination (queued/running/done/failed with retry on failure), and an "Open files" button wired to the existing folder-open route. Closing it leaves a bottom-right progress widget ("Uploading x/y") that stays in sync with polling and reopens the modal on click; it turns red and says "needs attention" on a failed job. CSS added (`.upload-widget`, `.upload-progress`, `.upload-job-*`) and i18n group `uploads.*` (en+de, 15 keys).
>
> Caught one real bug only visible in an actual browser: `.upload-widget { display: flex }` was overriding the `hidden` attribute's default `display: none` at equal CSS specificity (an author-stylesheet-vs-UA-stylesheet gotcha this codebase already works around elsewhere, e.g. `.modal-backdrop[hidden]`) — the widget was permanently visible regardless of state. Fixed with an explicit `.upload-widget[hidden] { display: none; }` rule. Neither `node --check` nor the Python test suite could have caught this; found via a Playwright smoke pass against the running dev server.

---

## T5 — Completed-studies browser + timeline view

**In plain terms:** below the study list, a list of finished sessions; opening one shows a timeline — heart rate, breathing, emotions, and EEG bands stacked as lanes, with the answered questions as markers on top.

### Current state (verified)

- Results live at `saved_results/<study_id>/<participant_id>/`: main result JSON (answers + per-card biosignal averages in `answer_details`), `*_mr60_signals.json`, `*_brainbit_signals.json`, `.xdf`. **No route serves any of this; no session index exists.**
- **Gap:** camera emotion has **no time-series sidecar** — only per-card averages survive. The plugin already keeps a history deque; only the sidecar wiring is missing.

### Approach

1. **Emotion sidecar (do early — T7's flush then covers emotions too):** wire `export_interval_samples` / `sidecar_sensor="camera_emotion"` / `sidecar_filename_suffix="camera_emotion_signals"` on `tablet_camera_emotion/plugin.py`, mirroring the mr60 wiring. Careful: only this plugin — the emotion *worker* plugin shares `config_key camera_emotion` and must not double-export.
2. **New `backend/services/sessions_index_service.py`** (lazy folder scan + mtime cache, no persistent index) and routes:
   - `GET /api/admin/sessions` — `[{study_id, participant_id, saved_at, answers_count, files, recovered}]`.
   - `GET /api/admin/sessions/<study_id>/<participant_id>` — result JSON + sidecar metadata.
   - `GET /api/admin/sessions/<study_id>/<participant_id>/signals?sensor=&max_points=` — server-side min/max-envelope downsampling (default 2000 points; a 2 h EEG-band sidecar is ~72 k samples — trivial). Sanitize both ids with `sanitize_identifier_for_filename` against path traversal.
3. **UI:** "Completed sessions" section under the study list (`web/scripts/admin/sessions-browser.js`); clicking opens a full-height in-admin panel (no new page route) rendered by `web/scripts/admin/session-timeline.js` — **vanilla SVG, no libraries** (offline rule): stacked lanes (heart rate, breath rate, emotion, EEG bands) on one shared time axis, answer markers as the top lane, click/hover popover with question text, answer, and the per-card averages from `answer_details`. v1 renders the downsampled full session; zoom/pan is a stretch goal.
4. i18n group `sessions.*` (en+de).

Tests: `tests/test_sessions_routes.py` with a fixture `saved_results` tree; downsampler unit tests (envelope correctness, short/empty inputs).

### Work items

| Item | Size | Risk | Tier |
|---|---|---|---|
| Emotion sidecar wiring | S | double-export if wired onto the wrong plugin | easy |
| Sessions index + 3 routes + downsampler | M | path traversal; large-folder scan cost | medium |
| Timeline SVG component | L | isolated UI, but real design work (axes, lanes, popovers, i18n, touch) | hard |
| Sessions list UI | S | low | easy |

> **GPT 5.6 Sol input:**
> **Implemented (2026-07-30):**
> - Camera-emotion samples now export only through `tablet_camera_emotion` as `camera_emotion_signals`; the worker plugin does not double-export.
> - Added the read-only sessions index with mtime cache, three planned routes, strict identifier/result-file selection, sidecar metadata, and bounded nested-channel min/max envelopes. Repeated sessions for one participant are selected by the concrete `result_file` query parameter while the route count stays unchanged.
> - Added fixture, traversal, cache, repeated-session, empty/short-input, and envelope-extreme tests. UI list/timeline work remains intentionally untouched.

> **Claude Sonnet 5 (UI implementation, 2026-07-30):**
> **Sessions browser + timeline wiring built** (`web/scripts/admin/sessions-browser.js`, wired into `admin-controller.js`): populates the hub's "Completed studies" list (capped at 25 with a "N more" hint) from `GET /api/admin/sessions`; clicking a session opens the existing `#view-session-detail` panel, fetches the result + sidecar metadata, fetches `/signals` per recorded sensor, and hands the assembled lanes to the already-built `session-timeline.js` renderer. Answer markers are built from `answer_details` using the server-clock epoch fields when present (falling back to parsing the ISO timestamps), so they land on the same time axis as the sensor lanes. Marker click shows a popover with the question, the answer, and a generic per-sensor numeric-field dump from `biosignal_interval` (works for any sensor without hardcoding its schema). Also renders the "Questions and answers" and "Saved files" cards. i18n group `sessions.*` added (en+de, 23 keys) — none of it existed before, despite the HTML already referencing it via `t()`/`data-i18n` fallbacks. In the process, split a genuine key collision: `sessions.loading` was reused for two different visible strings (the hub-list placeholder and the detail-page title); the detail title now uses `sessions.detailLoading`.
>
> The timeline renderer itself and the emotion sidecar/index/routes were already done (see above) — this closed the last gap: nothing ever called the renderer or populated the list.

---

## T6 — Nextcloud upload (share link)

**In plain terms:** in the study settings, next to the Notion toggle, a Nextcloud toggle: paste a share link, optionally a password, press "Test connection" — after every study the session folder is mirrored there automatically.

### Current state / research

- `requests>=2.31` is already a dependency — no new packages needed.
- Nextcloud public shares accept WebDAV uploads: primary endpoint `PUT https://<host>/public.php/dav/files/<share_token>/<path>` (NC ≥ 29); legacy fallback `https://<host>/public.php/webdav/<path>` with basic auth `share_token : share_password`. Chunked upload is not available on public shares — irrelevant here: sessions are JSON + XDF, tens of MB at most (no video is ever stored).

### Approach

- New `backend/services/nextcloud_service.py`: `parse_share_link(url)` → `(base_url, token)` from `…/s/<token>`; `MKCOL` for `<study_id>/<participant_id>/` (idempotent); `PUT` per file; endpoint fallback on 404/405; `test_connection()` = `PROPFIND` depth 0.
- Settings: `study_settings.nextcloud_enabled` (bool) + `nextcloud_share_link` (str) validated in `validation.py`'s study-settings section; optional share password stored via `secrets_service.py` (`local_secrets.json`) exactly like the Notion API key — never returned by GET APIs (copy the Notion flow verbatim).
- Upload runs as job kind `"nextcloud"` in the T4 job service; enqueued in `results.py` next to the Notion job.
- New route: `POST /api/nextcloud/test` (mirrors `/api/notion/test`).
- UI: two places, both reusing existing patterns (rule 1). Per-study on/off plus the share link lives in the study settings modal next to the Notion toggle (slide toggle, link field, password field, test button). Anything shared across studies gets a **Nextcloud settings page built on the same shell as the Notion page** (`.settings-page`, `.status-card`, `.setup-step`, `lib/settings-page.js` from T2-A) via `web/scripts/admin/nextcloud-settings-controller.js` — so Notion, Nextcloud and Certificate pages are three instances of one layout, not three designs. i18n group `nextcloud.*` (en+de).

Tests: `tests/test_nextcloud_service.py` with mocked `requests` (link parsing, endpoint fallback, MKCOL idempotency). One manual round-trip against Fabian's real share.

### Work items

| Item | Size | Risk | Tier |
|---|---|---|---|
| `nextcloud_service` (WebDAV, fallback, test) | M | endpoint quirks across NC versions | medium |
| Settings UI + secrets plumbing + validation | M | secrets leaking into GET responses — copy Notion pattern exactly | medium |
| Job kind + enqueue in results.py | S | low (rides on T4) | easy |

❓ Open: Nextcloud server version in use (determines primary vs legacy endpoint — the fallback covers both, but good to know).

> **GPT 5.6 Sol input:**
> **Backend implemented (2026-07-30):** added the public-share WebDAV client, modern-to-legacy endpoint detection, idempotent `MKCOL`, per-file `PUT`, strict share-link/path validation, and mocked regression tests. Also added validated per-study fields, backend-local password storage/redaction, and `POST /api/nextcloud/test`. The T4 upload-job integration is now wired as well: successful `/api/results` saves enqueue Notion and Nextcloud jobs without network I/O in the participant request, and the job executor uploads the saved local session folder with secrets resolved backend-local. Remaining acceptance gate: one manual round-trip against Fabian's real Nextcloud share link. UI implementation and review stay with the planned UI owner.

> **Claude Sonnet 5 (UI implementation, 2026-07-30):**
> The Nextcloud settings page (`admin/nextcloud-settings-controller.js`) and its `#view-nextcloud-settings` markup already existed on the shared T2-A shell, correctly built (setup steps, status tiles, share-link/password fields, test button) — but `initializeNextcloudSettings()` was never called from `admin-controller.js`, so the hub's "Nextcloud settings" button did nothing at all. Wired it in with the same callback shape as the Notion page. Also added the entire `nextcloud.*` i18n group (en+de, 55 keys) — every string on the page was relying on its hardcoded English `t()` fallback, so German operators would have seen English text; also added the two missing `hub.nextcloudSettings` / `hub.certificateSettings` hub-button labels. Verified in a real browser: the button now opens the page, the setup steps and status tiles render with real (localized) text.

---

## T7 — Crash / interruption recovery

**In plain terms:** if the Study Runner crashes or is closed mid-study, today the answers survive per-card but all sensor data is lost and the tablet cannot resume. After this: the tablet resumes seamlessly after a server restart, sensor data is saved every minute, and the dashboard offers "an interrupted session was found — finalize or discard?".

### Current state (verified)

- Session registry `STUDY_SESSIONS` is **in-memory only** (reset in `create_app`) → tablet resume returns 404 after a restart, sensors are not restarted for the session.
- `_partial/<session_id>.json` snapshots are written on every card advance + pagehide — but **nothing ever reads them back**. Same for `_recovery/` dumps.
- All sensor history lives in RAM deques, written to sidecars **only at final submit** → crash = 100 % sensor-data loss.
- LabRecorder XDF survives a crash on disk but is only collected at submit.
- Tablet client: silent reconnect polling already works; sessionStorage snapshot + resume flow exists and needs zero changes once the server remembers sessions.

### Approach

1. **Persistent session registry:** new `backend/services/session_store.py` (extracted from `routes/helpers.py`) — every mutation atomically persists `DATA_DIR/runtime/study_sessions.json`; `create_app` rehydrates (sessions older than 12 h marked stale, not resumable). Tablet resume then works across restarts unchanged.
2. **Periodic sensor flush:** new `backend/services/sensor_flush_service.py` — daemon thread (behind the background flag); every 60 s per active session calls `registry.export_interval_sidecars(context, session_start, now)` and atomically overwrites `DATA_DIR/<study_id>/_flush/<session_id>_<suffix>.json`. Full overwrite is correct and cheap — it reads the same bounded deques the final export reads. Deleted after a successful final save.
3. **Boot-time recovery scan:** new `backend/services/recovery_service.py` — scan `_partial/` + `_flush/` + `_recovery/` for artifacts without a matching saved result; group by session into candidates `{recovery_id, study_id, participant_hint, last_activity, answers_count, sensors_flushed, has_xdf_nearby}` (XDF is a hint only in v1, never auto-moved).
4. **New routes:** `GET /api/admin/recovery` (candidates), `POST /api/admin/recovery/finalize` (build a results payload from the partial snapshot + flushed sidecars, write into `saved_results` via `results_service` with `recovered: true` markers, enqueue upload jobs like a normal save — **reuses, never forks, `results_service`**), `POST /api/admin/recovery/discard` (move to `_recovery/discarded/`, never hard-delete).
5. **UI:** "Interrupted session found" card at the top of the dashboard + confirm dialogs via the shared modal. i18n group `recovery.*` (en+de).
6. Note: finalize needs tolerance for missing answers → **depends on T9's optional-answer skip path** (recovered sessions are inherently incomplete).

Tests: `tests/test_session_store.py` (persist/rehydrate/stale); `tests/test_recovery_service.py` (fixture crash artifacts → candidates → finalize produces a valid, browsable saved result). Manual crash drill: kill the server mid-study, restart, tablet resumes; separately: tablet gone → recovery card appears, finalize works.

### Work items

| Item | Size | Risk | Tier |
|---|---|---|---|
| `session_store` extraction + persistence + rehydrate | M | session semantics regressions (heartbeat, overrides) | hard |
| `sensor_flush_service` | M | must never block trial markers; thread hygiene | medium |
| Recovery scan + finalize | L | writing wrong data into `saved_results` | hard |
| Recovery card UI + i18n | S | low | easy |

> **GPT 5.6 Sol input:**
> **Backend present + verified (2026-07-30):** the working tree now contains the non-UI T7 implementation: persistent `SessionStore` rehydrates active sessions and marks stale ones; `SensorFlushService` periodically writes per-session sensor flush files behind `STUDY_RUNNER_DISABLE_BACKGROUND`; `recovery_service.py` scans `_partial`, `_flush`, and `_recovery`, finalizes through `save_results_payload`, splices flushed sidecars back into browsable results, archives handled snapshots instead of deleting them, and queues uploads via the existing T4 job path in the recovery route. The T9 skip-marker prerequisite is also complete and distinguishes skipped optional cards from never-reached cards. Verified with `python -m unittest -v software.tests.test_sensor_flush_service software.tests.test_session_store software.tests.test_recovery_service software.tests.test_recovery_routes software.tests.test_route_inventory` (42 tests green, run outside the sandbox because sandboxed Python cannot write inside `tempfile.TemporaryDirectory()` on this machine). Remaining acceptance gates: manual server-kill/tablet-resume drill, finalize/discard drill from the dashboard UI, and real sensor/XDF recovery smoke.

> **Claude Sonnet 5 (full T7 implementation, 2026-07-30):** Fabian asked me to own T7 completely rather than only the UI slice, with GPT 5.6 Sol/Codex reviewing — this whole topic (services, routes, UI, tests) is mine; the review above landed on the backend while I was still building the rest, which is a good independent sanity check. Two things worth recording beyond what that review saw:
>
> - **Sensor restart on resume, added after investigation, not in the original approach text:** `ACTIVE_STUDY_HARDWARE_CONFIG`/`ACTIVE_STUDY_SENSOR_PLUGINS` live only in `current_app.config`, so they're gone after a restart exactly like the old `STUDY_SESSIONS` was. Session persistence alone would have resumed the *tablet UI* while no sensor was actually recording again. `_resume_study_session` now calls `_start_study_sensor_runtime` whenever a resume finds no active hardware config. Covered by `test_study_session_routes.py::test_resume_after_restart_restarts_sensors_too`.
> - **Recovery candidates must not fire for a session that can still resume normally**, or the banner would show during every single in-progress study (a `_partial` snapshot exists from the first card onward). `list_recovery_candidates` now takes the session store's currently-active session IDs and excludes `_partial`-sourced candidates for any of them; a `_recovery/` dump (a *complete* submission whose save failed) is exempted from that filter since the participant already finished and the operator needs to act immediately, not wait. Covered by dedicated tests in both `test_recovery_service.py` and `test_recovery_routes.py`.
>
> **UI:** hub banner (`recovery-panel.js`, shown only when candidates exist), reusing the existing `confirm()` pattern already used for deleting a study, wired so a successful finalize also refreshes the T5 sessions list. i18n group `recovery.*` added (en+de, 12 keys).
>
> **Manual acceptance gates from the review above, now done:** a real crash drill — spawned the actual server as a child process, drove the tablet page with Playwright through participant-id and into the first real question, force-killed the server (not a graceful shutdown), respawned it against the same data dir, reloaded the *same* browser tab (so `sessionStorage` survives like a real reload would), clicked "Continue study", and confirmed `/api/study/session/resume` returns 200 and the UI lands back on the exact card (`card-q-1`) the session was on before the crash. Separately verified finalize and discard by clicking through the real hub banner against injected orphaned snapshots: finalize produces a browsable result that immediately shows up in Completed studies, discard archives to `_recovery/discarded/` without deleting anything - zero console errors in either case. Full suite: 244/244. Not covered: a real sensor/XDF recovery smoke (needs physical BrainBit/MR60 hardware or a live LabRecorder, unavailable here) - the file-splicing logic itself has direct unit coverage instead.
>
> **Aside, not mine to fix:** the crash drill surfaced a pre-existing, unrelated bug - `POST /api/marker` rejects the client's very first trial-start marker with `clock_offset_ms must be between -3600000.0 and 3600000.0`, and the reported offset is a huge nonsense value (looks like an absolute epoch-ms timestamp leaking in where a small delta belongs) rather than a real clock skew. Reproduces on a plain participant-id → next click, no crash or resume involved, so it predates and is unrelated to this topic. Flagging for whoever owns clock sync.
>
> **GPT 5.6 Sol audit + clock-sync follow-up (2026-07-30):** fixed the unrelated clock-sync bug Claude flagged. The client now sends the small wall-clock offset from `getClientClockOffsetMs()` as `clock_offset_ms` while keeping the precise server-epoch marker timestamp in `client_trigger_epoch_ms`; `/api/marker` accepts the first trial/question marker again with a sane offset. Added validation and route coverage for sane vs. epoch-sized marker offsets. Verified with `python -m unittest -v software.tests.test_validation software.tests.test_route_inventory` (25 tests green; sandbox run failed only because Windows `tempfile.TemporaryDirectory()` is blocked here, so the green run was outside the sandbox).
>
> T7 audit follow-up fixes applied (2026-07-30): `SessionStore.resume()` now rejects a known `session_id` when supplied `study_id`/`participant_id`/`client_id` do not match the stored active session; the participant client now exports only real card/answer events instead of fallback events for never-shown future cards; `STUDY_RUNNER_DISABLE_HARDWARE=1` is persisted in `app.config` and study session start/resume no longer initializes sensor runtimes while it is set. Recovery finalization also writes top-level `skipped_questions` for shown-but-skipped optional cards, keeping recovered result JSON aligned with normal submissions. Added regression coverage for each fix. Verified with `node --check software\study_runner\web\scripts\study-controller.js`, targeted unittest coverage (76 tests green), and full `python -m unittest discover -s software\tests -p test_*.py -v` (249 tests green; run outside the sandbox because sandboxed Windows tempfile writes are blocked here).

---

## T8 — Make the certificate effortless to install (keep the local CA)

**In plain terms:** certificates get their own settings page in Study Runner, built exactly like the Notion and Nextcloud pages — status, setup steps, buttons, done. The QR code has one single job: the tablet scans it and downloads the certificate. Nothing else moves out of the app.

### Why encryption is needed at all (the question behind this topic)

Not to protect the data. The WLAN is local and camera frames are discarded right after emotion analysis. The single reason is a browser rule: **camera access requires a secure context.** `web/scripts/camera-capture.js:19` aborts before touching the camera when `window.isSecureContext` is false, because `getUserMedia()` is unavailable on a plain-HTTP page served from a LAN IP. There is no flag or exception for this on iPadOS.

Everything else in the participant flow already works over plain HTTP (cards, answers, markers, heartbeat) — `STUDY_RUNNER_HTTPS=0` (`runtime_config.py:60`) exists for exactly that. So the certificate is a camera prerequisite, nothing more.

### Decision (Fabian, 2026-07-30 — supersedes both earlier designs)

The Let's Encrypt route (mine, then Codex's more rigorous version) is **not pursued**. Reasons, in order of weight:

1. **Expiry risk.** The local root CA is valid **3650 days** (`ssl_service.py:129`) and cannot expire mid-study. A Let's Encrypt certificate lasts 90 days and depends on automation that must keep working — DNS provider API changes, rotated credentials, or a bad moment offline all become a failure exactly when a session is running. Codex's elaborate diagnostics exist *because* that risk was introduced. For a lab that must not fail during a session, the boring 10-year CA is the more reliable choice.
2. **Complexity vs. scale.** Per-installation identities, an automatic DNS updater, a six-step wizard, and three manuals are disproportionate for one server and one tablet.
3. **Nothing about the install is actually recurring** once the certificate is portable (see below): the CA is valid for 10 years, so it is a one-time, ~3-minute setup per tablet.

Kept as a documented future option if the setup ever grows to many tablets or frequently changing server machines. Codex's design and my review of it are preserved in `docs/archive/` for that case.

Also considered and rejected: uploading the certificate to Nextcloud and linking a QR code to the public share (Fabian's first idea). It solves the same problem as item 1 below but adds an upload, a public link to a certificate file, and a tablet internet dependency — the tablet is already on the same WLAN as the server and can fetch it directly.

### Approach — two work items only

**1. A Certificate settings page, built like the Notion page (in-house, no separate guide page).**

New admin view `#view-certificate-settings` + `web/scripts/admin/certificate-settings-controller.js`, using the shared shell from T2-A (`.settings-page`, `.status-grid`/`.status-card`, `.setup-step`, `lib/settings-page.js`). Reached from a hub card, exactly like Notion. Its cards:

| Card | Content |
|---|---|
| Setup steps | The numbered `.setup-step` list, reused verbatim from the Notion guide pattern, with a per-step state marker: download on the tablet → install the profile → switch on full trust → open the study page. Plain language, no certificate vocabulary. |
| Status | `.status-card` tiles: certificate valid until, which addresses it covers, the CA's SHA-256 fingerprint (reuse `ssl_service._certificate_fingerprint_sha256`), and whether HTTPS is currently active. |
| Tablet setup | The **QR code** whose only content is the certificate download URL, plus that URL as text. This is the only QR-related work in T8. |
| Move to another computer | Export and import buttons (work item 2). |

Why the QR carries a URL and not the certificate itself: the repo's offline QR encoder (`web/scripts/qr-code.js`) supports versions 1-6, i.e. roughly 106 bytes; a PEM root CA is over 1 KB. A URL is the only thing that fits.

Why the download needs plain HTTP: the tablet cannot fetch the CA over an HTTPS connection it does not trust yet. Add one small stdlib `http.server` daemon thread started next to the Flask app in `app_server.py`, serving **exactly one file** (the root CA) on a configurable port, everything else 404. It is deliberately not a Flask route, so `EXPECTED_ROUTES` stays untouched; keep it in its own module (`backend/services/certificate_download_service.py`) so its scope stays obvious and testable. The settings page itself is normal Flask/HTTPS like every other page.

Note what cannot be automated regardless of delivery: iPadOS requires the operator to switch on full trust under *Settings > General > About > Certificate Trust Settings*. The setup-step list names that step in plain words; that is the honest fix.

New i18n group `certificate.*` (en+de).

**2. Make the certificate portable between server computers.**

Today every machine generates its own root CA, which is exactly why a new laptop meant repeating the tablet setup. Export bundles the two root-CA files from `<SETTINGS_DIR>/ssl/`; import writes them back on the second computer, which then signs its own leaf certificate with the CA the tablet already trusts. The leaf is reissued automatically there because the existing SAN check fails for the new machine's addresses (`ssl_service._server_certificate_matches`) — no extra code needed.

Cross-platform requirement (rule 3): export is a **browser download** and import a **browser file upload** — no native file dialogs, no OS-specific paths, identical on Windows and macOS. Import must validate the uploaded files (is this really a CA certificate, does the key match the certificate?) and refuse a bad bundle without touching the working files, so a mistyped import can never leave the server unable to start HTTPS.

**Document honestly** that the root CA private key is stored unencrypted (`ssl_service._write_private_key` uses `NoEncryption()`), so the export file grants the ability to impersonate any site to devices that trust it — acceptable for own devices in an own lab, but it belongs in the operator guide, not hidden.

Dropped from consideration: a UI toggle for "run this study without emotion detection" (would avoid the certificate entirely for camera-free studies), and a separate step-by-step guide page — Fabian judged both as added complexity. `STUDY_RUNNER_HTTPS=0` remains available for developers.

### Acceptance tests

- A tablet with nothing installed scans the QR on the certificate page, installs the certificate following only the on-screen steps, then opens the study URL and the camera works (`isSecureContext` true).
- Export the CA from computer A, import on computer B: the same tablet opens computer B's study URL without any new install and without a trust warning.
- The whole flow is verified on **both** a Windows and a macOS server, in a packaged build (rule 3).
- The download listener serves only the CA file; every other path returns 404.
- A deliberately broken import (wrong file, mismatched key) is refused and the server still starts HTTPS with the previous certificate.
- The certificate page uses the same shared shell classes as the Notion and Nextcloud pages (rule 1), asserted in `test_web_ui.py`.
- Guard suites stay green; the route inventory changes only by the export/import endpoints.

### Work items

| Item | Size | Risk | Tier |
|---|---|---|---|
| Certificate settings page on the shared shell (status, setup steps, QR) + i18n | M | none structural if T2-A landed first; copy the Notion page, do not invent | medium |
| Single-file HTTP download listener | S | must serve nothing else; port collision handling | medium |
| Root-CA export/import with validation + security documentation | M | handling an unencrypted private key; a bad import must never break HTTPS startup | hard |

> **GPT 5.6 Sol input:**
> **Implemented (2026-07-30):** the single-file HTTP bootstrap listener is complete. It serves only `/study-runner-local-root-ca.crt` via GET/HEAD, returns 404 for every other tested target/method, has no Flask route or directory exposure, reports port conflicts without taking down HTTPS, publishes its runtime status/URLs for the settings page, respects `STUDY_RUNNER_DISABLE_BACKGROUND`, and shuts down with the server. Default port is 3002 and can be overridden with `STUDY_RUNNER_CERTIFICATE_DOWNLOAD_PORT`.
>
> The backend CA handover is also complete: status/export/import routes are registered and inventory-guarded; an export is explicitly `no-store`; imports are schema-, CA-, expiry-, and key-pair-validated before disk mutation and limited to 1 MB. CA/key writes are individually atomic and form one rollback unit with old-CA backups and retirement of leaf certificates. A failed second write restores the original pair; a successful import forces a leaf reissue under the imported CA and reports that a restart is required. This is the deliberate multi-computer solution: install one long-lived lab CA once per tablet, then move that CA securely between Study Runner computers instead of repeating tablet trust setup. The settings UI remains with its planned UI owner.

> **Claude Fable 5 (note, 2026-07-30):** For the record, my review of Codex's Let's Encrypt design still stands on its merits — fail-closed instead of a silent local-CA fallback, a per-installation hostname, and an automatic DNS updater were all improvements over my own first draft. It was the *premise* that turned out to be wrong for this setup: a 90-day certificate needs reliable automation, whereas the 10-year CA needs none. Two cautions from that review remain relevant if the topic is ever revived: Caddy's DNS-provider modules are not in its standard binary (DNS-01 needs an `xcaddy` custom build per release platform, which is the same packaging trap that broke BrainBit in T1), and the pure-Python `acme` library — certbot's own core — freezes like any other dependency.

> **Claude Sonnet 5 (UI implementation, 2026-07-30):**
> Same situation as T6: `certificate-settings-controller.js` and the `#view-certificate-settings` shell were already fully built (setup steps, status tiles, QR card, export/import buttons) but never called from `admin-controller.js`, so the hub's "Certificate" button was dead. Wired it in. Added the entire `certificate.*` i18n group (en+de, 58 keys — none existed) and the CSS that was missing for this page's own markup (`.certificate-qr`, `.certificate-fingerprint`, `.certificate-warning`) as well as for the T5 timeline (`.timeline-*`, `.session-answer-list`, `.session-file-list` — session-timeline.js was committed in a later commit than main.css, so its classes were unstyled). Verified in a real browser with `STUDY_RUNNER_HTTPS=0` (dev mode): the page opens, status tiles correctly show "off"/"-" and the QR area correctly stays empty (no download URL without HTTPS) rather than erroring.

---

## T9 — Optional vs. required fields

**In plain terms:** in the editor, every question and every demographic field gets a "Required" toggle. Optional questions show a subtle "optional" tag on the tablet and can be skipped.

### Current state (verified)

- Everything is mandatory, doubly enforced: client (`isAnswered` gates the Next button in `study-controller.js`; `card-participant-id.js` `_updateHash` requires every enabled field) and server (`_validate_answers` raises on any missing answer; `_validate_participant_metadata` requires every enabled+store field — `validation.py`).

### Approach

1. **Schema:** per-question `required` bool, **default `true`, absent = required** (full back-compat — existing studies unchanged). Participant fields: per-field `required` bool, but `use_for_key: true` **forces required** (the anonymous-code hash needs the value); validation rejects `required: false` + `use_for_key: true` with a plain-language message.
2. **Server:** `_validate_answers` skips the missing-answer error for optional questions (provided values are still fully validated); optional enabled+store metadata may be absent.
3. **Results JSON:** optional-and-unanswered questions get `{"answer": null, "skipped": true}` in `answer_details` plus a top-level `skipped_questions: [...]` — distinguishing "shown but skipped" from "never shown". (Notion upload renders skipped as em-dash.)
4. **Editor UI:** "Required" slide toggle in the card editor sidebar; per-field required toggles in the participant-id field editor, locked with a hint when `use_for_key` is on.
5. **Client:** Next button enabled for unanswered optional questions; subtle "optional" badge on such cards (`main.css` badge style); `_updateHash` requires only `use_for_key` fields.
6. i18n: `editor.required_label`, `editor.required_locked_hint`, `study.optional_tag` (en+de).

Tests: validation (optional missing OK; required missing still errors; `use_for_key`+optional rejected; provided-but-invalid optional value still errors); results JSON skip markers. The existing toggle guard test automatically covers the new switches.

### Work items

| Item | Size | Risk | Tier |
|---|---|---|---|
| `validation.py` + `results_service` changes | M | loosening the most protected validation path — must not weaken required enforcement | hard |
| Editor toggles + participant-id constraints | M | editor state serialization edge cases | medium |
| Client gate + badge + hash fix | S | Next-button regressions | medium |

> **GPT 5.6 Sol input:**
> **Backend implemented (2026-07-30):** config normalization now persists `required` for answer cards with absent values defaulting to required, and participant fields have `required` with `use_for_key` forcing required plus a validation rejection for `required: false` on hash inputs. Result validation allows missing optional answers/metadata while keeping required enforcement and still validating optional values when provided. Saved results now include top-level `skipped_questions` plus `answer_details` entries with `answer: null` and `skipped: true` for optional questions that were actually shown; optional questions with no card/answer event are left unmarked so T7 recovery can distinguish "skipped" from "never reached." Notion renders skipped answers as an em dash. Regression coverage was added for optional-missing, required-still-missing, invalid optional values, optional stored metadata, hash-field locking, shown-vs-unshown optional skip markers, answer-detail skip markers, and Notion formatting. Verified with `python -m unittest -v software.tests.test_results_service software.tests.test_validation software.tests.test_notion_upload` (37 tests green, run outside the sandbox because sandboxed Python cannot write inside `tempfile.TemporaryDirectory()` on this machine).

> **Claude Sonnet 5 (UI implementation, 2026-07-30):**
> The backend for this topic (schema default, `_skipped_optional_questions`, participant-field `required` + the `use_for_key` lock/rejection, `answer_details` skip markers) is already in the working tree — see Codex's note under the T7 input block above (it looks like it landed in the wrong section; it describes this T9 work, not T7's crash recovery). Confirmed complete, not just started: re-read `results_service.py` and `notion_upload/adapter.py` end to end (the two files I hadn't already seen mid-write) and ran `test_validation.py` + `test_results_service.py` + `test_notion_upload.py` directly (35 tests, all green) — includes a direct test of the Notion em-dash rendering for skipped answers. **T9 is done on both sides, backend and UI**, pending only the manual mixed-required-study tablet run in both languages that phase P4 calls for. Built the UI side against it:
> - **Editor:** a generic "Required" slide toggle now lives in the shared per-question editor (`cards/card-info.js`, appended after every type's own editor fields), defaulting to on and hidden for `participant-id`/`stimulus`/`finish` — mirrors `NON_ANSWER_QUESTION_TYPES` in `validation.py` exactly. Participant-id fields get their own per-field "Required" toggle in the existing field-settings modal (`card-participant-id.js`), disabled and showing a locked hint whenever "Use for anonymous code" is on, live-updating if that toggle is flipped inside the same modal — matches the server's own use-for-key-forces-required rule, so the client can never submit a combination the server would reject.
> - **Client:** `study-controller.js`'s Next-button gate now bypasses the answered-check for optional questions (`isAnswered()` itself is untouched, since `collectAnswers()` still needs its real touched/untouched signal); an optional, never-touched question's key is omitted entirely from the submitted `answers` object rather than sent with a default value, so the server's `{"answer": null, "skipped": true}` path actually triggers. A subtle "optional" tag renders above any card whose question has `required === false` (`renderOptionalTag` in `card-info.js`, new `.q-optional-tag` style).
> - i18n added: `editor.requiredLabel`, `editor.requiredHint`, `cards.participant.requiredLockedHint`, `study.optionalTag` (en+de).
>
> Verified end-to-end in a real browser: a new Text question shows the Required toggle checked by default; opening a participant-id field's settings shows Required disabled+locked (hint visible) while "Use for anonymous code" is on, and unlocks live the moment that toggle is switched off. Full pytest suite (195 tests, including Codex's new validation/results-service coverage) passes together with this frontend work.

---

## Dependency order

```
T2-A shared utils + modal + settings-page shell ──┬─→ T1 (frozen-detect)
                                                  ├─→ T4 modal + widget
                                                  ├─→ T6 Nextcloud settings page
                                                  ├─→ T7 recovery card
                                                  └─→ T8 certificate settings page
T4 job service ──→ T6 (nextcloud job kind)
T4 job service ──→ T7 finalize (enqueues uploads for recovered sessions)
T9 optional-answers ──→ T7 finalize (recovered sessions are incomplete)
T5 emotion sidecar (tiny, early) ──→ T7 flush covers emotions
T1 ──→ T2-B brainbit split          T9 ──→ T2-B study-controller split
T2-C cosmetics last

The settings-page shell in T2-A is now a hard prerequisite for T6 and T8, not a nicety:
both are supposed to be further instances of the Notion page, not new designs (rule 1).
```

## Phase plan

| Phase | Contents | Verification |
|---|---|---|
| **P1** | T2-A shared utils/modal · T1 BrainBit fix · T5 emotion sidecar · T4 Notion-properties bugfix | full pytest; real packaged build smoke-tested with a physical headset; dashboard shows plain-language BrainBit errors |
| **P2** | T4 job service, instant submit, routes, modal + widget, queue migration | kill-and-replay journal tests; manual: Notion blackholed → tablet gets instant done screen, retry visible in modal, succeeds when network returns |
| **P3** | T6 Nextcloud | mocked unit tests + one manual round-trip against the real share link |
| **P4** | T9 optional fields | validation tests; manual tablet run with mixed required/optional study, both languages |
| **P5** | T7 crash recovery | scripted crash drill: kill server mid-study → restart → tablet resumes; recovery card + finalize produces a browsable session |
| **P6** | T5 sessions browser + timeline | fixture route tests; timeline check against a real ~30 min all-sensors session |
| **P7** | T8 certificate settings page (shared shell) + download QR + CA export/import | on Windows **and** macOS, packaged: a tablet with nothing installed completes setup from the page's steps alone and the camera works; the same tablet then trusts a second computer after a CA import, with no new install; a broken import is refused without breaking HTTPS |
| **P8** | T2-B splits + T2-C cosmetics | pytest green; route surface byte-identical; manual participant-flow smoke |

Route-inventory additions across the whole roadmap: **13 tuples** — T4: 3, T5: 3, T6: 1, T7: 3, T8: 3 (`GET /api/admin/certificate/status`, `GET /api/admin/certificate/export`, `POST /api/admin/certificate/import`; the CA download itself runs on the separate listener and adds no Flask route). Each is added to `EXPECTED_ROUTES` in the same commit as its route.

---

## Open questions summary (❓)

1. T8: which port for the plain-HTTP certificate download (must not collide with the study port 3000, the emotion worker on 3001, or common local services)? Proposal: 3080, configurable.
2. T6: how many Nextcloud destinations are wanted — one shared folder, or two (e.g. archive + working folder)? The T4 job service supports any number; this only changes the settings UI.
3. T6: Nextcloud server version (endpoint fallback covers all, informational).
4. T1: exact importable module names of the `pyneurosdk2` / `pyem-st-artifacts` wheels (verify during implementation).

The Let's Encrypt / DNS questions are retired with the T8 decision; they are recorded in `docs/archive/roadmap-0.5-t8-letsencrypt-alternative.md` should the topic ever be revived.
