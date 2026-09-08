# Architecture 1.0 - shared Claude/Codex handoff

Updated: 2026-09-08. Read this file and [the working plan](architecture-1.0-umbau.md)
before continuing. Both are tracked repository files, accessible to either
assistant through the local checkout; no private assistant memory is required.

## Shared location and coordination

- Canonical development branch: `feature/architecture-1.0`, `C:\SR-1.0`.
- Repairs prepared on `fix/architecture-review` in the main workspace's
  `.tmp/v1-review`, based on `c557cd2`; documentation approval commit `ce4adcd`.
- The commit containing this handoff completes R1-R5. Find its exact ID with
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

## Phase 4 progress — 6 of 10 packages done, 2026-09-08

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


## Approved completion package - active 2026-09-08

Owner: Codex, `fix/architecture-review`, based on `8635aee`; integrate verified
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
