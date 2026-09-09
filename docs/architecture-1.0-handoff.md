# Architecture 1.0 - shared Claude/Codex handoff

Updated: 2026-09-08. Read this file and [the working plan](architecture-1.0-umbau.md)
before continuing. Both are tracked repository files, accessible to either
assistant through the local checkout; no private assistant memory is required.

## Current authoritative state

Phase 4 is complete. Built-ins live in categorized `extensions`, the UI lives
in `apps/ui`, and the Flask factory, routes, and runtime live in `apps/server`.
The old `plugins`, `frontend`, and `backend` packages are gone; both promised
server entrypoints remain. Stored known hardware paths migrate idempotently,
while unknown custom paths are preserved. Planned session duration is editable,
and invalid capacity inputs or an unreadable storage status fail closed with a
useful blocker.

The final structure checkpoint records **235 visible package edges and 0
cycles**. The earlier 152-edge baseline treated large `backend` and plugin
packages as single areas; nested measurement now exposes the intended
`apps.server` to `runtime_core` and host/worker/contract boundaries. No
forbidden dependency was allowlisted. Version is `1.0.0-dev`.

Final evidence: **814 Python passed, 4 skipped; 27 JavaScript passed; 25
release/packaging passed; structure and version checks passed.** A fresh real
Windows onedir bundle containing only `extensions/outputs/packaging_probe`
passed catalog discovery, UI/root routing, and child-process
initialize/status/shutdown RPC. Disposable logs are under
`.tmp/bundle-final/` in the main workspace.

## Phase 3.1/3.3/3.5 complete — 2026-09-08

`_import_plugin` (the v3 in-process import path) removed;
`SUPPORTED_PLUGIN_API_VERSIONS` narrowed `(3, 4)` -> `(4,)`; `entry_point` is
now optional. Two things this turned up that go beyond a mechanical version
bump — **read the working plan's Phase 3 section for the full account, this
is a summary**:

1. `markers.py`/`clock_diagnostics.py` load their own `api_version: 3`
   manifests at *module scope*, imported eagerly by
   `apps/server/application.py` — narrowing the tuple without bumping these
   two in the same commit would have broken `create_app()` entirely (T3,
   caught for real, not just per the doc's warning). Both bumped to
   `api_version: 4` with a `runtime` block that is schema formality only —
   these two are host-process modules, never spawned as `driver.py`.
2. `_validate_plugin_object` in `plugin_catalog.py` turned out to be dead
   code for its only remaining caller (`build_process_plugin` derives every
   handler it checks for directly from the same manifest) — removed, along
   with the two tests that only ever exercised it.
3. Three test files used a "synthetic plugin in a temp directory" pattern
   that broke once any of them tried to invoke a live handler: v4 spawns a
   real `driver.py` subprocess that resolves itself via
   `extension_layout.trusted_roots()`, hardcoded to the real `extensions/`
   tree, with no way for a spawned child to see a parent test's
   monkeypatches. Added a test-only environment-variable seam
   (`extension_layout.TEST_EXTRA_ROOT_PATH_ENV_VAR`/
   `TEST_EXTRA_ROOT_PACKAGE_ENV_VAR`, read only from the environment — never
   a request or manifest value) and a shared `tests/support/fixture_plugin.py`
   helper; all three fixtures now go through the genuine v4 subprocess
   pipeline instead of a synthetic stand-in.

3.3 (doc wording) and 3.5 (one stale sentence in `extensions/README.md`) were
both already smaller than the working plan estimated and are done. Full
suite: **812 passed, 4 skipped** (two fewer than 814 from the dead-test
removal in point 2, not a new gap).

## 3.2 closed — verification only, 2026-09-08

`upload_destination.legacy` migrate-and-write-forward was already fully
implemented, predating this rebuild. Traced the full persist chain
(`apps/server/routes/{admin,study}.py` -> `validate_and_normalize_config` ->
`normalize_study_settings_plugins` -> `_remove_legacy_destination_fields`,
all in `runtime_core/studies/`): every save already migrates
`notion_enabled`/`nextcloud_enabled`/etc. into the canonical
`study_settings.plugins.<key>.settings` shape and strips the flat legacy
keys before writing. Confirmed by two already-passing tests
(`test_study_plugin_config.py`,
`test_study_settings_contract.py::StudySettingsRoundTripTests`), not just by
reading the code. No code changed. The read path (accepting legacy fields as
input) correctly stays per D3 -- removing it is gated on a real release
shipping first, which hasn't happened (version is still `1.0.0-dev`).

## 3.4 design plan written, not yet implemented — 2026-09-08

Traced every consumer of `readiness`/`runtime_control`/`health` before
proposing a merge shape — see the working plan's 3.4 entry for the full
account. Headline finding: `health` and `runtime_control` both turned out to
have **zero effect anywhere in the running app today** (confirmed by tracing
every call site, not assumed) — only `readiness` (platform-mode support,
meaningful only for camera_emotion) is load-bearing. Recommended design:
drop `runtime_control`, make `health` actually gate polling for the first
time, rename `readiness` to `runtime_modes` to stop colliding with the
unrelated `readiness_requirements` capability, bump to `api_version: 5`.
**Not yet implemented or approved** — read the working plan before starting
3.4's actual code changes.

## 5e complete — 2026-09-08

Journal/XDF event-id comparison. Duplicate detection *within* the XDF marker
stream already existed; what was missing was comparing the durable session
journal's full event-id set against the XDF's, reported as a soft
`quality_warnings` entry (`journal_xdf_event_id_mismatch`,
`missing_from_xdf`/`extra_in_xdf`) rather than a hard failure -- operator
decision, matches the target doc's §9 wording. New
`card_summary_service.py::_journal_xdf_mismatches`, opt-in via
`CardSummaryBuilder.build()`'s new `journal_event_ids` parameter (`None`
skips it, every existing caller unchanged). New
`FinalizationService._journal_event_ids()` reads the durable "trial" journal
straight from disk (not a live `TrialEventService`, which will not exist
after a server restart). **A real bug was caught by the new tests before
shipping**, not after: the journal record's payload sits under a nested
`"snapshot"` key, not the record's top level -- the first draft silently
read nothing back, which would have flagged every real session's XDF
markers as `extra_in_xdf`. Fixed before commit. 7 new tests; full suite
**819 passed, 4 skipped**; JS 27 passed; structure baseline rewritten as a
checkpoint. See the working plan's 5e entry for the full account.

## 5d complete — 2026-09-08

Stream contracts frozen at start, persisted as `stream-contracts.json`,
written into the XDF header; timing-delay provenance defined in the same
pass (operator decision: do both parts of 5d together rather than deferring
provenance to 5c). **Confirmed before writing code**: the native XDF writer
needed no change -- `card_summary_service.py` already read a
`desc/study_runner/...` namespace defensively, and
`tools/make_timeline_fixture.py` already wrote a one-field version of it;
nothing had ever populated it for a real recording. Pure Python-side LSL
`StreamInfo.desc()` metadata, no ABI risk.

New `contracts/manifest.py` schema:
`streams[].timing.capture_delay_ns.{source,min_ns,max_ns,reference}`,
`source` one of `measured|datasheet|estimated|unknown`, defaulting to an
honest `"unknown"` -- no adapter has a real measured delay yet, and
inventing one would be exactly the "silently count as success" data
CONTRIBUTING.md warns against. New `contracts/stream_contract.py`
(`stream_contract_desc_fields`, `apply_stream_contract_desc`,
`load_own_stream_contracts`) wired into all five LSL producers
(`data_core/host/{markers,clock_diagnostics}.py` plus the three sensor
adapters, which as v4 process-host plugins load and normalize their own
`manifest.json` independently, same pattern `markers.py` already used). New
`recording_contract.py::stream_contracts_document()` projects the
already-frozen `recording_contract["streams_by_source"]` into the new
per-session file, written once at the same freeze point as
`recording-plan.json` in `recording_runtime.py::start_session()`.
`tools/make_timeline_fixture.py` updated to match (T8).

11 new tests; full suite **830 passed, 4 skipped**; JS 27 passed; structure
baseline rewritten as a checkpoint. See the working plan's 5d entry for the
full account.

## 5a complete -- 2026-09-08

Explicit session lifecycle (`IDLE`/`PREFLIGHT`/`RECORDING`/`FINALIZING`/
`SEALED`/`WITHDRAWN`/`FAILED`) as new `contracts/session_lifecycle.py`:
states, allowed transitions, guards, and `derive_session_lifecycle()`.
**Additive by design** -- the target doc's own instruction to keep
recording, finalization and upload as separate state machines is what makes
this tractable: the four existing status dimensions are *mapped*, not
merged, and nothing about them changed. Surfaced as a `lifecycle` field per
session in `sessions_index_service.py`, derived on read so it can never
disagree with the documents it comes from.

Three decisions worth knowing: upload status is genuinely not an input (so a
refusing destination cannot un-seal data); `SEALED` requires the data to
have validated, so a `completed` job with `quality_status: invalid` maps to
`FINALIZING` while a human-confirmed `completed_degraded` is sealed; and
`attention_required` is terminal on neither machine. `WITHDRAWN` is defined
with its transitions and a settled marker name (`WITHDRAWN.json`) so 5i has
something to write into. **Caught by running it against a real session, not
just unit tests:** the first draft called the shipped `Demo_Completed_Study`
fixture `IDLE`, because an archival session has only its `COMPLETE.json`;
the derivation now falls back to that marker.

21 new tests; full suite **851 passed, 4 skipped**; JS 27 passed; structure
baseline rewritten as a checkpoint. See the working plan's 5a entry for the
full account, including the mapping table.

## 5c complete -- 2026-09-08

`quality.jsonl` and `timing.jsonl` written *while* recording, so an aborted
session still leaves QC behind -- that sentence from target doc §9 shaped
the whole design.

The counters largely existed already (`StreamRuntimeState` tracked sample
counts, timestamps, clock offsets, reconnects) but lived only in memory.
What was missing: a durable journal, thresholds that turn a number into an
event, and the derived metrics nobody computed live (gaps, timestamp
regressions, jitter, effective rate). New `contracts/quality_journal.py`
(pure, so the detached worker needs nothing host-side): schemas, the
versioned quality profile, `StreamQualityObserver` (running aggregates
only, never samples), `WallClockJumpDetector`. New
`data_core/worker/session_journals.py`: one append-only writer per worker
process, fsynced on the same checkpoint tick that already flushes XDF data
durably -- a crash loses at most the same window of evidence as of data. A
journal write can never break a recording; an unwritable disk is swallowed.

**A real numerical bug caught by the new tests, not shipped:** jitter was
first computed as `E[x²] - E[x]²`, which cancels catastrophically for
sample intervals and reported 1.3 ns of jitter on a perfectly even stream
-- false precision in exactly the number a researcher reads as timing
quality. Replaced with Welford's algorithm (now 3 ps on a synthetic even
250 Hz stream, which is the timestamps' own float representation).

Out of scope by decision, named in the module docstring: queue utilisation
belongs to 5h which introduces the queues, and continuous free-storage
monitoring is not ingest observation (5b already gates the start).

19 new tests; full suite **870 passed, 4 skipped**; JS 27 passed; structure
baseline rewritten. See the working plan's 5c entry for the full account.

## 5h complete (Claude, 2026-09-08)

Commit `0f7a2ba` plus this one. Confirmed prefix + bounded ingest.

The gap 5h actually closed: the worker already flushed durably every five
seconds and recovery already preserved a crashed generation's segment, but
nothing recorded *where those flushes fell*. So "how much reached the disk"
answered "everything up to some flush, then an unknown amount more" -- and
an unknown amount nobody wrote down is indistinguishable from no loss.

New `contracts/recording_checkpoint.py`. The commit order carries the
guarantee: write -> `flush(durable=True)` -> append checkpoint -> fsync the
checkpoint. The claim's fsync follows the data's flush, so a surviving
checkpoint is a promise about the data beneath it. `append_checkpoint`
returns whether its fsync succeeded, because a lost checkpoint would turn an
honest "unknown tail" into a false "all present". On recovery
`report_unconfirmed_tail` writes the boundary into `quality.jsonl` *before*
the replacement generation starts; a cleanly frozen generation writes a
`freeze` checkpoint and produces no event at all.

**Read this before touching bounded ingest again:** the target doc asks for
per-stream queue limits, but there is no internal queue -- `pull_chunk`
writes straight into the native writer under its own `RLock`, which is also
what already isolates streams from each other. The real bound is the LSL
inlet buffer (360 s), and it silently discards the oldest samples when full,
which downstream is indistinguishable from a sensor stall. New
`IngestBacklogMonitor` watches its fill ratio and emits `ingest_backlog`
edge-triggered (once on entry, once on clearing -- level-triggering would
flood the journal during exactly the minutes worth reading);
`peak_fill_ratio` rides along in every summary as positive evidence. A
marker-priority scheduler was deliberately not built: CONTRIBUTING.md §10,
and `peak_fill_ratio` will now surface the contention if it is ever real.

**Two silently-useless tests found and fixed:** the unwritable-directory
tests used an invented absolute path, which on Windows resolves under the
current drive and is creatable -- they passed without exercising a failure.
Use a child of a regular file instead; that cannot be a directory anywhere.

Full suite **893 passed, 4 skipped**; structure baseline rewritten. Plain-
language explanation of 5a/5c/5d/5h for non-coders now lives in
`docs/how-recording-quality-works.md` (per CONTRIBUTING.md §8); keep it in
step when these algorithms change.

## 5i complete (Claude, 2026-09-08)

New `runtime_core/delivery/withdrawal_service.py`: five ordered steps,
`stop_recording` -> `cancel_uploads` -> `delete_session_journals` ->
`delete_session_contents` -> `write_tombstone`. The order is a safety
property, not a preference: writers stopped and queue drained before any
deletion, so nothing publishes or re-creates a file behind the deletion.

**Three things to know before touching this again:**

*The ledger lives outside the folder it empties* (`runtime/withdrawals/`).
Inside, a withdrawal would erase its own progress record halfway through and
an interrupted run could not tell "already deleted" from "never started".

*A withdrawal leaves a tombstone, not a hole.* Contents go, the folder stays
with only `WITHDRAWN.json`. A vanished folder is indistinguishable from data
loss. **This forced a read-path change** found by reading the code rather
than assuming: `sessions_index_service` required a final marker *and* a
result payload, both of which a withdrawal deletes -- a tombstoned session
would have dropped out of the index entirely and 5a's `withdrawn=` branch
would never have been reached. `WITHDRAWN.json` is now a final marker in its
own right with a synthetic, answer-free payload.

*Published data is named, never claimed deleted.* Completed uploads sit on
someone else's server. `cancel_session()` returns them and they are written
into the tombstone so an operator knows where to go. Do not add code that
reports remote deletion it cannot evidence.

**Honest limitation:** the tombstone keeps its path, which contains the
participant folder name. Shedding that means removing the folder, which makes
the withdrawal invisible again. Recorded, not hidden.

Upload queue: terminal `cancelled` status restored by the journal replay,
queued payload files deleted (a queued job holds a second copy of the
participant's data), and three resurrection paths closed -- `_record_failure`
will not reschedule a cancelled in-flight job, `_run_job` will not mark it
done, explicit `retry(job_id=...)` refuses it.

12 new tests; full suite **905 passed, 4 skipped**; structure baseline
rewritten.

## Package A: A1, A2 and A3 complete (Claude, 2026-09-09)

Audit before starting more Phase 5 capability found 5a/5c/5h/5i had built
correct, tested machinery with **no reader anywhere**: `WithdrawalService`
and `summarize_quality_journal` had zero callers; `quality.jsonl`,
`checkpoints.jsonl` and the `lifecycle` field were written but never surfaced
in the UI. CONTRIBUTING.md section 1 rules out unreachable structure
regardless of correctness. Owner instruction: close this before 5g.

New `runtime_core/studies/session_quality_summary.py` -- the first caller
`summarize_quality_journal()` ever had. Wired into the existing
`load_session()` return value as `quality_summary`, no new endpoint. Reduces
`quality.jsonl` to a `recording_health` level, counted findings, and
`kept_up`. **Read this before touching it:** `unknown` is a fourth health
level distinct from `clean` -- a session with no journal (pre-5c) must not
report `clean`, which would repeat the exact mistake 5a's lifecycle
derivation was already written to avoid (an archived session reporting
itself `IDLE`). Findings are structured dicts (`{"kind": "gap", ...}`), not
English sentences -- the frontend already owns translation
(`t(key, fallback).replace(...)`, see `sessions-browser.js`); baking text
into Python here would duplicate and un-localize that.

**A2** landed in the same pass: a dedicated full-width "Recording quality"
panel in `admin.html` (not a 5th quarter-tile -- the existing 2x2
`status-grid--row` CSS assumes an even tile count via
`nth-last-child(-n+2)`, and a findings list needs more room than a
quarter-tile hint anyway), plus a lifecycle badge next to the session title.
Both reuse the existing generic `.status-pill` component with new
intent-color modifier classes in `main.css` -- the same shared style the
four existing status pills already use, not a second badge system.
**`SEALED` deliberately shows no badge** (it is the expected outcome; a
badge on every session is one nobody reads). Findings render via
`formatQualityFinding()` in `sessions-browser.js` using the project's
existing `t(key, fallback).replace('{placeholder}', ...)` pattern; new
locale keys added to **both** `en.json` and `de.json` (key-set parity
verified programmatically). No new JS unit test file: `sessions-browser.js`
exports only its two entry points today with no existing internal-helper
test coverage to extend consistently with.

**A3** landed in the same pass: `POST
/api/admin/sessions/<study>/<participant>/withdraw`, a thin handler over the
existing `WithdrawalService` (5i) -- its first caller. Two things worth
knowing before touching this again:

`RecordingRuntimeService._find_paths` was promoted to public `find_paths` --
reaching into a private method across the `apps/server` <-> `data_core.host`
boundary would have been worse than adding one line to its docstring
explaining the second caller. New `sessions_index_service.resolve_session_root()`
resolves "which folder" without `load_session()`'s full detail cost, which
would also raise on a session an earlier interrupted withdrawal had already
partly emptied -- a resumed withdrawal must still be able to find its
target, and it is (5i's tombstone marker keeps a withdrawn session
selectable in `_canonical_records`).

`confirm_session_id` is checked server-side, not only in the browser's
type-to-confirm modal -- a UI safeguard alone is not a validated boundary.
`already_published` destinations are rendered verbatim in the result toast,
never summarized into "handled". New route added to
`test_route_inventory.py`'s `EXPECTED_ROUTES` (that characterization test
exists to catch *unintentional* surface changes; this one is intentional).

Full suite **922 passed, 4 skipped**; `node --test tests/js` 27 passed
(unchanged); structure baseline rewritten. Full account, including the
six-file cost table for a new card type and the `card-info.js` precedent,
in the working plan's Package A / Phase 5g sections.

## 5g.B1 complete (Claude, 2026-09-09)

New `tests/support/card_type_fixtures.py` (data) and
`tests/test_card_type_fixtures.py` (checks): one golden question per type in
`ALLOWED_QUESTION_TYPES`, plus a submitted answer for every type outside
`NON_ANSWER_QUESTION_TYPES`, each run through the real
`validate_and_normalize_config`/`_results` and compared to a frozen,
hand-audited expected value -- not captured-and-trusted from the code's own
output, which would prove nothing.

**Read this before touching card-type validation:** `stimulus`'s
`plugin_actions` output is excluded from the exact-match check.
`normalize_card_plugin_actions()` reads the *live installed plugin
registry*, so that field is not a pure function of the question data --
pinning it would fail this fixture for reasons unrelated to card types (a
plugin gaining a `card_actions_schema` field). This is a real coupling
5g.B5 will need an opinion about, not an artifact of the test.

Fixture-set coverage is checked, not just content: one test catches a new
card type shipping with no fixture, another catches a stale fixture for a
removed type, a third checks the answerable/non-answerable split against
`NON_ANSWER_QUESTION_TYPES` exactly.

6 new tests, full suite **928 passed, 4 skipped** (tests/ is outside
`measure_structure.py` and `file-guide.md`'s scope, so neither needed
updating).

## 5g.B2 complete (Claude, 2026-09-09)

Closed the three genuine JS leaks the 5g.B1 audit found, plus the header
duplication. Read the working plan's rewritten 5g.B2 entry for the full
account; the two things most likely to bite someone touching this again:

`onInput`/`onClick` (slider, mood-meter) were already called
*unconditionally on every event* before this, self-filtering via a CSS
class inside each handler -- multi-slider's range inputs work today only
because they share slider's `.js-slider-input` class, with **no type-based
lookup at all**. Scoping dispatch to `CARDS[currentQuestion.type]` would
have silently broken that. Fixed with `dispatchCardHook()`: call every
distinct registered module's optional hook unconditionally, matching the
old named-import behaviour exactly. `bindDrag`/`bindCardEvents` are a
different shape (one-time per-question setup, not per-event) and became
one `cardModule.bindInteractions?.(cardElement, questionIndex)` call at the
existing render site instead.

`participant-id`'s `onInput` was already registry-based and carries its
own recursion guard against the `participantid:changed` event it
dispatches -- left untouched and explicitly excluded from the generic
dispatch rather than risk that guard.

New `tests/js/card-is-answered.test.mjs` (12 tests): this refactor touched
participant-facing logic gating study progression with *zero* prior JS
coverage anywhere. No jsdom in this project, so a minimal hand-rolled
`querySelector`/`querySelectorAll` stub stands in, matching the existing
pure-logic test style rather than adding a new one. `node --test` **39
passed** (27 prior + 12 new); Python suite unchanged (928 passed, 4
skipped -- this package touched no `.py` file).

## 5g.B3 complete (Claude, 2026-09-09)

`validation.py`'s scattered `if question_type ==` branches became two
dicts, `_QUESTION_NORMALIZERS` (13 entries) and `_ANSWER_VALIDATORS` (10 --
`ALLOWED_QUESTION_TYPES` minus `NON_ANSWER_QUESTION_TYPES`), each pointing
at a function moved verbatim out of its `if` body. Both dispatchers are now
four lines: look up, call, raise if absent.

**B1's fixtures earned their keep here**: `test_validation.py` and
`test_card_type_fixtures.py` passed against the new dispatch on the first
run, unmodified -- proof this genuinely changed nothing, on a refactor that
touched all 22 branches.

Read before adding a card type: `choice`/`single`/`ranking` deliberately
share one config-normalizer function (`_normalize_options_question`) rather
than three near-identical ones -- pinned by a dedicated test
(`test_validation_dispatch_tables.py`) so that sharing stays a choice, not
an accident. That same file also asserts both tables are complete sets
against `ALLOWED_QUESTION_TYPES`/`NON_ANSWER_QUESTION_TYPES`, so a type
added to the registry without a table entry fails a direct assertion
instead of a confusing runtime `ValidationError`.

Full suite **933 passed, 4 skipped**; structure baseline rewritten (named
functions cost a few more lines than compact `if` chains, which is the
expected and worthwhile trade).

## 5g.B4 complete (Claude, 2026-09-09)

New `tests/test_card_registry_contract.py` (6 tests): `CARD_TYPES` (JS,
read as text) cross-checked against `ALLOWED_QUESTION_TYPES` (Python),
every card module's required exports, both `validation.py` dispatch
tables, and the golden fixture set -- all four checked against the JS
registry's own type list directly, not only against each other, so
`ALLOWED_QUESTION_TYPES` itself drifting could not slip past every other
check at once.

**Deliberately no JS parser.** Reading JS source as text and matching with
regexes was already an established pattern here (`test_web_ui.py`'s
`WEB`/`_read` helpers, mirrored directly) -- a real parsing dependency for
what plain regexes already answer would be new infrastructure for no new
capability.

New "Adding A Card Type" section in `developer-guide.md`, matching the
existing "Adding A Recording Sensor" section's style: the three
registration points, when the optional `isAnswered`/`bindInteractions`/
`onInput`/`onClick` hooks are needed (dispatched generically -- never a new
named import in `study-controller.js`), and the golden-fixture step this
contract test now enforces.

Full suite **939 passed, 4 skipped**; `node --test` unchanged at 39;
structure baseline unaffected (new test file outside
`measure_structure.py`'s scope).

**Next task:** 5g.B5, the last and largest stage -- cards become real
extensions: `extensions/cards/<type>/` with a `manifest.json` (config/
answer schema, defaults) and the card's JS, delivered through the asset
route that already exists for plugin UI extensions
(`/api/plugins/<key>/assets/<path>`, `plugin_catalog.py`'s
`_validate_declared_ui_assets`). The `extensions/cards/` directory already
exists (created empty by Phase 4). Only attempt this now that B1-B4 give
it a safety net: golden fixtures, self-contained JS modules, one dispatch
table per side, and a contract test tying all three together. **Hard stop,
unchanged since the original plan:** if a card's contract cannot reproduce
`validation.py`'s exact semantics, report the incompatibility for a scope
decision -- never silently drop a card type from the approved 1.0 scope to
make a stage "succeed." Order for the rest of Phase 5: 5g -> 5j -> 5f. 3.4
remains a written, unimplemented design plan -- it blocks nothing. Claim
the package in the working plan before editing.

## Shared location and coordination

- Canonical development branch: `feature/architecture-1.0`, `C:\SR-1.0`.
- Phase 4 completion was prepared on `fix/architecture-review` in the main
  workspace's `.tmp/v1-review`, based on `8635aee`.
- Find the exact commit containing this handoff with
  `git log -1 --format=%H -- docs/architecture-1.0-handoff.md`.
- No package is currently owned. Claim the next package in the working plan
  before editing. Check `git status` and branch history first. Concurrent work
  requires separate worktrees and disjoint file ownership; never overwrite
  another assistant's unfinished files. Update these notes in every milestone
  commit with tests, remaining work and ownership.
- `main` retains operator edits in `software/study_content/settings/study_config.json`
  and `software/study_content/studies/Example Sensors Study.study-runner`.
  Preserve these; they do not belong in architecture commits.

## Completed and verified

- R1: fixed real Notion call signatures, declared allowed discovered settings,
  durable destination-ID overlays across retries, narrow patches to the latest
  matching study under its save lock. Queued scientific snapshots stay unchanged.
  Returned IDs survive a subsequent upload failure. A remote creation whose ID
  never reaches the host still cannot promise external exactly-once behavior.
- R2: effective Python 3.12 import blockers, negative controls, AST from-import
  resolution, nested DataCore area measurement and CI baseline check.
- R3: frozen resource/driver paths, isolated self-check data/environment and
  disabled background work; real harmless fixture plugin RPC in bundle CI.
- R4: physical journal append order and durable per-stream sequence, restart,
  torn-tail repair and legacy immutable archive compatibility. Runtime is the
  single journal-writer process; this is not the future recording checkpoint.
- R5: pure manifest helper closure in `contracts/manifest.py`, empty known-import
  violation allowlist. All nine comparison manifests normalize identically.
- Structure checkpoint: edges 147 to 151 reflect explicit contracts and generic
  destination checkpoint dependencies; framework LOC 3924 to 2603, contracts 1376,
  cycles 0, largest module unchanged at 2283 LOC. All metric gates stay enabled.

## Verification evidence (Windows, Python 3.12)

- Full app suite: **776 passed, 4 skipped** in 94.59 seconds.
  `python -m pytest -q -p no:cacheprovider software`
- JavaScript: **27 passed**; `node --test software/tests/js/*.test.mjs`.
- Packaging/source-release contracts: **25 passed**;
  `python -m unittest -v release_tools.tests.test_pyinstaller_common release_tools.tests.test_build_source_release`.
- `python -B tools/measure_structure.py --check` passes.
- Real Windows PyInstaller fixture-only onedir build and executable `--self-check`
  pass (exit 0): catalog, UI route, child PID and initialize/status/shutdown RPC.
  Local diagnostic logs are under the main workspace `.tmp/bundle-review/`;
  the tracked fixture and CI steps reproduce the check without those artifacts.
- Full-suite route test cleanup now waits for its status coordinator and mocks
  the device probe; release contract verifies each checkout's credential setting
  instead of assuming a fixed job count.

Set `PYTHONDONTWRITEBYTECODE=1`, `STUDY_RUNNER_DISABLE_HARDWARE=1` and
`STUDY_RUNNER_DISABLE_BACKGROUND=1` for local app tests. Windows sandbox temp
access required approved unsandboxed execution. No production instance or real
upload was used. The reduced bundle does not certify native recording/devices.

## 5b (preflight) complete — 2026-09-08

Capacity (manifest-declared stream rate x planned duration, never a measured
disk write benchmark) and clock plausibility (two hardcoded epoch bounds,
per-platform time-service evidence) enforced once at
`RecordingRuntimeService._start_worker_generation()`, the single real
worker-spawn choke point. New `shared/system_clock_probe.py`,
`backend/services/recording/recording_capacity.py` (that file has since
moved again -- it is `data_core/host/recording_capacity.py` as of Phase 4.5
below), new optional study field `planned_session_duration_minutes`, two
independent readiness blocker codes. 38 new/extended tests; full suite
**807 passed, 4 skipped**; JS **27 passed**; structure baseline rewritten as
a checkpoint (152 edges, cycles still 0). See the working plan's Phase-5b
checkbox and decision log for detail.

## User-directed course change — 2026-09-08

Operator judged incremental, fully-tested small steps too slow. Phase 3
(legacy removal) and Phase 5c-5j are deliberately deferred behind Phase 4,
the directory restructure. **Correction to the plan as first written down:**
the per-package full-suite-green gate was *intended* to be dropped for
speed, but in practice every package landed so far kept it -- see the
working plan's decision log, 2026-09-08 entries, for why (every single
package surfaced a real latent bug the full suite caught immediately: a
subprocess-blocker rule pointing at a module that no longer existed at that
path, a fresh `backend <-> data_core.host` import cycle, a stale hardcoded
test path). Moving fast was honored by keeping each package large and
shim-free instead, never by skipping the one check that kept catching
something.

## Phase 4 historical progress, 2026-09-08

Commits `dec0908`..`2d40c4a` on `feature/architecture-1.0` (in order:
`4.0`-`4.1` shim removal, `4.2` contracts/plugin_api, `4.3` data_core/contract,
`4.4` data_core/worker, `4.5` data_core/host, `4.6` runtime_core). Full detail,
including the two real design issues found and fixed during 4.5 (a
`recording_finalization_adapter.py` relocation to avoid a fresh import cycle,
and a new `shared/filename_sanitizer.py` extraction), is in the working
plan's Phase 4 section -- **read that section, not just this summary**,
before starting the next package.

Verified after every one of the six: full suite **807 passed, 4 skipped**,
`tools/measure_structure.py --check` cycle count unchanged at **0** (the
structure baseline itself is deliberately *not* rewritten mid-phase -- edge
count growth from each merge is expected and gets checkpointed once, at the
end of Phase 4, same as the one checkpoint after Phase 2).

**Next task, in order: `extensions/*` (item 4.7), then `apps/ui` (4.8), then
`apps/server` (4.9), then the tail items 4.11-4.16.** Claim the package in
the working plan's file-ownership table before starting. The mechanical
pattern is identical each time (see the six commit messages for the exact
shape): write a short throwaway Python script doing a literal
`str.replace()` on the dotted import path across `software/` (and `tools/`,
`release_tools/` where relevant) → `git mv` the directory → run the script →
grep for anything the literal replace could not reach (relative `from ..x
import` forms always need a manual pass; check both single- and double-dot
depending on the importing file's own location) → fix
`test_import_boundaries.py`'s `RULES` tuple and
`test_architecture_invariants.py`'s area/prefix checks if the moved package
touches either → run the full suite → fix forward → commit. `extensions/*`
specifically also needs: reading each of the six plugin manifests'
`category` field before moving anything (do not guess the destination
subfolder from the plugin's name), and very likely a same-or-next-commit
touch to `_MOVED_PLUGIN_PATHS` in `runtime_core/settings/hardware_settings_service.py`
(T7) plus the two dynamic import sites in `driver_runtime.py:28` and
`plugin_catalog.py:28`/`:31` (item 4.11) -- operator-stored plugin paths on a
real field machine's `hardware_settings.json` will break otherwise.

Phase 3 migration, lifecycle/QC/timing/checkpoint recovery, withdrawal, cards,
SDK and CLI remain open behind Phase 4. Hardware, power-loss measurement, full
bundle/upgrade, licences and platform release gates remain open. Version is
still 0.7.0; this checkpoint does not declare v1.0 complete.


## Approved completion package - completed 2026-09-08

Owner: none. Codex completed `fix/architecture-review`, based on `8635aee`; integrate verified
commits into `feature/architecture-1.0` at `C:\SR-1.0`. No concurrent file owner.
Implement extensions by category, then apps/ui and apps/server; keep server.py
and app_server.py entrypoints. Separate preflight UI/validation changes from moves.
Actual initial structure check FAILS: edges 152 to 229, contracts 1376 to 1515 LOC;
zero cycles alone is not a green check. Review and checkpoint once at completion.
Required final evidence: Python/JS/release suites, architecture gates and actual
Windows fixture bundle startup/RPC. Planned duration needs a settings input.
Do not touch operator edits on main, including study.html and main.css.

Checkpoint: sensors moved; shared trusted-root discovery/driver/UI asset resolution
and category-aware packaging added. Targeted catalog/process/camera/path tests:
76 passed. Other categories remain on their original paths until their commits.
Next: destinations, then outputs. Owner remains Codex.

Checkpoint: destinations moved; catalog, real fake-client Notion path, retry and
hardware-path suites: 69 passed. Next: outputs and final multi-root tests.

Checkpoint: outputs moved and the obsolete `plugins` package removed. Discovery,
driver startup, UI assets and self-check now share the trusted category-root
resolver. Packaging scans categorized manifests and CI stages its harmless
fixture under sensors. Targeted Python tests: 84 passed; packaging: 4 passed;
targeted JavaScript: 4 passed. Cross-category conflict coverage added. Next:
move `frontend` to `apps/ui`.

Checkpoint: `frontend` moved to `apps/ui`; runtime and PyInstaller data targets,
source-release licences and all browser-test imports follow the new path. Public
HTTP routes are unchanged. Evidence: 73 targeted Python, 27 JavaScript and 25
release/packaging tests passed. Next: move the Flask factory and routes to
`apps/server`, retaining both promised entrypoints.

Checkpoint: Flask factory, routes and server runtime moved to `apps/server`.
`software/server.py` and `study_runner/app_server.py` remain stable entrypoints;
the latter delegates and still supports module execution. The obsolete backend
package is gone. Evidence: 80 targeted route/server/architecture/self-check
tests and 4 packaging tests passed. Next: preflight settings UI and defensive
capacity errors in a separate behavior commit.

Checkpoint: the study settings now expose planned session duration, preserve it
through save/export/import normalization and focus that field when capacity
blocks start. Invalid user values are rejected locally. Capacity evaluation now
fails closed for non-finite/invalid rates and unreadable storage instead of
raising. Localized capacity/clock explanations added. Evidence: 109 targeted
Python tests and the focused JavaScript contract passed; locale JSON parses.
Final checkpoint: path and file-guide validation, version `1.0.0-dev`, all test
suites, structure check, and the real Windows fixture bundle pass. Next: the
deferred Phase 3 compatibility/plugin-contract package.
