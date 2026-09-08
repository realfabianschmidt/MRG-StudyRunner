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

## Next task: early preflight (5b)

1. Claim 5b in the working plan. Inspect existing preflight and start routes.
2. Define explicit planned-duration and acquisition-byte-rate inputs and their
   provenance. Disk throughput is not acquisition rate. Unknown duration/rate
   must not produce a successful capacity prediction.
3. Implement capacity plus reserve, required-stream and clock plausibility
   results with actionable reasons; enforce them at the actual start boundary.
   A running time service alone does not prove clock correctness.
4. Test sufficient/insufficient space, missing/invalid inputs, missing required
   streams and implausible clocks. Update plan, handoff and commit evidence.

Phase 3 migration, serial Phase 4 moves, lifecycle/QC/timing/checkpoint recovery,
withdrawal, cards, SDK and CLI remain open. Hardware, power-loss measurement,
full bundle/upgrade, licences and platform release gates remain open. Version
is still 0.7.0; this checkpoint does not declare v1.0 complete.
