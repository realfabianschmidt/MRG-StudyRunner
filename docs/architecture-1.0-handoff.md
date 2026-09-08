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

**Next task:** implement 3.4 per that plan (or revise it first if you
disagree with the recommendation), then Phase 5 **in full** (operator
decision, 2026-09-08: keep the complete target scope including `mrg` CLI 5f
and the extension SDK 5j, rather than trim against CONTRIBUTING.md's "keep
it simple" guidance — see the working plan's decision log). Order for
Phase 5: 5e -> 5d -> 5a -> 5c -> 5h -> 5i -> 5g -> 5j -> 5f. Claim the
package in the working plan before editing.

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
