# Architecture 1.0 Rebuild — Working State

Shared continuation notes and current ownership: [Claude/Codex handoff](architecture-1.0-handoff.md).

This is the shared working document for the 1.0 architecture rebuild. Two agents
(Claude Code and Codex) work on it in parallel, so **this file is the single
source of truth for what is done, what is in progress, and who owns which
files.**

The target architecture is preserved in [the initial plan](architecture-1.0-initial.md)
(German). That file is the unchanged input. This working document records
approved deviations, implementation progress and acceptance evidence.

## How to use this document

1. **Before starting a work package**, add yourself to
   [File ownership](#file-ownership). If someone else owns the package, pick a
   different one.
2. **Tick a checkbox in the same commit as the change it describes.** Not
   afterwards, not in batches. A checkbox that lies is worse than no checkbox.
3. **If you deviate from the planned order, write it down** under
   [Decision log](#decision-log) with the date and the reason. Nobody can
   reconstruct it later otherwise.
4. **Read [Traps](#traps) before touching anything.** Every entry there is a
   verified way this rebuild breaks silently — meaning CI stays green and the
   damage shows up weeks later on a field machine.

Status, 2026-09-08: **Phase 0 merged to `main` (`a1f39d9`); Phase 1, Phase 2
and 5b (preflight) implemented, including approved repairs R1-R5. Phase 4
(directory move) is complete.** The final packages are `extensions/*`,
`apps/ui`, and `apps/server`; the old `plugins`, `frontend`, and `backend`
packages are gone. Tail items 4.11-4.16 are complete and version is
`1.0.0-dev`. **Phase 3 items 3.1/3.2/3.3/3.5 also complete** (3.2 was found
already implemented pre-dating this rebuild, verified rather than built —
see Phase 3 section); **only 3.4 remains in Phase 3**, then Phase 5 in full
(operator decided 2026-09-08 to keep the complete target scope, including
`mrg` CLI and the extension SDK, rather than trim it against
CONTRIBUTING.md's "keep it simple" guidance). **Phase 5's first two
packages 5e (journal/XDF event-id comparison), 5d (stream contracts +
timing provenance), 5a (session lifecycle), 5c (live quality/timing
journals), 5h (recording checkpoints + bounded ingest) and 5i (withdrawal
workflow) are also complete** — see Phase 5 section. **Package A (visibility
before more capability) started 2026-09-09**: 5a/5c/5h/5i built correct
machinery with no UI reader at all (`WithdrawalService` and
`summarize_quality_journal` had zero callers) — see "Package A" below.
**Package A (A1/A2/A3) and 5g are complete. All 13 question types now
come from 12 process-isolated card extensions discovered through the existing
API-v4 plugin framework; `choice` and `single` deliberately share one
extension. Package 5g.B5 completed 2026-09-09** — see the rewritten 5g entry.
Then
5j, 5f in that order (3.4 is a written,
not-yet-implemented design plan, see Phase 3 section — it can land
whenever convenient, it blocks nothing in Phase 5).
The shared rebuild is `feature/architecture-1.0`, worktree `C:\SR-1.0`.
Corrections were prepared on `fix/architecture-review`; see the tracked handoff
for integration and verification evidence. **User-directed course change,
2026-09-08:** Phase 3 (legacy removal) and the remaining Phase 5 packages
(5c-5j) are deliberately deferred — priority moved to Phase 4 (directory
restructure), executed with a lighter per-package gate than originally
planned (see the note at the top of the Phase 4 section for exactly what
changed and why) because the operator judged incremental, fully-tested small
steps too slow relative to the goal. Temporary breakage between package
moves is accepted; `main` still only receives the result once Phase 4's
suite is green again, in one merge — every package landed so far has in
fact kept focused or full suites green. Final Phase 4 evidence is 814 Python
tests passed/4 skipped, 27 JavaScript tests, 25 release tests, a green
structure check, and a real Windows fixture-bundle self-check.
The copy on `main`
is a foundation snapshot with a pointer here, not a second independently
maintained progress checklist.

---

## Scope and release shape

One release, `1.0.0`, full scope. The phases below are internal milestones, not
releases.

Estimated at 9–14 weeks for the full scope, excluding gates no agent can
perform (hardware smoke test, plug-pull test, licence decision).

**Phase 0 lands on `main`, everything else on `feature/architecture-1.0`.**
Phase 0 is purely behaviour-preserving — CI hardening, path-resolution fixes,
regression fixtures — and needs no version bump. This keeps `main` releasable so
a field bug can still be hotfixed during the rebuild. Fixes go onto `main` first
and are cherry-picked onto the branch, never the other way round.

---

## Decisions already taken

| # | Decision | Rationale |
|---|---|---|
| D1 | **`study_runner` stays the single importable top-level package.** The 1.0 structure is nested *inside* it, not placed beside it as `software/{apps,packages,extensions}`. | Every invariant in §14 of the target document is about **import edges**, and edges are enforceable in any layout. The literal top-level layout would break `collect_submodules("study_runner.apps.server")`, the single `sys.path` entry in `software/tests/conftest.py`, `DEFAULT_PACKAGE_NAME`, 328 `study_runner.` references across 66 test modules, and the `(root/"study_runner").exists()` software-root probe in the PyInstaller spec — in exchange for a nicer directory listing. **This deviates from the target document; §3 there should be corrected.** |
| D2 | **Legacy flat result folders are NOT removed** (contra target doc §13). | `software/saved_results/Example_Sensors_Study/` is in that shape on disk right now, as is every session recorded before the layout change. A major release that removes the ability to read a researcher's earlier data is not cleanup. Keep the read path, pin it with a fixture test, and rename it in the docs from "legacy" to "archival compatibility surface". Remove only remaining *write* paths. |
| D3 | **`upload_destination.legacy` is migrated before it is removed.** | It reads fields out of live operator `.study-runner` files. Order: migrate and write forward → ship one release → then remove the read path. |
| D4 | **Work happens in a second worktree**, `git worktree add C:\SR-1.0 -b feature/architecture-1.0`, with an external data directory. | The production install must keep working. See [Development environment](#development-environment). |
| D5 | **No compatibility shims for moved modules**, with one exception: `software/server.py` and `study_runner/app_server.py` stay permanently, and the **built executable name never changes** (`study-runner-server` / `study-runner-server.exe`). | Every consumer of the old paths is in-repo and editable. A shim would re-introduce exactly the import edge the rebuild removes, and would defeat git rename detection. `server.py` is not a shim — it is the documented entry point baked into the install/start scripts and the PyInstaller root probe, and the packaged app re-invokes *itself* with `server.py --flags`. The executable name is a separate, harder constraint: the 0.7.0 updater already deployed in the field finds the new build by literally globbing for that filename (see Phase 0.6) — nothing in the 1.0 codebase can fix a 0.7.0 client that already shipped. |
| D6 | **Target layout (per D1):** | `study_runner/{apps/{server,cli,ui}, contracts, runtime_core, data_core/{contract,host,worker}, plugin_framework, extensions/{sensors,cards,destinations,outputs}, shared, updates}` |
| D7 | **`study_runner/updates/` does not move in Phase 4.** | See Phase 0.6 findings below — it is the one path a live field machine actually executes with 0.7.0 code during an update, moving it buys no architectural benefit and adds real risk. |

---

## Approved review corrections — 2026-09-08

The user approved this package before implementation. Keep the 1.0 scope;
repair the foundations before further broad restructuring. The original
9–14 week estimate is a planning estimate, not current completion evidence.

### Immediate repair packages

- [x] **R1 — Upload correctness.** Repair both Notion call signatures. Exercise
  the real publish-to-adapter path with a fake destination. A destination
  reports a narrow allowed settings patch; the host merges into the latest
  matching study under the existing save lock/revision transaction. Never
  restore an entire queued study snapshot as the active study. Persist discovered
  destination identity independently of subsequent upload success and reuse it
  across retries/older queued jobs for the same study and destination settings.
  Preserve the queued scientific configuration as immutable session evidence.
- [x] **R2 — Effective architecture checks.** Replace the ineffective Python 3.12
  import blocker, add a negative control, resolve `from package import module`
  and relative equivalents, and run the structure check in CI. Keep host/worker
  boundaries visible after they share the `data_core` parent. Structural baseline
  changes need an explained checkpoint; a field-name denylist is only a tripwire,
  not proof of timing semantics.
- [x] **R3 — Packaged runtime.** Resolve frozen resources without requiring a
  source checkout or physical Python entry scripts. Run a bundled harmless
  plugin through its process protocol. Self-check always overrides the data
  directory, prevents background runtime work and restores its environment.
  Source tests and a plugin-free bundle alone cannot demonstrate plugin startup.
- [x] **R4 — Durable journal order.** Recovery follows append order/a durable
  sequence, never wall-clock ordering. Preserve old journals, account for
  restarts and torn tails, and test backwards/forwards clock jumps. Do not claim
  that this audit-journal fix implements the recording checkpoint protocol below.
- [x] **R5 — Finish Phase 2.4 before moves.** Extract manifest normalization and
  its pure dependency closure into `contracts`, remove the two remaining known
  violations, then require an empty allowlist. Temporary re-export modules have
  explicit removal work in Phase 4; they are not the final package contract.

Sequence: update this document → R1–R4 → R5 → early preflight (5b) → serial
package moves → remaining recording/lifecycle/extensions work → release gates.
Independent repairs may run in parallel with exclusive file ownership; directory
moves and tree-wide import rewrites remain serial.

### Ownership of durable state

| Concern | Authoritative owner | Other components |
|---|---|---|
| Study configuration, semantic session/card events | RuntimeCore | UI/CLI submit commands; destinations return limited observations |
| Semantic event journal | RuntimeCore, one append API | Acknowledgement follows durable append; the same `event_id` is sent through LSL |
| Recording lifecycle, segment checkpoints, ingest QC/timing | DataCore | RuntimeCore requests transitions and reads the recording state |
| Scientific validation, merge and sealing | DataCore | RuntimeCore schedules the work; it cannot assert `SEALED` independently |
| Finalization and publication job progress | RuntimeCore | Distinct from recording/session state; failed upload does not unseal valid data |
| Withdrawal orchestration | RuntimeCore | DataCore stops writers; delivery cancels jobs; deletion is replayable |

The initial plan's “only the Core writes the session” is refined to these
explicit owners. Extensions never directly mutate session artifacts or study
configuration. Each durable journal has one defined append interface. Split
scientific sealing out of the current `delivery` services when assigning final
package locations; do not mechanically move all finalization responsibility
into RuntimeCore.

### Requirements traced to acceptance

| Initial requirement | Work package | Required evidence |
|---|---|---|
| §2, §8 durable acquisition | 5h checkpoints/recovery ✅ | Inject failure before/after data flush and journal commit; recover only confirmed prefix; report gaps |
| §6 honest timing | 5d timing contract + 5c live observations | Source/receive/LSL clocks distinguished; delay provenance required including `unknown`; detect clock jumps |
| §7 journal/LSL event parity | 5e | Identical IDs, replay deduplication and persisted mismatch evidence |
| §8 bounded queues/writer isolation | 5h ✅ (see the package note: no internal queue exists; the LSL inlet buffer is the real bound and is now observed) | Saturate one stream without silently losing/blocking unrelated streams; record drops |
| §9 preflight and versioned QC | 5b, 5c | Disk reserve, required stream, clock plausibility failures; versioned thresholds |
| §10 lifecycle/seal | 5a | Transition table; sealing requires verified artifacts; upload failure preserves seal |
| §10 withdrawal | 5i | Stop writers/jobs; repeatable deletion of raw, derived and runtime journal copies; destination disposition recorded |
| §11 extension SDK | 5j | Versioned schemas, validator, fake runtime, synthetic LSL source, one template per type |
| §12 maintenance CLI | 5f | Shared online command guards; offline writes refuse a live runtime and require exclusive maintenance lock |
| §13 compatibility | 3.1–3.3, 6.2–6.3 | Existing studies migrate and recordings remain readable; archival discovery/browser promises tested separately |
| §14 invariants/structure | R2, R5, 4, 5a | Negative controls; empty import allowlist; CI structure check; sealed-session validation |
| §16–17 release decisions | 6 | Licence/platform decisions, real upgrade, hardware smoke and measured power-loss test |

## Target package mapping

```text
study_runner/apps/server/       <- backend/routes/ + app_server.py
study_runner/apps/cli/          <- NEW (mrg)
study_runner/apps/ui/           <- frontend/
study_runner/contracts/         <- NEW: from plugin_api.py, shared/, manifest validation,
                                   recording/errors.py, worker protocol types, CoreProbe
study_runner/runtime_core/      <- backend/services/{studies,settings,delivery}
study_runner/data_core/contract/ <- recording/{worker_protocol,artifacts,errors}, backup projection model
study_runner/data_core/host/    <- backend/services/recording/ + recording/{coordinator,recovery,worker_binary,xdf}
study_runner/data_core/worker/  <- recording_worker/{application,lsl_recording,runtime,core}
study_runner/plugin_framework/  <- plugin_framework/ minus plugin_api.py
study_runner/extensions/        <- plugins/, split by each manifest's `category` field:
                                     biosignal -> sensors/    storage -> destinations/
                                     output    -> outputs/    (cards/ is new)
study_runner/shared/            <- shared/ + runtime-config helpers
study_runner/updates/           <- UNCHANGED, do not move (see Phase 0.6 findings)
```

`software/recording_worker/native/` (the C++ core) stays where it is — it is not
a Python package and `tools/setup_recording_worker.py` plus the `recording-core`
CI matrix reference that path.

**Vocabulary rule:** package names move, domain vocabulary does not. `recording`
stays in capability names (`recording_source`), session files
(`recording-plan.json`, `recording-lease.json`) and schema IDs
(`study-runner/recording-plan/v1`). Renaming schema IDs would break the
readability of already-recorded sessions.

---

## Baseline inventory (verified before Phase 1; see status and R1–R5 for progress)

### Already built — move it, do not rebuild it

All nine building blocks the target document §2 lists as "built and tested" do
exist, several in better shape than the document implies:

| Capability | Where |
|---|---|
| Native C-ABI around pinned LabRecorder XDFWriter v1.17.1 | `software/recording_worker/native/` (`UPSTREAM_LOCK.json` pins tag + commit + per-file SHA-256) |
| Fail-closed core locator | `recording/worker_binary.py` — checks ABI version, binary SHA-256 against `worker-build.json`, byte order, and 8 named `REQUIRED_CANONICAL_FEATURES` |
| Segmentation (~10 s boundary, ≥5 s durable flush) | native `write_boundary`/`flush`, `recording/coordinator.py` `SegmentLedger` |
| Two-stage lossless merge | `sr_xdf_merge_files` + `recording_worker/runtime.py` |
| Merge validation, pinned PyXDF, raw mode | `recording/xdf.py` `PyXdfInspector.validate_merge_parity` — compares metadata, sample count, full timestamp sequence, clock-offset chunks, normalized data hash |
| Append-only fsynced session journal | `backend/services/studies/session_journal_service.py` |
| Lease, generation fencing, idempotent `command_id` | `recording/recovery.py`, `recording/worker_protocol.py` |
| Slowest-grid backup projection | `recording/backup.py`, `recording_worker/lsl_recording.py` `BackupRecorder` |
| Journaled 9-step finalization | `backend/services/delivery/finalization_service.py` |
| Process-isolated plugins (`study-runner-stdio/v1`) | `plugin_framework/process_host.py` + `driver_runtime.py` |

### Does not exist yet — genuinely new work

`quality.jsonl` · `timing.jsonl` · `stream-contracts.json` · preflight disk-space
check · preflight system-clock check · session lifecycle enum
(`IDLE→PREFLIGHT→RECORDING→FINALIZING→SEALED`) · `WITHDRAWN` · shared `event_id`
dedup between journal and XDF · stream contract in the XDF header ·
`contracts/` package · `mrg` CLI · **card extensions**

### The 11 import edges that violate the target invariants

| Edge | Sites |
|---|---|
| host ↔ worker, both directions | `recording/worker_binary.py:18` → `recording_worker.core`; `recording_worker/application.py:13`, `runtime.py:15-16`, `lsl_recording.py:16` → `recording.*`; `backend/services/recording/recording_dependencies.py` |
| extensions → backend | `plugins/notion_upload/adapter.py:23` (module-level), **`:854`, `:874-877` reads *and writes* study configuration**; `plugins/brainbit/adapter.py:169,179,262` + `plugin.py:35`; `plugins/camera_emotion/worker/plugin.py:748` |
| plugin_framework → backend | `plugin_framework/plugin_api.py:79`, `dependency_utils.py:68` |
| data_core → plugin_framework *(missing from the target doc's invariant list)* | `recording/markers.py:26-27`, `recording/clock_diagnostics.py:17-18` |

Eight of the eleven are **function-local** imports, so a string-matching test
will not find them. Use an AST walker.

`notion_upload/adapter.py:854-877` is not a constant move. A destination
extension reads and writes study configuration, which target doc §11 forbids in
as many words. It needs a new `study-runner-stdio/v1` capability. **Invariant #2
is unreachable until that is designed.**

---

## Traps

Every entry here is verified. Each one breaks *silently* — CI stays green.

### T1 — `parents[N]` path arithmetic does not raise when it is wrong

`backend/services/settings/runtime_config.py:47` is `Path(__file__).resolve().parents[4]`.
Today that resolves to `software/`. After the move it resolves somewhere else,
and `get_project_base_dir()` then points `study_content/`, `saved_results/`,
settings, SSL certs and branding one directory too high. **No exception. No
failing test.** It presents as "where did all my studies go", on a field machine,
after an auto-update.

Other sites: `plugins/camera_emotion/worker/plugin.py:63`,
`plugins/mr60_mini_radar/tools/ble_mr60_receiver.py:24`,
`plugin_framework/process_host.py:203`.

The fix pattern already exists in
`release_tools/pyinstaller/study_runner_server_common.py:16-22` — walk upwards
looking for `server.py`. Copy it. **Do this before the first `git mv`.**

### T2 — Two tests go blind instead of red when directories move

- `software/tests/test_file_guide.py:52` matches `path.name` — the **basename**.
  Moving a file changes nothing it looks at, so moves pass. And `SOURCE_DIRS`
  (`:17-21`) is filtered by `if not root.exists(): continue` (`:34-35`), so once
  `software/study_runner/` no longer exists under that name the test scans only
  `tools/` and `release_tools/` and **passes vacuously**.
- `software/tests/test_area_boundaries.py:80` rglobs
  `PROJECT_ROOT/"study_runner"/"shared"`; a nonexistent directory yields zero
  offenders, i.e. green.

Fix both to assert their roots exist.

### T3 — Narrowing `SUPPORTED_PLUGIN_API_VERSIONS` to `(4,)` breaks the server

`recording/markers.manifest.json:2` and `recording/clock_diagnostics.manifest.json:2`
are `api_version: 3`. They are loaded at **module scope**
(`recording/markers.py:36`) and `backend/__init__.py` imports both, so
`create_app()` becomes unimportable and every route test dies at import.

Bump both manifests **in the same commit** that narrows the tuple.

### T4 — `entry_point` cannot be removed before the v3 import path

`plugin_catalog.py:654-665` (`_import_plugin`) reads it. Order: remove
`_import_plugin` → make `entry_point` optional (`:243` is `_required_text`
today) → delete the key from manifests one release later. Making it optional
rather than deleting it immediately keeps an operator-edited manifest loading.

### T5 — PyInstaller is built by **no** workflow, and the test that would catch a rename does not run

`.github/workflows/release.yml` has jobs `source`, `source-quality`,
`recording-acceptance`, `publish` — no PyInstaller job. The bundle is built by
hand on the developer machine.

`release_tools/tests/test_pyinstaller_common.py` pins exactly the strings a
rename breaks (`"study_runner/apps/ui"`, `"study_runner.apps.server"`), but
`ci.yml:68` runs only `release_tools.tests.test_build_source_release`, and
`python -m pytest software` never reaches `release_tools/`. **The test is dark.**
Turning it on is one line and it is the highest-value action available before
any move.

### T6 — LSL `source_id` collision destroys the *production* recording

`recording/markers.manifest.json` pins `"source_id": "study_runner.markers"`;
`clock_diagnostics.manifest.json` pins `"study_runner.clock_diagnostics"`. Only
`stream_name` is configurable. `recording_worker/lsl_recording.py:398-407` does
`resolve_byprop("source_id", …)` and raises `RuntimeError("source_id … is not
unique")` when more than one matches.

Two Study Runner instances on one machine therefore break recording in **both** —
including the production one — and only at the moment someone presses record.
`STUDY_RUNNER_DISABLE_HARDWARE=1` in the dev instance is **mandatory**, not
optional. Port 3001 is hardcoded for the emotion worker, and BLE/camera are
exclusive devices.

### T7 — operator-stored plugin paths break on a folder move

A field machine's `hardware_settings.json` contains literals like
`"script_path": "study_runner/plugins/brainbit/brainbit_realtime_cli.py"`.

The precedent is already in the repo:
`backend/services/settings/hardware_settings_service.py:26-29`
`_MOVED_PLUGIN_PATHS` exists **because the last folder rename**
(`integrations/` → `plugins/`) did exactly this. Add the new pairs. And do not
additionally rename plugin folders to match their `plugin_key` — every rename is
a migration on someone else's installation.

### T8 — the stream contract in the XDF header changes recorded bytes

Merge parity validation compares stream metadata. Extend the existing
`desc/study_runner/…` namespace rather than opening a second one, and update
fixtures plus `tools/make_timeline_fixture.py` in the same commit.

---

## Phases

### Phase 0 — Foundation (on `main`, before the branch exists)

- [x] **0.0** Create this document, link it from `docs/README.md`
- [x] **0.1** Record the import-root decision (D1) and confirm no
      packaging metadata is needed — there is no `pyproject.toml`, `setup.py`,
      `setup.cfg` or `pytest.ini` in the repo today; imports work via
      `software/tests/conftest.py:33` `sys.path.insert(0, software/)`
- [x] **0.2** CI hardening: `release_tools.tests.test_pyinstaller_common` now
      runs in `verify`; `- "feature/architecture-**"` added to the push
      trigger; new `packaging-smoke` job builds a plugin-free bundle on Linux
      and runs it with a new `server.py --self-check` flag
      (`study_runner/self_check.py`: asserts `static_folder` resolves,
      `pages/study.html` is servable, `discover_plugin_catalog()` does not
      raise, `GET /` returns 200 — runs with hardware disabled against an
      isolated temp data dir).

      **This caught a real, pre-existing bug on the first run**, unrelated to
      the rebuild: `common_datas()` never bundled
      `recording/markers.manifest.json` or
      `recording/clock_diagnostics.manifest.json`. Both are read via
      `Path(__file__).parent / "*.manifest.json"` at runtime, not imported, so
      PyInstaller's static analysis never found them — a real bundle crashed
      at `create_app()` before this job existed to notice. Fixed in
      `study_runner_server_common.py::common_datas` (now raises loudly if
      either is missing) and pinned in
      `test_pyinstaller_common.py`. Verified against an actual built onedir
      bundle, not just the unit tests, before landing.
- [x] **0.3** Replace `parents[N]` with an upward marker search — 4 sites, see [T1](#t1--parentsn-path-arithmetic-does-not-raise-when-it-is-wrong).
      New `study_runner/shared/software_root.py` (`find_software_root`,
      `is_software_root`); used by `runtime_config.py`, `process_host.py` and
      `camera_emotion/worker/plugin.py`. `ble_mr60_receiver.py` keeps a local
      copy — it is a bootstrap that makes `study_runner` importable and so
      cannot import from it. `process_host.py` resolves from its own
      `__file__`, not from the plugin directory, because the PYTHONPATH entry a
      driver needs is the root that makes `study_runner` importable and a
      plugin folder may sit at any depth. Pinned by
      `software/tests/test_software_root.py`, including an AST check that no
      watched module reintroduces `parents[N>=2]`
- [x] **0.4** Golden regression fixtures. Audited what already existed before
      adding anything:
      - **Flat legacy result — was a real gap, now closed.** A completed
        0.6-era result in this shape exists on disk right now
        (`software/saved_results/Example_Sensors_Study/…`), but it was never
        git-tracked and no test exercised the read path. Added
        `software/tests/fixtures/legacy_flat_result/` (a synthetic,
        git-tracked stand-in) and
        `software/tests/test_legacy_flat_result_compat.py`, which pins both
        halves of the current, correct behavior: `recovery_service` still
        recognizes the identity so it never re-offers an already-saved
        session as a crash-recovery candidate, and the canonical browser
        (`sessions_index_service.list_sessions`) still ignores it.
        **Correction to my own earlier notes:** `study_readiness_service.py:80`
        is *not* part of this — it normalizes legacy flat *study-config*
        plugin fields (`<plugin>_enabled` → `study_settings.plugins.<key>`),
        a different, already-well-tested legacy concept unrelated to result
        folders. Don't conflate the two when doing Phase 3.3.
      - **Canonical session** — already covered:
        `software/saved_results/Demo_Completed_Study/` is git-tracked and
        contains a real `derived/session.xdf`. No action needed.
      - **`upload_destination.legacy` / `_MOVED_PLUGIN_PATHS`** — already
        covered by direct unit tests
        (`test_study_plugin_config.py`, `test_plugin_catalog.py`,
        `test_hardware_settings_service.py`) that build the exact legacy
        shapes in memory and assert the migration. A binary `.study-runner`
        fixture would test the same code path redundantly; skipped.
- [x] **0.5** Make the blind tests loud — see [T2](#t2--two-tests-go-blind-instead-of-red-when-directories-move).
      `test_file_guide.py` gained `test_source_roots_exist`;
      `test_area_boundaries.py::test_shared_depends_on_no_area` now asserts its
      directory exists before rglobbing it. Both verified to go red when a root
      is renamed
- [x] **0.6** Investigated the update path. Traced what actually executes
      during a real update, since a field machine runs **0.7.0 code** for
      most of it — the rebuild cannot patch that retroactively:

      1. 0.7.0's `update_service.check_for_update` fetches the manifest,
         `download_and_stage_update` verifies the signature and extracts the
         zip to `<storage_root>/updates/staged/<version>/`.
      2. `_find_staged_executable` locates the new build by
         `stage_dir.rglob("study-runner-server.exe")` (or the extensionless
         name on POSIX) and picks the shallowest match — **it does not care
         about the internal directory layout of the zip**, only the
         executable's filename. This survives the 1.0 restructure completely,
         as long as D5 holds.
      3. `_spawn_installer` re-launches the **currently running 0.7.0**
         executable with `--apply-update <state_file>` — this is 0.7.0's own
         `server.py --apply-update` branch, already deployed, already frozen.
         It calls `study_runner.updates.installer.main()`, which reads the
         staged executable path from the state file and launches it directly
         with `_spawn_detached`, passing environment variables:
         `STUDY_RUNNER_DATA_DIR`, `STUDY_RUNNER_APP_MODE=packaged`,
         `STUDY_RUNNER_HOST`, `STUDY_RUNNER_PORT`, `STUDY_RUNNER_HTTPS`.

      **Findings:**
      - The only hard compatibility surface is: the built executable's
        filename (D5, now explicit about this), the zip being flat enough for
        `rglob` to find that filename at all, and those five env var names
        being read the same way by `runtime_config.py` on the 1.0 side. None
        of this depends on the internal `study_runner` package layout — the
        Phase 4 restructure is free to happen without touching the update
        path's compatibility contract.
      - **Real gap found and fixed:** `study_runner/updates/` (`installer.py`,
        `signatures.py`, `trusted_keys.py`) was missing entirely from the
        target package mapping above. It is the one path a live field machine
        actually executes with old code during an update. Decision D7: it
        does not move in Phase 4. Small (3 files), self-contained, touched by
        no invariant, and moving it would buy nothing while adding the one
        risk this whole investigation exists to avoid.
      - No 0.7.x fix is needed before the rebuild — the assumptions above
        are already stable across the versions checked.

### Phase 1 — Invariant harness (branch) — **implemented and repaired**

- [x] **1.1** AST-based import walker at `software/tests/support/import_graph.py`
      (`ImportEdge`, `iter_imports`, `iter_python_files`, `module_area`,
      `file_area`). Catches function-local imports (8 of the violations below
      are function-local) and resolves relative imports (`from .x import y`)
      against the importing file's own package, matching Python's own
      resolution. Kept `test_area_boundaries.py`'s subprocess-blocker
      technique unchanged — it catches transitive edges no static AST walk
      can see.

      **Two real bugs found while actually running this, not just reading
      it:** (a) `ast.parse` raised on a BOM in
      `plugins/camera_emotion/adapter.py` — fixed by reading with
      `utf-8-sig`. (b) `file_area()` treated a file sitting directly in
      `study_runner/` (`__init__.py`, `version.py`, `app_server.py`,
      `self_check.py`) as belonging to a fictitious area named after itself
      — crashed `measure_structure.py` the first time something iterated
      *files* rather than checking one already-known path. Fixed by requiring
      3 path segments, not 2, for a file to have an area. `module_area` has
      the same shape of ambiguity for dotted module strings but no
      filesystem access to resolve it; documented rather than silently
      "fixed" with a guess, since every current caller already intersects
      the result against a real directory listing before treating it as a
      real area — verified this precisely to avoid re-introducing (b) by a
      different door.
- [x] **1.2** `test_import_boundaries.py` — a `KNOWN_VIOLATIONS` allowlist,
      not a red test. **Discovered mechanically, not hand-transcribed** —
      the hand-written list in my own planning notes had 17 pairs and missed
      one real edge (`notion_upload/adapter.py` also imports
      `study_plugin_config`, not just `study_config_service`). The
      mechanical scan found **18** distinct `(file, module)` pairs, plus a
      19th edge invariant #1's own wording doesn't mention:
      `backend/services/recording/recording_runtime.py` imports
      `recording_worker.lsl_recording` directly — the future data_core
      *host* importing the future data_core *worker*, from the host side.
      `RULES` therefore uses path-prefix matching
      (`backend/services/recording/` specifically, not all of `backend`),
      not just top-level area matching, so this edge could be expressed
      before Phase 4 physically separates the two. Verified both failure
      modes actually fire (new violation added / known violation silently
      fixed-but-not-removed) with a scripted before/after check, not just by
      reading the assertions.
- [x] **1.3** Invariants already true, now green regression locks:
      - #5: `test_architecture_invariants.py::OnlyDataCoreWritesXdfBytesTests`
        — only `recording_worker/` may import `NativeXdfWriter` (not just
        "the recording area", since `recording/worker_binary.py` legitimately
        imports `probe_core_library`/`CoreProbe` from the same module to
        validate the library without writing to it — the check is precise
        about which *names* are imported, not just which module).
      - #4: widened `test_no_core_module_names_a_plugin.py` from 5 to 7
        functions (added `destination_definitions_from_manifests`,
        `discover_plugin_catalog`). Considered and rejected a whole-file
        literal scan across all core modules: it cannot tell a real branch on
        a plugin key from a plugin name used as a docstring example —
        confirmed by hand against `recording_contract.py` ("a BrainBit-2
        family device", prose) and `study_readiness_service.py`
        (`notion_enabled` as a worked example) — so it would need its own
        allowlist infrastructure to stay honest, and every module-level hit
        surveyed this way turned out to be an already-legitimate, previously
        undocumented exception (deprecated route aliases in
        `routes/sensors.py` / `routes/notion.py`, Notion's one-time legacy
        queue migration). Left as a possible future test, not invented here
        under time pressure.
      - #3: `ContractsDependsOnNothingTests` — `study_runner/contracts/`
        doesn't exist yet, so this currently **skips** (not passes
        vacuously-and-silently) with an explicit reason. Activates itself
        the moment Phase 2/3 creates the first file there.
- [x] **1.4** Invariant #6 as a field-name denylist
      (`NoPersistedGlobalTimeFieldTests`), scoped to string literals used as
      an actual dict/JSON key (`ast.Dict` keys, subscripts,
      `.get`/`.setdefault`/`.pop` first arguments) — deliberately **not** a
      whole-string-literal scan, for the same docstring-false-positive reason
      found in 1.3. Patterns: `global_time`, `unified_time(stamp)`,
      `synced_time`, `synchronized_time`, `absolute_time`, `canonical_time`,
      `merged_time`. Verified it does not flag legitimate fields
      (`client_clock_offset_ms`, `timestamp_start`) and does flag a
      synthetic bad one (`unified_global_time_ms`). Today's codebase has zero
      matches — correct, since `timing.jsonl` (Phase 5c) doesn't exist yet;
      this is the tripwire for when it's built carelessly.
- [x] **1.5** `tools/measure_structure.py` — cross-package import edges,
      cycle count (transitive closure over the area graph, not just direct
      mutual edges — a cycle can route through a third area), lines per
      package, largest file. `--write-baseline` / `--check` (fails only on
      regression) / no-flag (prints current metrics). Baseline committed at
      `tools/structure_baseline.json`: **143 cross-package edges, 6 cycles**
      (`backend`×{`plugin_framework`,`recording`,`recording_worker`},
      `plugin_framework`×{`recording`,`recording_worker`},
      `recording`×`recording_worker` — all expected, all traceable to the 19
      known violations plus `backend`'s legitimate heavy use of those areas),
      largest file `plugins/brainbit/adapter.py` at 2283 lines. Verified
      `--check` both passes against its own baseline and fails when the
      baseline is stricter than reality, in all four metrics at once.
- Invariants #7 and the lifecycle-dependent parts defer to Phase 5a, per plan.

All of Phase 1 verified against a real run of the full suite in the worktree,
not just the new tests in isolation: 729 passed, 5 skipped. (One
`test_runtime_routes.py` failure is a pre-existing Windows temp-directory
cleanup race, already seen and confirmed unrelated in Phase 0 — passes in
isolation.) One doc-drift catch along the way: `test_file_guide.py` correctly
went red for the new `tools/measure_structure.py` having no guide entry —
exactly the mechanism it exists for, not a bug.

### Phase 2 — Break the import edges in place (branch, zero moves) — **implemented and repaired**

Import edges are broken before directory moves. R5 completes the validator
extraction and the known-violation allowlist is empty. Cycle count: **6 to 0**.
Zero directory moves: existing runtime packages remain in place; `shared/` and
`contracts/` hold the extracted dependency closures.

- [x] **2.1** `is_frozen` / `get_app_mode` / `get_project_base_dir` moved to
      new `shared/runtime_mode.py`; `runtime_config.py` re-exports them for
      its own internal use and for every existing backend-internal caller.
      The four out-of-area callers (`plugin_framework/dependency_utils.py`,
      `plugins/brainbit/{adapter.py,plugin.py}`,
      `plugins/camera_emotion/worker/plugin.py`) now import from
      `shared.runtime_mode` directly. Killed exactly the 4 edges predicted.
      Safe because 0.3 had already removed the depth assumption these
      functions relied on
- [x] **2.2** `PluginContext.secret_resolver` is now an injected field, not a
      lazy import. But the fix goes further than "wire it in
      `registry.build_context()`": `.secret()` runs in **two** processes, not
      one — the host (`backend/__init__.py`) and each plugin's own
      `driver.py` subprocess (`process_host.py` → `driver_runtime.py`). A
      subprocess cannot receive an injected host-side callable, and it may
      not import `backend` either, so the resolver itself had to move
      somewhere both sides can reach: `study_secrets_service.py` was 100%
      pure (no Flask, no app context — confirmed by reading the whole file)
      despite living under `backend/`, so it moved wholesale to new
      `plugin_framework/plugin_secrets.py` (`resolve_plugin_secret` and its
      full dependency chain: `get_study_secret`, `study_key`,
      `secret_fields`, `_credential_declarations`, `describe_secret_state`,
      `describe_secret_storage_location`, `list_study_credential_state`,
      `set_study_secret`, `copy_study_secrets`, `forget_study_secrets`).
      `normalize_study_id` (its one real dependency) moved to new
      `shared/study_identifiers.py` for the same reason.
      `backend/services/studies/study_secrets_service.py` is now a thin
      re-export shim so its 7 existing backend callers (routes,
      `study_readiness_service.py`, ...) keep working unchanged.
      `driver_runtime.py::_context_from_payload` and
      `backend/__init__.py::_plugin_context` both now pass
      `secret_resolver=resolve_plugin_secret` from the new location.
      Verified all three paths directly: no-resolver raises a clear
      `RuntimeError`, the host path resolves a secret, the **subprocess**
      path (`_context_from_payload` → `.secret()`) resolves one too — this
      last one is the one that actually matters, since it's the one that
      silently wasn't being exercised by any existing test
- [x] **2.3** Two real, independent-of-the-rebuild bugs found while doing
      this, not just an import move:
      - `PARTICIPANT_FIELD_ORDER` → new `contracts/participant_fields.py`
        (pure data, no dependency), re-exported from `validation.py`.
      - `notion_upload/adapter.py`'s `_refresh_config_for_retry` turned out
        to be **dead code**: it only ran when `upload_study_result`'s
        `is_retry` flag was `True`, and grepping the whole plugin confirmed
        nothing ever passes `True` — retries are entirely the persistent
        upload-job queue's responsibility now, which just re-invokes
        `publish_destination` with the original payload. Deleted outright,
        no capability needed for this half.
      - `_persist_study_database_id` **could never have worked** since the
        v4 subprocess migration: it called `flask.current_app`, which raises
        outside an active Flask app context, and a `driver.py` subprocess
        has none. Every call was silently swallowed by its own broad
        `except Exception`. Practical effect: every time Notion
        auto-created a database, the id was never actually saved back to
        the study config, so `_ensure_database`'s only lookup
        (`study_settings.get("notion_database_id")`) would find nothing
        next time and **create another database** — confirmed by reading
        `_ensure_database`, which has no other dedup path. Fixed by
        rejecting the "or the configuration in the payload" half of this
        step's original two options: `_ensure_database` /
        `_get_data_source_id` now write into a plain `updates: dict[str,
        str]` passed down instead of calling back into Flask;
        `upload_study_result` attaches it to its result as
        `study_config_updates` when non-empty; new
        `upload_runtime.py::_persist_study_config_updates` (host-side,
        backend-legal) does the canonicalization + `save_config` +
        `save_study` that `_persist_study_database_id` used to do in-process,
        after the RPC call returns. No new bidirectional protocol needed —
        it rides the existing payload-in/result-out shape.
        `test_upload_runtime.py` covers the host half,
        `test_notion_upload.py`'s replacement test covers the plugin half.
        The `request_study_config` capability this step's plan text
        mentioned was **not needed**: it would only have served the now-
        deleted dead code path
- [x] **2.4** Extract dependency utilities and pure manifest validation.
      - **`ensure_requirements` → `shared/dependency_utils.py`.** Fully
        self-contained (stdlib + `shared.runtime_mode`), so the whole file
        moved rather than splitting it. `plugin_framework/dependency_utils.py`
        re-exports for its 6 existing plugin callers.
        `recording/{markers,clock_diagnostics}.py` now import it from
        `shared` directly. Killed 2 of the 4 remaining `plugin_framework`
        edges.
      - **`validate_and_normalize_manifest` lives in `contracts/manifest.py`.**
        R5 moves the complete pure helper closure; discovery stays in the framework.
        Recording imports contracts directly. All nine comparison manifests
        normalize identically. The two remaining known violations are removed.
        Existing public reexports remain until the explicit Phase 4 migration.
- [x] **2.5** Broke the host↔worker cycle. Landed in `shared/`, not
      `recording/contract/`: a subpackage nested under `recording/` would
      still match `study_runner.recording` as a dotted prefix and violate the
      very invariant being fixed — the real `data_core/contract/` package
      only becomes possible once Phase 4 moves `recording/` out from under
      that name entirely. `shared/` is architecturally equivalent for now
      (depends on nothing, importable from anywhere) and is a mechanical
      rename into `data_core/contract/` later.

      Five wholesale moves, each with the old location kept as a re-export
      shim (same pattern as 2.1–2.4): `worker_protocol.py` (473 lines — the
      entire wire contract: `WorkerCommand`, `WorkerResponse`,
      `WorkerEndpointState`, `PersistentCommandLedger`,
      `LoopbackWorkerClient`, `WorkerCommandRouter`, `WorkerStateStore`),
      `errors.py` (worker_protocol's own dependency — had to move first),
      `backup.py` (316 lines, fully self-contained already), `recovery.py`
      (`RecordingLeaseStore`). Two split extractions:
      `CoreProbe`/`NativeXdfError`/`probe_core_library` out of
      `recording_worker/core.py` (confirmed `NativeXdfCore`/`NativeXdfWriter`
      — the actual byte-writing classes invariant #5 restricts to the worker
      — depend on the probe but not vice versa, so the split is clean), and
      `require_pylsl`/`lsl_version_info` out of
      `recording_worker/lsl_recording.py` into `shared/lsl_dependency.py`
      (also fixes the 19th edge 1.2 found:
      `backend/services/recording/recording_runtime.py`'s host-side
      preflight needed the exact same two functions).

      `worker_binary.py:60`'s existing injection seam
      (`core_probe: Callable[...]`) turned out not to need touching — the fix
      was one level down, in what `core.py` itself imports.

      **Verification went beyond the allowlist test.** Added two new
      subprocess-blocker tests to `test_area_boundaries.py`, symmetric with
      the existing Flask-blocking one but blocking `study_runner.recording`
      and `study_runner.recording_worker` respectively — these are the tests
      that would have caught this exact violation before it was fixed, and
      now prove both directions of invariant #1 directly rather than just
      pinning today's known exceptions.

      **Structure metrics after Phase 2** (`tools/measure_structure.py`,
      baseline rewritten as a deliberate checkpoint): **cycle count 6 → 0.**
      Cross-package edges rose slightly (143 → 147) and `shared/` grew from
      154 to 1536 lines — both expected and correct: the shim files add a
      few re-export edges, and moving real logic into `shared/` is the whole
      point of this phase. The cycle count dropping to zero is the number
      that actually mattered.

### Phase 3 — Legacy removal (branch)

- [x] **3.1** Removed `_import_plugin` and `SUPPORTED_PLUGIN_API_VERSIONS`
      narrowed from `(3, 4)` to `(4,)` — all six shipped manifests were
      already `api_version: 4`, so the v3 in-process path was provably dead
      before this landed, not just legacy-but-used. `entry_point` is now
      optional (validated-if-present) rather than required, per T4; kept on
      `markers.manifest.json`/`clock_diagnostics.manifest.json` as accurate
      documentation of where `BUILT_IN` lives, since it's harmless and true.
      **T3 caught for real, not just in theory**: `markers.py`/
      `clock_diagnostics.py` load their own manifests at *module* scope with
      `api_version: 3` still declared, and `apps/server/application.py`
      imports both eagerly — narrowing the tuple without bumping these two
      in the same commit would have made `create_app()` unimportable, exactly
      as T3 predicted. Bumped both to `api_version: 4` with a `runtime` block
      that is schema formality, not a real subprocess declaration (documented
      in `markers.py`'s docstring) — these two are imported directly by the
      host process and never spawned as `driver.py`, unlike every real
      extension.
      **Second, deeper finding, beyond T3/T4's own scope:** `_validate_plugin_object`
      in `plugin_catalog.py` (the "does this Plugin object actually implement
      the handler its manifest capability requires" check) turned out to be
      dead code for its only remaining caller: `build_process_plugin` derives
      every handler directly and unconditionally from the same manifest's own
      `capabilities` set, so every one of that function's checks became a
      tautology once v3's hand-written in-process `Plugin` objects (which
      genuinely could omit a handler while still declaring the capability)
      were the only thing that could ever trip it. Removed; two tests that
      only ever exercised that dead path removed with it (git history has
      them if this reasoning needs revisiting).
      **Third finding: a whole test pattern needed real modernization, not a
      version bump.** Three test files (`test_fixture_plugin_blueprint.py`,
      `test_plugin_credentials_capability.py`,
      `test_plugin_readiness_requirements_capability.py`) discover a
      synthetic plugin from a temp directory and, in one case, actually
      invoke a live handler through it — which for a real v4 plugin means
      spawning `driver.py` as a subprocess that resolves itself via
      `extension_layout.trusted_roots()`, hardcoded to the real
      `extensions/{sensors,cards,destinations,outputs}` directories with no
      injection seam. Added one: `TEST_EXTRA_ROOT_PATH_ENV_VAR`/
      `TEST_EXTRA_ROOT_PACKAGE_ENV_VAR` in `extension_layout.py`, read only
      from the environment (never a request or manifest value, so it can
      never become an attacker-controlled plugin path per
      CONTRIBUTING.md #1), which the child subprocess inherits since
      `process_host.py` already does `env = os.environ.copy()`. New
      `tests/support/fixture_plugin.py` (`FixturePluginRootMixin`,
      `write_driver_py`) centralizes the temp-package + env-var + PYTHONPATH
      wiring so all three (and any future one) share it instead of
      re-deriving it. All three fixtures gained a real `driver.py` shim
      (`run_plugin_driver(plugin_key)`, identical to
      `tests/fixtures/packaging_probe/driver.py`) and now go through the
      genuine v4 subprocess pipeline end to end, not a synthetic stand-in.
      Full suite still green throughout (812 passed/4 skipped — two fewer
      than before from the dead-test removal above, not a new gap).
- [x] **3.2** `upload_destination.legacy`: migrate and write forward (D3) —
      **found already implemented, predating this rebuild; verified, not
      newly built.** Traced the full call chain: every route that persists a
      study (`apps/server/routes/{admin,study}.py`) calls
      `validate_and_normalize_config` before `save_config`/`save_active_study`,
      which calls `normalize_study_settings_plugins`
      (`runtime_core/studies/study_plugin_config.py`), which migrates
      `notion_enabled`/`notion_database_id`/`nextcloud_enabled`/
      `nextcloud_share_link` etc. into the canonical
      `study_settings.plugins.<key>.settings` shape *and* calls
      `_remove_legacy_destination_fields` to pop every legacy flat key from
      the dict that actually gets written to disk. Confirmed by two already-
      passing tests, not just by reading the code:
      `test_study_plugin_config.py::test_v3_plugin_values_override_legacy_without_reemitting_flat_fields`
      and `test_study_settings_contract.py`'s round-trip tests both assert
      `assertNotIn("notion_enabled", ...)` after normalization. No code
      change was needed or made. What D3 still defers, correctly: the READ
      path (accepting the legacy fields as input) stays — D3's own sequence
      is migrate-and-write-forward → ship one release → *then* remove
      reading them, and no release has shipped yet (version is
      `1.0.0-dev`). Removing the read path is explicitly **not** part of
      3.2 and remains future work, gated on a real release.
- [x] **3.3** Renamed "legacy flat result folders" to "archival compatibility
      surface" in `docs/plugin-recording-architecture.md` and
      `docs/sensors-and-data.md`. The fixture test pinning the actual
      behavior already existed since Phase 0.4
      (`tests/test_legacy_flat_result_compat.py`) — this was wording-only.
      **Do not remove** (D2)
- [x] **3.4** Merge capabilities `readiness` / `runtime_control` / `health` into
      one lifecycle contract, `api_version: 5` — **implemented 2026-09-10,
      exactly as designed below** (owner approved the design as scoped, no
      deviations). `runtime_control` removed entirely; the dead fallback in
      `process_host.py::build_process_plugin` now reads `runtime.actions`
      alone. `health` now gates `SensorCoordinator.build_status`'s per-plugin
      poll (`data_core/host/sensor_coordinator_service.py`) — a plugin with no
      `health` capability is skipped, not given a "pending" placeholder
      either. `readiness` renamed to `runtime_modes` in `contracts/manifest.py`
      (`_normalize_readiness` → `_normalize_runtime_modes`) and in
      `study_readiness_service.py`'s reader; the 5 plugins that declared it
      empty (`{}`) had it dropped outright, only `camera_emotion` keeps it,
      non-empty. `RETIRED_CAPABILITIES` in `contracts/manifest.py` rejects a
      manifest still declaring either old name with a message naming the
      replacement, instead of silently ignoring an unrecognized capability.
      All 21 manifests (12 cards, 3 sensors, 2 destinations, 1 output, the 2
      host-process manifests, the packaging-probe test fixture) bumped to
      `api_version: 5` in the same commit as the contract change, per T3 —
      `create_app()` imports `markers.py`/`clock_diagnostics.py` eagerly at
      module scope, so a split commit would have made it unimportable, exactly
      as it did at 3.1. `SUPPORTED_PLUGIN_API_VERSIONS = (5,)`, no `(4, 5)`
      transition window, matching 3.1's precedent. The dead `runtime_control`
      icon branch in `apps/ui/scripts/shared/plugin-catalog.js::pluginUiIcon()`
      removed.

      **New test coverage**, not just updated assertions:
      `test_plugin_catalog.py::test_retired_v4_capabilities_are_rejected_with_a_clear_message`
      (both retired names raise `PluginManifestError` naming the retirement)
      and `test_sensor_coordinator_service.py::test_a_plugin_without_the_health_capability_is_never_polled`
      (confirms `get_plugin_status` is never even called, and the plugin gets
      no entry in `status["plugins"]` at all — proven via `Mock.assert_not_called()`,
      not inferred from timing).

      **Verified directly, not assumed:** with a live dev server
      (`STUDY_RUNNER_DISABLE_HARDWARE=1`), the public catalog reports
      `api_version: 5` with all 18 plugins valid and zero invalid entries; a
      direct (non-HTTP) call to `SensorCoordinator.build_status` returned in
      33ms with exactly the 6 non-card plugins that declare `health`
      (`brainbit`, `camera_emotion`, `mini_radar`, `nextcloud`, `notion`,
      `osc`) and none of the 12 cards — confirming the gate actually removes
      12 extensions from the poll path, not just from a manifest field. (A
      `curl` request to `/api/admin/status` over the dev server's HTTPS
      separately hung; isolated to the dev server/TLS layer, unrelated to this
      change, and out of scope here.)

      Evidence: **965 Python passed, 4 skipped** (963 + 2 new); 43 JavaScript
      passed; PyInstaller packaging-contract tests passed; structure baseline
      rewritten (small line-count growth from new docstrings/comments/tests —
      cross-package edges and cycle count unchanged, so this is explanatory
      content, not new coupling).

      Docs updated: `docs/developer-guide.md` ("Manifest API v5"),
      `docs/plugin-recording-architecture.md` (capability list, the
      `_import_plugin` removal note corrected — it was already stale, dating
      to before 3.1, not something this package introduced),
      `extensions/README.md`, `CONTRIBUTING.md` §7.

      Original design record, preserved below for the reasoning trail:

      **What each capability actually does today, verified by tracing every
      call site, not assumed from the manifests:**

      - **`health`** (declared by all 6 manifests): **zero effect anywhere.**
        `sensor_coordinator_service.py::SensorCoordinator.build_status` polls
        *every* plugin via `iter_plugins()` unconditionally — nothing reads
        the `health` capability to decide whether to poll. Declaring or
        omitting it changes nothing observable.
      - **`runtime_control`** (declared by the 3 sensors: brainbit,
        camera_emotion, mr60_mini_radar): **also zero effect**, for two
        independent reasons. (a) `process_host.py::build_process_plugin`'s
        only use of it is a *fallback* — `actions = runtime.actions or
        (["start","stop","restart"] if "runtime_control" in capabilities
        else [])` — but all three sensors already declare `runtime.actions`
        explicitly, so the fallback branch is dead code in practice. (b) The
        admin UI's `plugin-catalog.js::pluginUiIcon()` has a `runtime_control`
        icon branch, but it sits *after* `lsl_stream_provider` in the
        cascade, and all three sensors also declare `lsl_stream_provider` —
        so that branch is unreachable too.
      - **`readiness`** (platform-mode support; declared by all 6, but
        non-empty only for camera_emotion's `worker_mode` local/remote
        split): the **only one with real, load-bearing behavior** —
        `study_readiness_service.py` reads `mode_setting`/`default_mode`/
        `platform_modes` from it to block an unsupported mode per platform.
        For the other 5 plugins it is declared as `{}`, which
        `contracts/manifest.py::_normalize_readiness`'s own `if not config:
        return {}` turns into a confirmed no-op.

      **What this means for scope:** this is not "merge three living
      contracts" — it is "retire two capabilities that already do nothing,
      keep the one that does something, and fix a confusing name," which is
      smaller and safer than a from-scratch 3–5 day contract redesign.
      `readiness` (this capability) and `readiness_requirements` (a
      completely different, unrelated capability — "is the operator's
      config complete", see that function's own docstring) already collide
      in name today; the merge is a good moment to fix that too.

      **Recommended design for `api_version: 5`:**
      1. Remove `runtime_control` entirely. Confirmed unused; per
         CONTRIBUTING.md §1 ("no structure for a hypothetical future need")
         there is no reason to keep a flag with a provably dead fallback.
         `runtime.actions` remains the one and only source of truth for
         start/stop/restart, with its existing default of `[]` when absent
         — no capability-triggered default needed.
      2. Make `health` mean something instead of removing it: gate
         `SensorCoordinator.build_status`'s per-plugin poll on
         `"health" in capabilities`, so a plugin that has nothing worth
         polling (e.g. a pure one-shot output) can opt out. Keeps the
         familiar name, gives it real teeth for the first time.
      3. Rename `readiness` (this capability) to **`runtime_modes`** —
         same schema (`mode_setting`/`default_mode`/`platform_modes`), just
         a name that stops colliding with `readiness_requirements`. Drop the
         empty `{}` declarations on the 5 plugins that never configured it;
         only camera_emotion keeps it, non-empty.
      4. `SUPPORTED_PLUGIN_API_VERSIONS` becomes `(5,)`, mirroring 3.1's "one
         live version" cleanup — no reason to carry a `(4, 5)` transition
         window since every manifest is migrated in the same commit.

      **Files that changed** (see the 2026-09-10 implementation note above for
      the full account): `contracts/manifest.py`, all 21 manifests (12 cards,
      3 sensors, 2 destinations, 1 output, 2 host-process, 1 test fixture —
      grew from the originally-scoped 6 once card manifests and the two host
      manifests were accounted for), `plugin_framework/process_host.py`,
      `data_core/host/sensor_coordinator_service.py`,
      `runtime_core/studies/study_readiness_service.py`,
      `apps/ui/scripts/shared/plugin-catalog.js`, plus every test referencing
      any of the three capability names by string.
- [x] **3.5** Fixed doc drift: the file is `extensions/README.md` now (moved
      in Phase 4.7) and already correctly said "api_version: 4" everywhere
      except one leftover sentence ("API v3 reads only per-folder
      manifests") describing the discovery model itself, not a specific
      plugin's version — reworded to drop the now-doubly-stale version
      number rather than bump it to a version that will itself go stale
      again at 3.4.

### Phase 4 — Directory move (branch) — **complete**

> **2026-09-08 course change (operator-directed):** Phase 3 (legacy removal,
> above) is deliberately **skipped for now** and Phase 4 was pulled forward
> ahead of it — the operator judged that continuing small, fully-tested,
> backwards-compatible increments was too slow relative to the goal, and
> asked for the biggest structural change (this directory move) done first,
> in large steps, accepting temporary breakage between steps, with a single
> bundled fix-and-verify pass rather than a green suite after every file.
> Concretely this changed one rule below: **the per-package "full suite
> green → next package" gate was dropped in favour of a full-suite run after
> most (not all) packages** — in practice nearly every package below *was*
> immediately verified with the full suite and any regression fixed in the
> same commit, because every single package surfaced at least one real
> latent bug (see "Real issues found" under each entry) that would have been
> far more expensive to untangle later, bundled across multiple moves. The
> discipline that was **not** dropped: one commit per package, never mixing
> a house-keeping fix with an unrelated feature, and no compatibility shims
> for the old paths — old import paths break immediately and are rewritten
> tree-wide in the same commit as the `git mv`. See the decision log entries
> dated 2026-09-08 below for the full reasoning.

Two rules that still hold: **never mix a move with a behaviour change in one
commit**, and **one commit per target package**.

Per package: `git mv` → in the *same* commit, mechanically rewrite imports for
that one prefix across the whole tree (a small throwaway Python script doing
literal `str.replace()` on the dotted import path, run from the repo root —
faster and less error-prone by hand than sed across ~30-90 files per package)
→ fix any relative imports the script's literal replace cannot reach (dotted
`from ..x import` forms need manual conversion to absolute) → confirm rename
detection with `git show --stat -M90%` → full suite green (in practice: every
package so far) → next package.

Order: `shared` → `contracts` → `data_core/contract` → `data_core/worker` →
`data_core/host` → `plugin_framework` → `runtime_core` → `extensions/*` (one
commit per category, derived from each manifest's `category`) → `apps/ui` →
`apps/server`.

- [x] **4.0** `git config merge.renameLimit 4000` and `diff.renameLimit 4000`
      set. Also resolved first, before any `git mv`: the Phase 2 re-export
      shims (`recording/{worker_protocol,errors,backup,recovery}.py`,
      `plugin_framework/dependency_utils.py`,
      `backend/services/studies/study_secrets_service.py`) — pure
      pass-through modules Phase 2/R5 deliberately left for later removal.
      Two were literal same-named-file duplicates of their `shared/`
      counterpart (`worker_protocol.py`, `dependency_utils.py`) — exactly
      what the operator's naming-clarity requirement forbids for the final
      layout. All six deleted, ~30 importers rewritten to the real module
      directly. Commit: "Phase 4.0-4.1: resolve Phase-2 re-export shims
      before directory moves".
- [x] **4.1 `shared`** — no move needed; `shared/` was already at its D6
      location. Its content was the shim cleanup above.
- [x] **4.2 `contracts`** — `plugin_framework/plugin_api.py` (pure
      data/type module: `PluginContext`, `Plugin`, handler type aliases; no
      behaviour, no dependency but `contracts/manifest.py`) moved into
      `contracts/`. 32 importers rewritten (every plugin, `plugin_framework/*`,
      several `backend/services/*`, the whole test suite).
- [x] **4.3 `data_core/contract`** — the six wire-type/pure-probe modules
      Phase 2.5 had parked in `shared/` as an interim step (`worker_protocol`,
      `recording_errors`, `backup_projection`, `recording_lease`,
      `native_core_probe`, `lsl_dependency` — see Phase 2.5's own note: "a
      mechanical rename into `data_core/contract/` later") moved to their
      real home. 20 importers rewritten.
- [x] **4.4 `data_core/worker`** — `recording_worker/` → `data_core/worker/`.
      Not to be confused with `software/recording_worker/native/`, the C++
      core, a different, untouched top-level directory. 26 references
      rewritten. **Real issue found:** `test_architecture_invariants.py`'s
      "only the worker writes XDF bytes" check used a first-path-segment
      area match; carried forward naively it would have started allowing
      the sibling `data_core/contract/` (and later `data_core/host/`) to
      import `NativeXdfWriter` merely for sharing the `data_core` top
      segment. Tightened to a two-segment prefix check.
- [x] **4.5 `data_core/host`** — the big one: `recording/` (7 files) and
      `backend/services/recording/` (13 files) merged into one directory.
      They were the same responsibility split in two only because the
      pre-1.0 shape had `backend` importing `recording` eagerly at
      Flask-app-construction time, a real cycle this package no longer
      risks (it has no Flask routes). 39 importers + 8 relative-import call
      sites rewritten. **Real issues found (two), both caught by
      `tools/measure_structure.py`'s cycle count, not assumed away:**
      (a) `recording_finalization_adapter.py` imported both
      `backend.services.delivery.finalization_service` and
      `data_core.host.recording_quality`; since `backend` already legitimately
      imports `data_core.host` extensively, having this adapter live in
      `data_core.host` too closed a fresh cycle. Moved to
      `backend/services/delivery/` instead — the target diagram's allowed
      direction (RuntimeCore → DataCore) — not to `data_core/host`.
      (b) `sensor_flush_service.py` (now `data_core/host`) and
      `results_service.py` (`backend/services/studies`) both needed
      `sanitize_identifier_for_filename`, previously defined inside
      `results_service.py`. Extracted to new, dependency-free
      `shared/filename_sanitizer.py` rather than have one side import the
      other. Also updated the two internal-diagnostic-plugin manifests'
      `entry_point` field (`markers.manifest.json`,
      `clock_diagnostics.manifest.json`) to their new module path — confirmed
      unused by the actual discovery path (they self-load by `__file__`, not
      through `discover_plugin_catalog`) but left accurate rather than stale.
- [x] **4.6 `runtime_core`** — `backend/services/{studies,settings,delivery}`
      → `runtime_core/{studies,settings,delivery}` (recording already gone
      in 4.5). Sibling-relative imports between the three stayed correct
      automatically (same relative depth after moving together).
      `backend/services/README.md` ported to `runtime_core/README.md`,
      updated to remove the now-wrong claim that recording lives alongside
      these three. `backend/services/` (now empty) removed. 53 files
      rewritten (absolute + two different relative-dot forms: `backend/__init__.py`'s
      single-dot `.services.X`, `backend/routes/*.py`'s two-dot `..services.X`).
- [x] **4.7 `extensions/{sensors,cards,destinations,outputs}`** — all six
      built-ins moved according to their manifests; `cards/` is an empty
      package. Discovery, child-process launch, UI assets, self-check, and
      packaging share one trusted-root resolver. Conflicts are checked across
      categories and the old `plugins/` package is removed.
- [x] **4.8 `apps/ui`** — `frontend/` moved to `apps/ui/`; static HTTP URLs
      stayed unchanged. Runtime lookup, JS tests, release licences, and
      PyInstaller data targets use the new directory.
- [x] **4.9 `apps/server`** — Flask factory, routes, and server runtime moved
      completely to `apps/server/`; the old `backend/` package is removed.
      `software/server.py` and `study_runner/app_server.py` remain stable
      entrypoints, with the latter delegating to `apps/server/application.py`.
- [ ] **4.10** *(reserved — the plan above only names 8 real packages plus
      4.0; renumber if a package above turns out to need splitting)*
- [x] **4.11** Hand-edit the two dynamic import sites: `driver_runtime.py:28`
      and `plugin_catalog.py`. Both resolve categorized packages through
      `plugin_framework/extension_layout.py`.
- [x] **4.12** Extend `_MOVED_PLUGIN_PATHS` — see [T7](#t7--operator-stored-plugin-paths-break-on-a-folder-move).
      Now in `runtime_core/settings/hardware_settings_service.py` (moved in 4.6).
- [x] **4.13** Pull the rest along: `study_runner_server_common.py` (15 path
      literals) · `build_source_release.py` · `build_python_onedir.py` ·
      `build_python_update_manifest.py` ·
      `tools/{setup_recording_worker,study_runner_manager,make_timeline_fixture}.py` ·
      `tools/{install,start}-{windows.ps1,macos.sh}` · `ci.yml` ·
      `.gitattributes` · `release_tools/tests/test_pyinstaller_common.py` ·
      the ~30 `Path(…)/"study_runner"/…` literals in `software/tests/`.
      The final sweep and path tests cover current source, packaging, CI, and
      documentation paths; legacy literals remain only in explicit migration
      tests and historical documents.
- [x] **4.14** Rewrite `docs/file-guide.md` structurally, **once, at the end**
      (the test only checks name presence, so it stays green throughout —
      confirmed still true after 4.0-4.6; one line was added for the new
      `shared/filename_sanitizer.py` in 4.5 rather than deferred, since the
      test would otherwise fail immediately, not just look stale), and
      add ~20 lines asserting every backticked path in the guide exists on disk
- [x] **4.15** `version.py` → `1.0.0-dev`
- [x] **4.16** **Build and start a bundle now**, not at the end of Phase 6 —
      otherwise a packaging break sits undetected in the branch for weeks

**Handoff note:** Phase 4 is closed. Resume with the deferred Phase 3
compatibility/plugin-contract work, then Phase 5c lifecycle and checkpoint
recovery. Keep native hardware and measured power-loss checks as separate
release gates.

Keep the branch fresh with `git merge main` daily. **Do not rebase** — it
re-derives rename detection on every replay and will eventually lose a file's
history.

### Phase 5 — New capabilities (branch, partly parallel)

- [x] **5a** Explicit session/recording lifecycle and transition guards. Keep
      recording recovery, finalization jobs and upload jobs as separate state
      machines, with a documented mapping. `SEALED` describes validated data;
      a failed upload cannot invalidate that seal. Define withdrawal transitions
      during recording and after sealing. Scientific sealing depends on 5h.

      **This is additive, not a replacement** — the doc's own instruction
      ("keep them as separate state machines") is what makes 5a tractable.
      Mapped, not merged, the four status dimensions that already exist and
      each answer their own question well:

      | Machine | Document | States |
      |---|---|---|
      | Recorder | `recording-plan.json` `.status` | `starting`, `recording`, `recovering`, `attention_required`, `frozen` |
      | Finalization job | `finalization-state.json` `.status` | `queued`, `running`, `retrying`, `attention_required`, `completed`, `completed_degraded`, `failed` |
      | Scientific quality | `finalization-state.json` `.quality_status` | `pending`, `valid`, `degraded`, `invalid` |
      | Upload job | upload queue | `queued`, `running`, `done`, `failed` |

      New `contracts/session_lifecycle.py` (pure, no dependencies): the
      seven states, `ALLOWED_TRANSITIONS`, `is_allowed_transition()`/
      `require_transition()` guards, and `derive_session_lifecycle()` — the
      documented mapping. Surfaced as a `lifecycle` field on every session
      in `sessions_index_service.py`, derived on read rather than stored a
      second time, so it can never disagree with the documents it comes from.

      Three decisions worth naming, each protecting something the target
      doc asks for:
      - **Upload status is not an input at all.** Not "considered and
        outranked" — genuinely not read, so a destination that refuses a
        file cannot un-seal validated data (ownership table: "failed upload
        does not unseal valid data"). Pinned by its own test.
      - **`SEALED` requires the data to have validated**, not merely that a
        job stopped: a `completed` job whose `quality_status` is `invalid`
        maps to `FINALIZING`, not `SEALED`. A *human-confirmed* degraded
        outcome (`completed_degraded` + `degraded`, which only happens after
        an operator explicitly confirms via `degraded_confirmation`) is
        sealed — the degradation stays visible in `quality_status`, which
        remains its own separate dimension.
      - **`attention_required` is not terminal** on either machine. On the
        finalization side an operator can still retry or confirm-degraded,
        so it maps to `FINALIZING`; on the recorder side participation
        continues, so it maps to `RECORDING`. The *reason* stays in each
        machine's own status, unchanged.

      **Caught by running it against a real session, not just unit tests:**
      the first draft reported the shipped `Demo_Completed_Study` fixture as
      `IDLE`, because an archival session has neither a recording plan nor a
      finalization state — only its `COMPLETE.json` marker. Reporting a
      visibly finished session as "not started" is exactly the confidently
      wrong answer the module's own docstring warns against, so the
      derivation now falls back to the terminal marker (which carries the
      same status vocabulary, having been written by the finalization job).

      `WITHDRAWN` is defined here with its transitions (reachable from every
      state including `SEALED`, terminal itself) and a settled marker name
      (`WITHDRAWN.json`, matching the existing marker convention), so 5i's
      withdrawal workflow has something to write into rather than inventing
      a state next to an already-shipped lifecycle. `SEALED` never returns
      to `FINALIZING`; `FAILED` does, because a retry is a real operator
      action.

      21 new tests (`test_session_lifecycle.py`). Full suite **851 passed,
      4 skipped**; JS 27 passed; structure baseline rewritten as a
      checkpoint.
- [x] **5b** Preflight: capacity and clock, enforced once at
      `RecordingRuntimeService._start_worker_generation()` (covers fresh
      start, crash-recovery reissue and full resume — the single real
      worker-spawn choke point, not three scattered checks).
      New `shared/system_clock_probe.py` (plausibility bound against two
      hardcoded epoch constants, not a self-referential one; per-platform
      time-service evidence via PowerShell `Get-Service .Status` on
      Windows — a .NET enum, locale-invariant, unlike `sc query` text) and
      `backend/services/recording/recording_capacity.py`. **Deviation from
      the literal target text:** required bytes come from the
      manifest-declared stream rate (`channels × format bytes ×
      nominal_rate_hz`) × the new optional `planned_session_duration_minutes`
      study setting, never a measured disk write-speed benchmark — an SSD's
      hundreds of MB/s sequential rate would always pass against a
      kilobytes/s EEG stream and prove nothing. Missing duration is
      `ok=False, known=False`, never a silent pass.
      `study_readiness_service.py` gained two independent blocker codes
      (`recording_capacity_insufficient`, `recording_clock_implausible`)
      alongside the existing `recording_worker_unavailable`, checked
      independently so one failure never hides another. 38 new/extended
      tests, full suite 807 passed/4 skipped, structure baseline rewritten
      as a deliberate checkpoint (152 edges, cycles still 0).
- [x] **5c** `quality.jsonl` + `timing.jsonl` during recording. **Not merely a
      relocation**: `recording_quality.py` imports `markers` (→
      `plugin_framework`) and reads plugin manifests, so moving it into the
      worker violates invariant #2. Its signatures also consume fully parsed XDF
      artifacts, while live QC needs streaming counters. Needs its own data
      model, and must come after 2.5 and 5e (both done).

      The one sentence that shaped the whole design (target doc §9):
      "Qualität entsteht **während** der Aufnahme in `quality.jsonl`, nicht
      erst bei der Finalisierung. Eine abgebrochene Session hinterlässt
      sonst kein QC." Everything below follows from *an aborted session must
      still leave evidence*.

      **The counters largely already existed** — `StreamRuntimeState` in
      `data_core/worker/lsl_recording.py` already tracked sample counts,
      first/last timestamps, clock offsets, reconnects and errors. They just
      lived in memory and were only ever exposed through `status()`. What
      was missing was a durable journal of them, thresholds that turn a
      number into an *event*, and the derived metrics (gaps, timestamp
      regressions, jitter, effective rate) nobody computed live.

      New `contracts/quality_journal.py` (pure, dependency-free so the
      detached worker can use it without importing anything host-side):
      record schemas, the versioned quality profile (§9: "Schwellenwerte
      stehen in einem versionierten Qualitätsprofil" — versioned so a later,
      stricter profile cannot silently re-judge old sessions),
      `StreamQualityObserver` (running aggregates only, never the samples,
      so describing a recording never costs memory proportional to its
      length), and `WallClockJumpDetector` (§6: an NTP correction or DST
      change must be on record, because the session's UTC anchors came from
      the clock that moved).

      New `data_core/worker/session_journals.py`: one append-only writer per
      worker process, shared by every recorder because the journals are
      session-scoped while recorders are per plugin. Durability follows the
      recording's own rhythm rather than inventing a second one — lines are
      fsynced on the same checkpoint tick that already flushes XDF data
      durably, so a crash loses at most the same window of evidence as of
      data. Promising more would mean fsyncing every line and slowing the
      ingest loop down to protect its own commentary. A journal write can
      never break a recording: an unwritable disk is swallowed, because
      these files are evidence *about* the session, not part of it.

      Wired in at three points: the ingest loop folds each pulled chunk into
      its stream's observer and journals any events; `_checkpoint_loop`
      writes periodic summaries on the durable-flush tick; `freeze()` writes
      final totals after the drain, so a cleanly frozen session ends with
      real counts rather than the last tick's. The worker runtime writes the
      session's two UTC anchors (§6 allows wall time *only* as one anchor at
      start and one at end) and runs clock-jump detection on its existing
      one-second monitor tick. `quality.jsonl`/`timing.jsonl` land at the
      session root (§8's layout) and are picked up automatically by the
      artifact manifest's glob, so they are checksummed like every other
      session artifact.

      **A real numerical bug, caught by my own test rather than shipped:**
      the first draft computed jitter as `E[x²] − E[x]²`, which cancels
      catastrophically for sample intervals — tiny numbers that barely
      differ. It reported **1.3 ns of jitter on a perfectly even stream**,
      false precision in exactly the number a researcher reads as timing
      quality. Replaced with Welford's online algorithm; a synthetic even
      250 Hz stream on a realistic large LSL clock now reports 3 ps, which
      is the float representation of the timestamps themselves rather than
      the algorithm.

      **Deliberately out of scope, with reasons rather than silence:**
      queue utilisation and drops under saturation belong to 5h, which
      introduces the bounded queues in the first place — reporting on a
      queue that does not exist yet would be inventing a number; and
      continuous free-storage monitoring is a different concern from ingest
      observation (5b already refuses to *start* without a plausible
      reserve). Both are named in the module's own docstring so the
      boundary is visible where someone would look for the missing metric.

      19 new tests (`test_quality_journal.py`). Full suite **870 passed,
      4 skipped**; JS 27 passed; structure baseline rewritten as a
      checkpoint.
- [x] **5d** Stream contracts: freeze at start, persist as
      `stream-contracts.json`, write into the XDF header, define timing-delay
      provenance. Operator decision 2026-09-08: do both the mechanical
      freeze/persist/header-write *and* the timing-provenance schema in one
      pass, rather than deferring provenance to 5c.

      **Confirmed before writing code, not assumed:** the native XDF writer
      needed no change. `card_summary_service.py::_study_runner_metadata`
      already *read* a `desc/study_runner/...` namespace defensively
      (`except: return None`), and `tools/make_timeline_fixture.py` already
      *wrote* a one-field version of it (`plugin_key` only) — but nothing
      populated it for a real recording. This is pure Python-side LSL
      outlet metadata (`pylsl.StreamInfo.desc()`), not an ABI/native change.

      **Freeze + persist**: new
      `recording_contract.py::stream_contracts_document()` projects the
      *already-frozen* `recording_contract["streams_by_source"]` (built by
      `build_recording_contract()`, itself unchanged) into a flat,
      publishable `stream-contracts.json` — written once in
      `recording_runtime.py::start_session()`'s fresh-start branch, the
      same freeze point as `recording-plan.json`, never rewritten on a
      later reattach/recovery of the same session (target doc §8's file
      layout, item marked *neu*).

      **Timing provenance** (target doc §6): new per-stream `timing`
      schema in `contracts/manifest.py`
      (`streams[].timing.capture_delay_ns.{source,min_ns,max_ns,reference}`,
      `source` one of `measured|datasheet|estimated|unknown`). Defaults to
      an honest `source: "unknown"` when absent — no adapter has a real
      measured delay yet, and CONTRIBUTING.md's own standard ("broken or
      incomplete data must not silently count as success") argues against
      fabricating false precision. Declaring anything but `"unknown"`
      requires bounds *and* a reference, so a future real measurement
      cannot be entered without also saying how it was obtained.

      **XDF header write**: new `contracts/stream_contract.py`
      (`stream_contract_desc_fields`, `apply_stream_contract_desc`,
      `load_own_stream_contracts`) — one shared, pure function instead of
      five hand-copied XML-building blocks. Wired into all five LSL
      producers: `data_core/host/{markers,clock_diagnostics}.py` (already
      load their own manifest at module scope) and the three sensor
      adapters `extensions/sensors/{brainbit,camera_emotion,mr60_mini_radar}/adapter.py`
      (which, as v4 process-host plugins, never see the host's parsed
      manifest — each now loads and normalizes its own `manifest.json`
      independently via `load_own_stream_contracts(__file__)`, same
      pattern `markers.py` already used for itself). Adapters' existing
      hand-written `LSL_SOURCE_IDS`/`LSL_CHANNEL_UNITS` constants are
      untouched — this only adds the new `study_runner` desc block
      alongside them, not a refactor of outlet creation itself.
      `tools/make_timeline_fixture.py` updated in the same commit (T8) to
      match the same field names, so the manual fixture stays consistent
      with a genuine 5d-era recording instead of drifting from it.

      Destinations/outputs (`nextcloud_upload`, `notion_upload`,
      `osc_touchdesigner`) do not publish LSL streams and needed no change.

      11 new tests (5 timing-schema tests in `test_plugin_catalog.py`, 6 in
      new `test_stream_contract.py`, plus an extended assertion in an
      existing `test_recording_runtime.py` test exercising the real
      freeze-to-file path). Full suite **830 passed, 4 skipped** (819 + 11
      new); JS 27 passed; structure baseline rewritten as a checkpoint
      (expected growth: new `contracts/stream_contract.py`, five adapters
      each gained a few lines of wiring).
- [x] **5e** Shared `event_id` across journal and LSL markers, dedup and
      compare at finalization, mismatch is a quality event.
      **Duplicate detection within the XDF marker stream already existed**
      (`card_summary_service.py::_marker_event_times`); what was missing was
      comparing the *durable session journal's* full event-id set against
      the XDF's, and softening that from a hard failure into a quality
      event per the operator's decision (2026-09-08, matching §9 of the
      target doc: "kein stiller Datenverlust; Marker werden priorisiert,
      aber nicht unrealistisch garantiert").

      New `card_summary_service.py::_journal_xdf_mismatches`, opt-in via a
      new `journal_event_ids` parameter on `CardSummaryBuilder.build()`
      (`None` skips the comparison, preserving every existing caller
      unchanged). Reports both directions as `quality_warnings` with code
      `journal_xdf_event_id_mismatch`: `missing_from_xdf` (journaled but no
      matching marker survived to the merged XDF) and `extra_in_xdf` (a
      marker with no journal counterpart, e.g. a lost acknowledgement). The
      one pre-existing hard check — a *required* terminal marker
      (`study_end_event`) missing entirely — stays a hard `CardSummaryError`,
      raised earlier in `build()`; the new comparison only ever adds
      additional soft findings on top, never re-flags what already passed
      that gate.

      New `FinalizationService._journal_event_ids()` reads the durable
      "trial" journal stream directly from disk via the existing
      `SessionJournalStore` (already a `FinalizationService` dependency),
      not a live `TrialEventService` instance — finalization must work
      after a server restart, when no such instance for an old session
      exists anymore. Each journal record is a full snapshot (see
      `session_journal_service.py`'s own docstring), so the newest one
      already contains every event id ever recorded for that session.
      **Real bug caught by my own new test, not shipped**: the record's
      payload sits under a nested `"snapshot"` key
      (`session_journal_service.py::append`'s own record shape), not at the
      record's top level — the first draft read `record.get("events")`
      directly and silently got nothing back, which would have made every
      real session's comparison spuriously flag every XDF marker as
      `extra_in_xdf`. Fixed before commit by reading `record["snapshot"]["events"]`.

      `FinalizationService._build_card_summary`'s existing warning-render
      loop already forwarded `quality_warnings` into the job's public
      `warnings` list; extended its one-line rendering to include
      `direction` when present, so `missing_from_xdf` and `extra_in_xdf`
      stay distinguishable in the short form, not just in the full
      `card-summary.json`.

      7 new tests (`test_card_summary_service.py`'s
      `JournalXdfEventIdComparisonTests`, one finalization-level
      integration test exercising the real on-disk journal read). Full
      suite 819 passed/4 skipped (812 + 7 new), JS 27 passed, structure
      baseline rewritten as a checkpoint (small, expected growth).
- [ ] **5f** `mrg` CLI. Last. "the same local command API as the UI" **does not
      exist** — the UI speaks HTTPS to Flask. Either the CLI does TLS and auth,
      or a new IPC surface gets designed. `software/server.py` keeps working
      regardless (D5)
      Include read-only offline inspection and exclusive maintenance locking for
      recovery/sealing writes; never bypass a running runtime's ownership
## Package A — visibility before more capability (2026-09-09)

Not a numbered Phase-5 package; a course correction. Auditing 5a/5c/5h/5i
after they landed found five pieces of correct, tested machinery reachable
by **nothing**: `WithdrawalService` and `summarize_quality_journal` had zero
callers anywhere in the codebase, `quality.jsonl`/`checkpoints.jsonl` were
written but never read back by the UI, and the `lifecycle` field 5a adds to
every session summary was never rendered. `CONTRIBUTING.md` section 1 rules
out structure with no reachable use — unreachable machinery is exactly that,
independent of how correct it is. Owner instruction: close this before
starting more Phase 5 capability.

- [x] **A1** Quality summary reader. New
      `runtime_core/studies/session_quality_summary.py` — the first caller
      `summarize_quality_journal()` (`contracts/quality_journal.py`) ever
      had. Reduces `quality.jsonl` to three UI-sized things: a
      `recording_health` level (`clean`/`warnings`/`attention`/`unknown`),
      counted findings (one per event kind + stream, not one line per raw
      event), and `kept_up` (the positive evidence from `peak_fill_ratio`,
      5h). Wired into the existing `load_session()` return value
      (`sessions_index_service.py`) as `quality_summary` — no new endpoint.

      **`unknown` is a fourth health level, not folded into `clean`.** A
      session recorded before 5c existed has no journal to read, and
      reporting that as `clean` would repeat the exact mistake 5a's own
      lifecycle derivation had already been written to avoid (an archived
      session with no finalization state reporting itself as `IDLE`).
      "Never measured" and "measured, found nothing" are different claims;
      only a journal that exists (even empty) earns `clean`.

      **Findings are structured, not English sentences.** `{"kind": "gap",
      "stream_key": "eeg", "count": 3}`, not "3 gaps in EEG" — the frontend
      already has a translation layer (`t(key, fallback).replace(...)`,
      see `sessions-browser.js`'s `moreHint`) and is the one place that
      should own user-facing text. Baking English into the Python layer
      would make it unlocalizable and duplicate that responsibility.

      **`kept_up` and the `ingest_backlog` finding are derived from the
      same computation**, never two independent thresholds that could
      disagree with each other — pinned by its own test.

      12 new tests (`test_session_quality_summary.py`) plus two in
      `test_sessions_routes.py`. Full suite **918 passed, 4 skipped**;
      structure baseline rewritten.

- [x] **A2** Session detail page. Built as a dedicated full-width panel
      ("Recording quality"), not a fifth quarter-tile in the existing 2x2
      `status-grid--row` — that grid's CSS (`nth-last-child(-n+2)` clearing
      the last row's border) assumes an even tile count, and a 5th tile
      would have broken the row-border math for an odd one. A findings list
      also needs more room than a quarter-tile's one-line hint allows.

      New markup in `admin.html`: `#session-lifecycle-badge` (next to the
      title) and `#session-quality-card` (its own article, between "What
      was recorded" and "Timeline"). Both reuse the existing generic
      `.status-pill` component with new intent-color modifier classes in
      `main.css` (`--clean`/`--warnings`/`--attention`/`--unknown` for
      health, `--sealed`/`--recording`/`--finalizing`/... for lifecycle,
      `--failed` already existed and needed no new rule) — the same shared
      style the four existing status pills already use, not a second badge
      system.

      **`SEALED` deliberately shows no badge.** It is the expected, ordinary
      outcome for a completed session; a badge that fires on every session
      is one nobody reads. Only the exceptional lifecycle states get one.

      Findings render as one line each via a small per-`kind` switch in
      `sessions-browser.js` (`formatQualityFinding`), using the project's
      existing `t(key, fallback).replace('{placeholder}', value)` pattern
      (see `moreHint`) — new locale keys added to **both** `en.json` and
      `de.json` in the same commit (key-set parity verified). Deliberately
      no chart, no raw jitter numbers, no journal viewer — three sentences,
      not thirty numbers, per the owner's explicit warning against
      over-detailing this UI.

      No new JS unit tests: `sessions-browser.js` exports only its two entry
      points today and has no existing unit-test coverage of its internal
      rendering helpers (`sessionTags`, `formatDuration`, etc.) to extend
      consistently with — adding a bespoke test harness for one new function
      would be inconsistent structure, not a fix. Verified instead by the
      Python-side route test asserting the exact `quality_summary` payload
      shape, `node --test tests/js` (27 passed, unchanged), a syntax check,
      and a DOM-id cross-reference between the new HTML/CSS/JS. Full suite
      **919 passed, 4 skipped**.
- [x] **A3** Withdrawal route + button. `POST
      /api/admin/sessions/<study>/<participant>/withdraw`, thin handler in
      `sessions.py` over the existing `WithdrawalService` (5i) — no new
      service, just its first caller. `recording_stopper` is wired in
      `apps/server/__init__.py` as `_stop_recording_for_withdrawal`: it
      freezes then shuts down the worker, catching its own internal errors
      ("no active recording" is not a failure) rather than letting them
      escape as a hard `WithdrawalError` that would need an operator.

      **Promoted `RecordingRuntimeService._find_paths` to public
      `find_paths`** rather than reaching into a private method across a
      package boundary from `apps/server`. One new caller (this route's
      resolution path) alongside the existing `refresh_lease`; the
      docstring says why there are now two.

      **New `sessions_index_service.resolve_session_root()`** rather than
      reusing `load_session()`: the route needs only "which folder", and
      `load_session()`'s XDF stream read would raise on a session an
      earlier, interrupted withdrawal had already partly emptied — a
      resumed withdrawal call must still resolve its target. Confirmed
      working against a tombstoned session too, since 5i's marker fallback
      in `_canonical_records` keeps a withdrawn session selectable.

      **`confirm_session_id` is checked server-side**, not only in the
      browser's two-step modal — a UI safeguard alone is not a validated
      boundary. Mismatch is a 400 before anything runs.

      **UI**: a `.btn-secondary--danger` trigger button plus a
      `createModal()`-based dialog (not `confirmWithModal()`, which only
      offers yes/no and cannot gate a button on typed input matching the
      session id). `already_published` destinations are rendered verbatim
      in the result toast, never summarized into "handled" — that list is
      the one thing the operator must act on personally.

      New `('POST', '.../withdraw')` route added to
      `test_route_inventory.py`'s `EXPECTED_ROUTES` characterization set —
      that test exists to catch *unintentional* surface changes, and this
      one is intentional.

      Full suite **922 passed, 4 skipped**; `node --test` 27 passed
      unchanged; structure baseline rewritten.

## Phase 5g — card extensions, full scope (owner decision 2026-09-09: build
completely, including cards becoming a real fourth extension type — not
only the JS/validation cleanup a narrower reading of the target doc would
allow)

Re-audited 2026-09-09, and the starting position is better than the target
doc's own description assumes: a real registry already exists
(`cards/index.js`: `CARDS`, `CARD_TYPES`, `defaultFor()`), and all 13
modules already share one interface (`meta`, `defaultQuestion`,
`renderStudy`, `renderEditor`, `collectConfig`, `collectAnswer`). The actual
gap is narrower and precisely located:

| # | What a new card type needs today | Should it? |
|---|---|---|
| 1 | `cards/card-X.js` | yes — correct |
| 2 | `cards/index.js` registration | yes — correct |
| 3 | `study-controller.js` `isAnswered()` (~1999–2032), 13 `type ==` branches | **no — leak** |
| 4 | `study-controller.js:5-8` + bind sites 657/767/1024/1027, 4 named hook imports | **no — leak** |
| 5 | `validation.py`, one normalize + one validate branch (22 branches total, two functions) | yes, but scattered |
| 6 | `renderStudy`'s header (type tag + prompt + instruction), copied into all 12 modules | **no — leak** |

`validation.py` is the **only** Python file that knows card type strings —
no service, no route. And the project has already solved leak #6's problem
once, for the editor side: `cards/card-info.js`'s own docstring says the
shared editor frame existed "because it was copied into nine card modules,
which is how they drifted out of order." The same extraction never happened
for the participant side.

Order, each stage independently useful and a valid stopping point if a
later stage hits the hard stop below:

- [x] **5g.B1** Golden fixture per card type, normalized through
      `validation.py`, output frozen. New
      `tests/support/card_type_fixtures.py` (data only) and
      `tests/test_card_type_fixtures.py` (the checks). One minimal, realistic
      author-written question per type in `ALLOWED_QUESTION_TYPES`, plus a
      submitted answer for every type outside `NON_ANSWER_QUESTION_TYPES`.

      **Frozen values are hand-audited against the source, not captured by
      running the code and trusting it.** They were generated once from the
      real `validate_and_normalize_config`/`validate_and_normalize_results`
      output, then checked line-by-line against the `_validate_question_by_type`
      and `_validate_answer_value` branches that produce them (participant-id's
      full nested `fields` block against `PARTICIPANT_FIELD_DEFAULTS` +
      `CONFIGURABLE_OPTION_DEFAULTS`; mood-meter/multi-slider/word-cloud's
      defaulted fields against their branches directly) before being
      committed as the frozen expectation. A test that only reruns the code
      and compares it to itself proves nothing; this proves the frozen value
      was correct on the day it was written, so a later diff means something.

      **`stimulus` is deliberately excluded from the exact-match check's
      `plugin_actions` key** (`STIMULUS_EXCLUDED_KEYS`). Real finding while
      building this: `normalize_card_plugin_actions()` reads the *live
      installed plugin registry* (`study_plugin_config._plugin_manifests()`),
      so a stimulus card's normalized output is not a pure function of the
      question data alone -- it silently depends on which plugins happen to
      be installed. Pinning it exactly would make this fixture fail for
      reasons that have nothing to do with card-type validation (a plugin
      gaining a `card_actions_schema` field). Worth remembering for 5g.B5:
      this coupling exists today and a card-as-extension design needs an
      opinion about it, not silence.

      **Fixture-set coverage is itself checked**, not just fixture content:
      one test asserts every `ALLOWED_QUESTION_TYPES` member has a fixture
      (catches a new card type shipping with no golden fixture), another
      asserts the reverse (catches a stale fixture for a removed type), and
      a third checks which types carry an `"answer"` key against
      `NON_ANSWER_QUESTION_TYPES` exactly. Without these, the safety net
      itself could silently stop covering what it claims to.

      6 new tests, all passing on first run against the real code. Full
      suite **928 passed, 4 skipped**; structure baseline unchanged (tests/
      is outside `measure_structure.py`'s and `file-guide.md`'s scope).
- [x] **5g.B2** Closed the three JS leaks, plus the header duplication.

      **`isAnswered()`** (`study-controller.js`) went from 13 type branches
      to: no question → answered; no `CARDS[type].isAnswered` export →
      answered (this is what makes `stimulus`/`finish` need no special case
      at all, unlike the old code); no rendered `cardElement` yet →
      answered (preserved exactly, including for `participant-id`, which
      the old code also gated on `cardElement` despite never reading it);
      otherwise delegate to `cardModule.isAnswered(question, index, {
      cardElement, touchedFieldCount })`. 11 modules now export it
      (`participant-id`, `likert`, `semantic`, `choice`/`single`, `slider`,
      `ranking`, `text`, `mood-meter`, `multi-slider`, `word-cloud`);
      `stimulus`/`finish` correctly export none.

      **The two named-import families were not the same shape and needed
      different fixes.** `onInput`/`onClick` (slider, mood-meter) were
      already called *unconditionally on every event*, self-filtering via a
      CSS-class check inside each handler -- multi-slider's range inputs
      reuse slider's `onInput` purely by sharing its `.js-slider-input`
      class, with no type-based lookup at all. Scoping these to
      `CARDS[currentQuestion.type]` would have silently broken that reuse.
      Fixed instead with `dispatchCardHook(hookName, event)`: call every
      *distinct* registered module's optional hook unconditionally
      (`new Set(Object.values(CARDS))` dedupes the `single`/`choice`
      alias), identical to what the named imports did. `participant-id`'s
      `onInput` was already registry-based (`CARDS['participant-id']?.onInput(event)`,
      not a named import) and has its own recursion guard against the
      `participantid:changed` event it dispatches -- left untouched and
      excluded from the generic dispatch rather than risk that guard.

      `bindDrag`/`bindCardEvents` are different in kind: one-time setup
      calls at a per-question render site that already has
      `cardModule = CARDS[question.type]` in scope. These became one
      `cardModule.bindInteractions?.(cardElement, questionIndex)` call.
      `card-ranking.js` gained a thin `bindInteractions()` wrapper doing
      the `.rank-list` lookup itself (moving that DOM knowledge into the
      module that owns it); `card-word-cloud.js`'s `bindCardEvents` was
      renamed directly to `bindInteractions` (its only caller, verified).

      **Header duplication**: new `renderStudyHeader(q, { icon, tagKey,
      tagFallback, prompt })` in `card-info.js`, following the file's own
      documented precedent for the *editor* frame ("copied into nine card
      modules, which is how they drifted out of order") -- same fix,
      applied to the *participant* side, which had the identical problem
      and no fix yet. Adopted by all 10 modules that share the exact
      three-line pattern (tag + prompt + instruction); `stimulus` and
      `finish` build genuinely different layouts and were correctly left
      alone. One dead import (`escapeHtml` in `card-text.js`, unused once
      its two calls moved into the shared header) caught and removed.

      **New safety net**: `tests/js/card-is-answered.test.mjs` (12 tests) --
      this refactor touched participant-facing logic that gates study
      progression and had *zero* prior JS test coverage anywhere in the
      codebase. No jsdom dependency exists in this project, so DOM access
      is a minimal hand-rolled stub (`querySelector`/`querySelectorAll`
      returning a fixed count), matching this test directory's existing
      pure-logic-only style rather than introducing a new one. `node --test`
      **39 passed** (27 prior + 12 new), unchanged Python suite (928 passed,
      4 skipped) since this package touched no `.py` file.
- [x] **5g.B3** `validation.py`'s scattered `if question_type ==` branches
      became two dictionaries: `_QUESTION_NORMALIZERS` (13 entries, one per
      `ALLOWED_QUESTION_TYPES` member) and `_ANSWER_VALIDATORS` (10 entries,
      `ALLOWED_QUESTION_TYPES` minus `NON_ANSWER_QUESTION_TYPES`). Each
      branch's body became its own named function
      (`_normalize_<type>_question` / `_validate_<type>_answer`), moved
      verbatim — no logic rewritten, only lifted out of an `if` and given a
      name. `_validate_question_by_type`/`_validate_answer_value` are now
      four-line dispatchers: look up the type, call the function, raise if
      absent.

      **Purely mechanical, and B1's fixtures proved it**: `test_validation.py`
      and `test_card_type_fixtures.py` (26 + 6 tests) passed against the new
      dispatch on the first run, unmodified. That is the entire point of
      building the fixtures before touching the branches — this refactor
      carried real risk (22 branches touched) and produced zero surprises.

      **One deliberate sharing preserved as one function, not thirteen**:
      `choice`/`single`/`ranking` share `_normalize_options_question`
      (an options list is their whole config shape) exactly as the original
      `if question_type in {"choice", "single", "ranking"}:` branch did —
      not split into three near-identical functions, which would have
      re-introduced copy-paste drift risk for no benefit.

      New `test_validation_dispatch_tables.py` (5 tests): checks the
      *tables themselves* are complete sets against `ALLOWED_QUESTION_TYPES`/
      `NON_ANSWER_QUESTION_TYPES`, and that the one deliberate sharing
      stays exactly the three types it should — a future card type added to
      the registry without a table entry now fails a direct assertion
      instead of surfacing as a confusing `ValidationError` the first time
      someone happens to exercise it.

      Full suite **933 passed, 4 skipped**; structure baseline rewritten
      (named functions cost a few more lines than compact `if` chains —
      expected, and the trade for one lookup point per type instead of
      searching among thirteen branches).
- [x] **5g.B4** Contract test + docs. New
      `tests/test_card_registry_contract.py` (6 tests): `CARD_TYPES` (JS)
      read as text and cross-checked against `ALLOWED_QUESTION_TYPES`
      (Python), every module's required exports
      (`meta`/`defaultQuestion`/`renderStudy`/`renderEditor`/`collectConfig`/`collectAnswer`),
      both `validation.py` dispatch tables, and the golden fixture set --
      all four checked against the JS registry's own type list, not only
      against each other, so a drift in `ALLOWED_QUESTION_TYPES` itself
      could not slip past every other check simultaneously.

      **No JS parser, on purpose.** Cross-language checking by reading JS
      source as text and matching with regexes already existed in this
      codebase (`test_web_ui.py`'s `WEB`/`_read` helpers, mirrored here
      directly) — introducing a JS-parsing dependency for a check plain
      regexes already answer would be new infrastructure for no new
      capability, exactly what `CONTRIBUTING.md` section 1 warns against.

      New "Adding A Card Type" section in `developer-guide.md`, matching
      the existing "Adding A Recording Sensor" section's style: the three
      registration points, when `isAnswered`/`bindInteractions`/`onInput`/`onClick`
      are needed (and that they are dispatched generically — never a new
      named import), and the golden-fixture step this contract test now
      enforces.

      Full suite **939 passed, 4 skipped**; `node --test` unchanged at 39;
      structure baseline unaffected (new test file is outside
      `measure_structure.py`'s scope).
- [x] **5g.B5** Cards are real extensions — completed 2026-09-09
      after the declarative-schema hard stop described below. All 13 question
      types are supplied by 12 trusted `extensions/cards/<key>/` directories;
      `choice` also supplies `single`. Each manifest declares a versioned
      `card_contract`, question and answerless types, host-data requirements,
      picker order and `card.js`. Duplicate or invalid providers never enter
      the live question-type lookup.

      Python remains the only source of card defaults, config normalization and
      answer validation. The host calls `card_defaults`, `card_normalize` and
      `card_validate_answer` through the existing `study-runner-stdio/v1`
      driver. The browser fetches defaults once per catalog generation before
      enabling an editor option or rendering a required participant card, then
      gives a defensive copy to the extension module. There are no separately
      authored JavaScript defaults. `stimulus` receives normalized installed
      plugin actions as declared host data; the extension never imports runtime
      services or receives application context or secrets.

      The browser imports declared card assets from the catalog. A missing
      required module blocks that study with a clear message, while a broken
      unrelated card does not stop usable cards. Backend worker isolation does
      not isolate extension JavaScript: all loaded `card.js` modules execute in
      the same browser page and therefore remain trusted shipped code.

      Card RPC failures use structured `invalid_input` and
      `extension_failure` kinds. Invalid input returns HTTP 400. A worker
      timeout, malformed response, handler crash, process exit or exhausted
      recovery returns HTTP 503 and never turns a failed answer into a saved,
      skipped or empty success. Timeout termination and bounded restart apply
      only to card workers. Each pending request is tied to the process instance
      that received it, so a late timeout cannot kill a replacement. Sensor,
      destination and output timeout behavior remains unchanged.

      Final automated suites: **961 Python passed, 4 skipped; 42 JavaScript
      passed; 25 release/packaging passed**. Acceptance evidence includes all
      golden card fixtures through real child processes; a truly blocked
      handler with another card and `/` still
      responsive; late response, malformed envelope, exception, exit, recovery
      lockout, three-restart exhaustion and successful recovery; result-recovery
      preservation and later valid retry; lazy startup/status with zero card
      workers; cached defaults and reused workers; a synthetic extension
      discovered through its directory alone; 42 JavaScript checks; source
      headless-Chrome editor add/save/reload plus participant preview; and a real
      Windows PyInstaller onedir build whose self-check exercised discovery,
      defaults RPC and the declared HTTP asset.

      Measured on the development machine: normal startup launched **0** card
      workers; cold loading all defaults launched **12** workers, took about
      **1.5 seconds** and used about **196 MiB aggregate RSS**; warm slider
      validation averaged about **0.21 ms per RPC**. The twelve-process memory
      cost is accepted for B5. Shared-worker or idle-lifecycle optimization is a
      separate architecture decision.

**Hard stop, exercised on 2026-09-09:** when the original schema could not
reproduce the existing semantics cleanly, implementation stopped for a scope
decision. That rule remains for future card-contract changes: report a semantic
incompatibility instead of forcing a migration to appear successful.

- [x] **5h** Recording checkpoints and bounded ingest (commits `0f7a2ba`,
      this package's second commit). Two halves, both landed:

      **Confirmed prefix.** New `contracts/recording_checkpoint.py` plus
      `SessionJournalWriter.append_checkpoint`. The commit order is the
      substance: write → `writer.flush(durable=True)` → append checkpoint →
      fsync the checkpoint. The fsync of the *claim* follows the flush of the
      *data*, so a checkpoint that survives a crash is a promise about the
      data under it; reversed, a surviving checkpoint could vouch for samples
      that never landed. `append_checkpoint` therefore returns whether its
      fsync succeeded — a checkpoint is a claim about durability, and a
      silently lost one would turn an honest "unknown tail" into a false
      "all present". Checkpoints carry generation + segment relative path,
      because a sample count only means something against the segment it was
      counted in. `freeze()` writes a closing `reason: "freeze"` checkpoint
      after `close(durable=True)`, confirming the whole segment.

      **Recovery reporting.** `recording_runtime_support.report_unconfirmed_tail`
      is called in `_reattach_or_recover` *before* the replacement generation
      starts, so a failed start does not also lose the only record of the
      previous segment's boundary. It writes `unconfirmed_tail` quality events
      naming the last confirmed count/timestamp per stream. A generation that
      closed with a `freeze` checkpoint produces **no** event — a warning that
      fires on healthy sessions is one operators learn to ignore. A generation
      with no surviving checkpoint at all still gets one event saying exactly
      that, since "nothing confirmed" is the strongest form of the warning.

      **Bounded ingest — what the doc asked for vs. what exists.** There is no
      internal queue to bound: `pull_chunk` writes straight into the native
      writer under the writer's own `RLock`, so streams are already isolated
      from each other by that lock and no per-stream queue was ever introduced.
      The bound that *does* exist is the LSL inlet's buffer
      (`INLET_BUFFER_SECONDS = 360`), and it is the dangerous kind: when full,
      LSL **discards the oldest samples without telling anyone**, and
      downstream that loss is indistinguishable from a transport stall — 5c
      would report it as a `gap` and blame the sensor for something the
      recorder did. New `IngestBacklogMonitor` watches the buffer's fill ratio
      (profile key `backlog_fill_ratio`, default 0.25) on the existing
      clock-offset tick and emits `ingest_backlog` edge-triggered: once on
      entry, once on clearing. Level-triggering would flood the journal during
      exactly the minutes an operator needs to read it. `peak_fill_ratio` rides
      along in every `summary`, so "the buffer never went past 2% full" is
      recorded as positive evidence even when nothing fired.

      **Deliberately not built:** a marker-priority scheduler. Markers already
      have their own stream and their own thread, and the shared writer lock is
      held only for the duration of one native write. A real priority scheduler
      over that is the kind of machinery `CONTRIBUTING.md` §10 rules out, for a
      contention problem that `peak_fill_ratio` will now make visible if it
      ever actually occurs. Revisit with evidence, not in advance.

      **Fault injection** at each commit boundary lives in
      `test_hybrid_recording_worker.py::CheckpointCommitBoundaryTests`: a
      failing data flush writes no checkpoint for that tick and stops the
      recorder; a lost checkpoint leaves the earlier prefix intact rather than
      retracting it; a recorder with no journals still records. Actual
      power-loss guarantees remain a platform/storage-specific release gate

      **Two test holes closed on the way.** The unwritable-directory tests in
      5c and 5h both used an invented absolute path (`/definitely/not/...`),
      which on Windows resolves under the current drive and *is* creatable —
      they passed while exercising no failure at all. Now a child of a regular
      file, which cannot be a directory on any platform. Worth remembering for
      any future "this path cannot be written" test
- [x] **5i** Withdrawal workflow. New
      `runtime_core/delivery/withdrawal_service.py`, five ordered steps
      (`stop_recording`, `cancel_uploads`, `delete_session_journals`,
      `delete_session_contents`, `write_tombstone`). The order is itself a
      safety property: writers stopped and queue drained *before* any
      deletion, so nothing can publish or re-create a file behind the
      deletion's back. 5a had already settled the destination (`WITHDRAWN`
      reachable from every state, nothing reachable from it), so this package
      only had to reach it safely.

      **Three design constraints, each one the obvious implementation gets
      wrong:**

      1. *The ledger must outlive what it deletes.* A withdrawal recording its
         progress inside the session folder erases that record halfway through,
         and an interrupted run could never tell "already deleted" from "never
         started". The ledger lives in `runtime/withdrawals/<session>.json`;
         the session tree is only ever a target. Every step is idempotent, so
         a crash costs a repeat of at most one step.
      2. *Deleting everything hides the withdrawal.* A folder that simply
         vanishes is indistinguishable from data loss — the exact silence this
         project spends its effort removing. Contents go, folder stays, holding
         only `WITHDRAWN.json`. **This forced a real change to the read path**
         (see below).
      3. *Published data cannot be un-published.* Completed uploads put a copy
         on someone else's server. The queue is stopped; the remote copy is
         *named* in the tombstone, never claimed deleted. Claiming external
         deletion without evidence is the one failure here no later check could
         catch.

      **Found by reading the real read path, not assumed:**
      `sessions_index_service._canonical_session_roots` requires
      `COMPLETE.json`/`ATTENTION_REQUIRED.json` *and* `_canonical_records`
      requires a result payload — both deleted by a withdrawal. A tombstoned
      session would have dropped out of the index entirely and the lifecycle's
      `withdrawn=` branch would never have been reached. `WITHDRAWN.json` is
      now a final marker in its own right, with a synthetic payload
      (`_withdrawn_payload`) carrying no answers and no file list.

      **Honest limitation, recorded rather than hidden:** the tombstone keeps
      its path, and the path contains the participant folder name. That is the
      one identifying trace it cannot shed — shedding it means removing the
      folder, which makes the withdrawal invisible again. Named in the code
      comment and in `how-recording-quality-works.md`.

      **Upload queue changes** (`upload_jobs_service.py`): new
      `cancel_session()`, a terminal `cancelled` status that the journal replay
      restores, deletion of the queued payload file (a queued job holds a
      *second copy* of the participant's data — deleting the tree while leaving
      it behind would be a withdrawal that did not withdraw), and three
      resurrection paths closed: `_record_failure` will not reschedule a
      cancelled in-flight job, `_run_job` will not mark it done, and explicit
      `retry(job_id=...)` refuses it. `counts()` reports `cancelled` so
      withdrawn work is visible rather than quietly absent.

      **Deliberately injected, not imported:** `recording_stopper` is a
      callable. Reaching into the host recording stack from here would tie the
      withdrawal path to a stack it must run without — a sealed session has no
      recorder left to stop.

      12 new tests. Not covered by design: withdrawing a session while the
      recorder is mid-write is exercised through the injected stopper, not
      against a live worker; that belongs to a Phase 6 hardware gate
- [x] **5j** Minimal extension SDK -- complete 2026-09-13. `tools/extension_sdk.py`
      adds three commands (`new`, `validate`, `check-runtime`) plus a
      generated schema reference (`schema --write`), and
      `tools/synthetic_lsl_source.py` pushes fake samples for a sensor's
      declared stream. None of this is a second copy of the real rules:
      `validate` calls `plugin_catalog.discover_plugin_catalog` directly
      (the same function `apps/server` uses), and `check-runtime` calls
      `process_host.build_process_plugin` to boot the plugin's real
      `driver.py` subprocess -- reusing the same test-only environment seam
      (`extension_layout.TEST_EXTRA_ROOT_*`) that
      `tests/support/fixture_plugin.py` already uses for the same reason.
      The generated schema deliberately covers only the manifest's outer
      shape (top-level fields, `ui`, `runtime`), not each capability's own
      fields -- writing those out by hand a second time is exactly the
      drift risk the 5g.B5 decision rejected a schema for; `validate`
      remains the one real check.

      One template per category (`tools/extension_templates/{sensors,cards,
      destinations,outputs}/`), each a small, real, working plugin, not a
      stub -- every template passes `validate` and `check-runtime` under
      its own shipped key, proven by `test_extension_sdk.py`.

      **Real bug found and fixed while building `check-runtime`, not
      invented for it:** `driver_runtime.py` crashed with `'list' object
      has no attribute 'get'` for any plugin whose manifest authors
      `capabilities` as a bare list instead of an object -- which
      `tests/fixtures/packaging_probe/manifest.json` (a real, shipped
      fixture) already does. Nothing in the existing test suite spawns that
      fixture's real subprocess, so this was never caught. Fixed by
      normalizing the list form the same way the real validator already
      treats it as equivalent; the SDK's own outputs-template test (which
      uses list-form `capabilities`) is the regression guard.

      Evidence: `test_extension_sdk.py` (9 tests, includes the schema-drift
      check and the synthetic-source tests) plus the full suite below.

## Update path: source-mode self-update (2026-09-14)

Not a numbered Phase item -- part of the release/update planning round that
also produced the MIT license switch above. One update path, not two: the
admin dashboard's existing Update panel (`apps/server/routes/update.py`,
`update_service.py`) already had a full signed-manifest-and-zip flow for a
packaged (frozen) build; it unconditionally refused in source mode
("use git pull or a fresh ZIP"). Owner decision: make the same panel actually
update a git checkout, instead of adding a second install path (a
resurrected Tkinter Manager, a signed feed) that would need a release-signing
key and, on macOS, notarization credentials the project does not have.

**What changed, in `update_service.py`:** `check_for_update`,
`download_and_stage_update`, and `request_update_install` each now dispatch
on `sys.frozen` at their very top -- source mode (not frozen) calls three new
private functions instead of the packaged-build logic that follows:

- `_check_for_source_update` fetches `study-runner-source-release.json` from
  the latest GitHub Release (`release_tools/build_source_release.py` already
  publishes it for every tag) and compares its `version` the same way the
  packaged flow already does (`compare_versions`) -- no signing key involved,
  since a source update is trusted through git and the release tag, not a
  signature.
- `_apply_source_update` is the fail-closed step: refuses (changing nothing)
  if no update was checked, a study session is active
  (`ACTIVE_STUDY_HARDWARE_CONFIG`), the checkout has no `.git`, it is not on
  `main` (the only branch that receives tagged releases), or it has local
  changes to tracked files. Only then does it run `git pull --ff-only`
  followed by the platform install script, and re-reads
  `study_runner/version.py` from disk afterward rather than trusting the
  network response for what version the checkout actually became.
- `_request_source_restart` reuses `_spawn_installer` as-is (it already had a
  non-frozen branch that runs `study_runner.updates.installer` as a module);
  only `installer.py` itself gained a source-mode branch
  (`_restart_source_checkout`), since the packaged branch's "launch this
  downloaded executable" logic has no source-mode equivalent -- there is
  nothing to launch but `server.py`, in place.

`is_install_supported` and `_base_status` both gained a source-mode
short-circuit (always supported; always "configured", no key needed) ahead of
their packaged-build logic, which is otherwise unchanged.

**A real design correction made mid-package:** the first draft dispatched on
"is a signing key configured" (matching the *old* code's own condition
literally), which would have kept running the packaged flow in a source
checkout that happened to have a key configured for testing. Four existing
tests relied on exactly that quirk. Re-read them against what they were
actually testing (not what they happened to exercise) and concluded the
`sys.frozen`-based dispatch is the correct one -- a source checkout should
never need a signing key at all. Fixed the dispatch and updated those four
tests to patch `sys.frozen` explicitly where they mean to test the packaged
path, matching the one test that already did this correctly.

**UI**: reused the existing three-button panel and its 46 `update.*` keys
entirely -- no new buttons, no new panel. `setUpdateActions` no longer
hard-disables Check in source mode; eight new locale keys (both `en.json` and
`de.json`) supply source-mode wording for the same states the packaged flow
already renders (checking, updating, ready, error), and two keys for a
dead branch (`source_mode` could never combine with `!configured` once
source mode became always-configured) were removed.

**Docs**: `docs/release-and-update.md`'s "Updating A Source Checkout" section
now leads with the dashboard panel, keeps the manual `git pull` steps as the
documented fallback (also what runs under the hood), and its "Legacy
Packaging Code" section now names `tools/study_runner_manager.py` explicitly
and states exactly what reviving it would need (signing key, and on macOS,
Apple credentials) -- neither this package nor any prior one touches that
file. Also corrected an unrelated latent inaccuracy found in passing: the
"Release Files" section implied `.zip` was Windows-only and `.tar.gz`
macOS-only; both are already built from the identical tagged commit
(`ARCHIVES` in `build_source_release.py`) and either works on any platform.

**Verified against real infrastructure, not only mocks:** a live dev server
in source mode reported `source_mode: true`, `configured: true`,
`install_supported: true` from `/api/admin/update/status`; a real POST to
`/api/admin/update/check` fetched the actual published
`study-runner-source-release.json` from GitHub and correctly reported
`1.0.0-dev` as newer than the latest published tag. `_apply_source_update`'s
fast-forward path is proven with real `git` subprocesses against a real
bare-repo-and-clone fixture (push a second commit to the "remote", pull it in
the "local" checkout, confirm `version.py` actually changed on disk) rather
than mocked git calls, since the whole safety argument rests on `--ff-only`
itself.

Evidence: 984 Python passed / 4 skipped (974 baseline + 10 net new/changed:
6 new `SourceUpdateTests`, 2 new `test_update_installer.py` tests, 2 rewritten
route tests, plus a live-server smoke test with a real GitHub round trip), 43
JavaScript passed, structure baseline rewritten (line growth in
`runtime_core/` and `updates/` from the new functions above; no new
cross-package edges or cycles).

### Phase 6 — Acceptance

- [ ] **6.1** Bundle builds and starts
- [ ] **6.2** A real 0.7.0 installation updates to 1.0 keeping studies,
      settings and sessions
- [ ] **6.3** A 0.7.0 session opens in the session browser; new artifacts are
      optional on read, mandatory on write
- [ ] **6.4** Docs: `developer-guide.md`, `file-guide.md`,
      `plugin-recording-architecture.md`, `README.md`, `CONTRIBUTING.md`;
      move `roadmap-0.5.md` (92 KB) to `docs/archive/`
- [ ] **6.5** `version.py` → `1.0.0`, `CHANGELOG.md`
- [ ] **6.6** Target doc §17 acceptance list, plus: plug-pull test with the
      measured loss recorded **as a number** in the operator documentation;
      marker parity journal ↔ XDF; withdrawal via `WITHDRAWN`; one migrated
      `.study-runner` per card type; hardware smoke test with BrainBit, MR60,
      tablet and camera

---

## File ownership

Add a row before starting. Remove it when the package is merged.

| Package / work item | Owner | Branch | Since |
|---|---|---|---|

No package is currently owned.

Completed: Claude implemented Phases 0-2, 5b, Phase 4 packages
`shared`/`contracts`/`data_core/{contract,worker,host}`/`runtime_core`
(commits `dec0908`..`2d40c4a`), Phase 3 items 3.1/3.2/3.3/3.5, and Phase 5
items 5e, 5d, 5a and 5c on 2026-09-08; Codex completed R1-R5 and the remaining
Phase 4 packages on 2026-09-08, then completed 5g.B5 on 2026-09-09 from
Claude's partial implementation; Claude completed the UI redesign (visuals
only, no functional change), 3.4 (plugin contract cleanup, `api_version: 5`)
on 2026-09-10, and 5j (minimal extension SDK) on 2026-09-13.

Rules:
- **Moves and tree-wide import rewrites are serial.** Approved R1–R4 repairs
  may run in parallel after explicitly claiming disjoint file ownership. One integrator owns
  the document and commits; concurrent agents do not stage or commit each
  other's files. CI edits are separated by job and coordinated
- **`contracts/` is not frozen** — every Phase 5 package needs to write to it.
  Instead: contract changes land as their own commits, additive only, never
  mixed with a feature commit, read by both agents.
- Known collision points, so assign them explicitly:
  `markers.py` and `recording_quality.py` (5c ∩ 5e) · `trial_service.py` (imports
  markers at `:17`) · `card_summary_service.py` (`:672` parses
  `desc/study_runner`, which 5d extends; also the card-answer summarizer 5g
  restructures) · `locales/{en,de}.json` (key sets are parity-enforced by
  `test_web_ui.py:29-60`; use a key prefix per package and let one agent merge)

---

## Development environment

```powershell
git worktree add C:\SR-1.0 -b feature/architecture-1.0
git config --global core.longpaths true
```

Short path deliberately — deeper nesting plus PyInstaller's `_internal/` gets
close to Windows `MAX_PATH`. The original directory stays on `main`; git refuses
to check `main` out twice, so the production tree is structurally pinned.

Rather than copying secrets into the second tree, point it at an external data
directory. `backend/services/settings/runtime_config.py:100-146` supports this
and `initialize_runtime_storage()` seeds `study_config.json`,
`hardware_settings.json` and the example studies on first start.

```powershell
$env:STUDY_RUNNER_DATA_DIR = "C:\StudyRunnerDev"
$env:STUDY_RUNNER_PORT = "3100"                        # default 3000
$env:STUDY_RUNNER_CERTIFICATE_DOWNLOAD_PORT = "3102"   # default 3002
$env:STUDY_RUNNER_NO_BROWSER = "1"
$env:STUDY_RUNNER_DISABLE_HARDWARE = "1"               # MANDATORY, see T6
```

Then hand-drop `local_secrets.json`, `ssl/` and `branding/` into
`C:\StudyRunnerDev\settings\`. `git status` in the rebuild tree stays clean and
no test run can touch field data.

**Copying `ssl/` matters:** without it `ensure_local_ssl_certificate` mints a new
root CA and every participant tablet that trusted the old one shows a
certificate warning.

Two more environment facts:
- `software/.build/xdf_core/` does not exist in a fresh checkout, so recording is
  fail-closed. Run `python tools/setup_recording_worker.py`, or copy the
  directory **including `worker-build.json`** (validation is content-hash based,
  not path based).
- DeepFace weights are gitignored (`model_assets/*.h5`) and are required for
  a bundle containing the camera plugin. The reduced CI packaging smoke excludes
  production plugins and does not require these weights. A complete release
  bundle must provision/cache the models and the canonical recording core;
  passing the reduced smoke does not certify those components.

Open a **draft PR to `main` on day one** — it gives PR-triggered CI regardless of
branch-name rules, a running diff, and a place to keep this checklist visible.

---

## Verification

```bash
python -m pytest software
python -m unittest -v release_tools.tests.test_pyinstaller_common   # new in 0.2
node --test software/tests/js/*.test.mjs
python tools/measure_structure.py --check                            # new in 1.5
git diff --check
```

After Phase 4 additionally: `test_import_boundaries.py`, `test_file_guide.py`,
`python tools/setup_recording_worker.py --probe-only --require-canonical`, and
the Flask-free subprocess import of `data_core`.

---

## Decision log

| Date | Decision | Reason |
|---|---|---|
| 2026-09-09 | 5g.B5 completed as genuine process-isolated card extensions reusing the existing API-v4 plugin framework; only card workers terminate and recover after RPC timeout, and browser defaults are fetched from Python and cached per catalog generation | Design work on the original scope found no clean implementation: a schema expressive enough to reproduce all 13 card types' exact semantics (including `stimulus`'s live-plugin-registry coupling) would be a large, novel, risky validation engine; a schema that does not drive validation is a second, undriven copy of `validation.py`, exactly the drift risk 5g.B1-B4 exist to remove. Raised to the owner as a hard-stop finding per the standing 5g hard-stop clause; owner supplied the revised architecture directly (process isolation via the existing plugin-framework, an executable contract instead of a schema, `stimulus`'s registry coupling kept host-side, a card-only timeout-termination policy with instance-bound bounded recovery). Accepted despite tension with `CONTRIBUTING.md` §7 ("extend the existing simple path instead of building a second system next to it") for the same reason the 5f/5j scope tension with §1/§10 was accepted on 2026-09-08 — explicit, detailed owner direction |
| 2026-09-08 | Keep the full Phase 5 target scope (including `mrg` CLI 5f and the extension SDK 5j) rather than trim it against `CONTRIBUTING.md` §1/§10 ("no structure for a hypothetical future need", "no heavy framework just to look architecturally clean") | Operator-directed after the tension was raised explicitly: the target document (`MRG_Recorder_Core_Architektur_1.0.md`) is the deliberate, current decision on functional scope. `CONTRIBUTING.md`'s other rules (clear names, thin handlers, why-comments, validate at every boundary) still apply in full to *how* each package gets built — only the scope question itself was in play, not the quality bar |
| 2026-09-08 | 3.2 (`upload_destination.legacy` migrate-and-write-forward) closed as verification-only, no code change | Traced the full persist call chain and found `normalize_study_settings_plugins`/`_remove_legacy_destination_fields` already migrate and strip every legacy flat field before any save, confirmed by two already-passing tests. This predates the 1.0 rebuild; 3.2 was not new work, just unverified until now |
| 2026-09-08 | Removed `_validate_plugin_object` from `plugin_catalog.py` in the same commit as 3.1's `_import_plugin` removal, rather than leaving it as a defensive check | It is dead code for its only remaining caller: `build_process_plugin` derives every handler it checks for directly and unconditionally from the same manifest's `capabilities` set, so the check is a tautology for anything build_process_plugin produces. It only ever caught anything for a hand-written v3 `Plugin` object, which could genuinely omit a handler while still declaring the capability — that possibility no longer exists |
| 2026-09-08 | Added `TEST_EXTRA_ROOT_PATH_ENV_VAR`/`TEST_EXTRA_ROOT_PACKAGE_ENV_VAR` to `plugin_framework/extension_layout.py`, read only from the environment | Three tests discover a synthetic plugin from a temp directory and (in one case) invoke a live handler through it; v4's real path spawns `driver.py` as a subprocess that resolves itself via `trusted_roots()`, hardcoded to the real `extensions/*` directories with no prior injection seam — a spawned subprocess has no access to a parent test's monkeypatches, only its environment. Read only from the environment, never a request or manifest value, so it can never become an attacker-controlled plugin path (CONTRIBUTING.md #1) |
| 2026-09-08 | Directory-move package boundaries for Phase 4: `contracts` gets `plugin_api.py`; `data_core/host` absorbs both old `recording/` and `backend/services/recording/`; `runtime_core` is exactly `studies`+`settings`+`delivery` | Matches D6/target package mapping; `data_core/host`'s merge specifically resolves the pre-1.0 split that existed only because `backend` importing `recording` eagerly at Flask-construction time was a real cycle risk before this package (with no Flask routes) existed |
| 2026-09-08 | `recording_finalization_adapter.py` placed in `backend/services/delivery/` (now `runtime_core/delivery/`), not in `data_core/host/` alongside the rest of the old `backend/services/recording/` | It is the one file in that directory that imports RuntimeCore-side code (`finalization_service.py`); leaving it in `data_core/host` would have created a real `backend <-> data_core.host` import cycle (backend already imports data_core.host extensively) once the merge closed the loop. Moved to the allowed direction (RuntimeCore → DataCore) instead of redesigning the finalization/DataCore ownership split under a move commit — that split is real, deferred design work (see "Split scientific sealing out of the current delivery services..." above), not something to improvise while relocating files |
| 2026-09-08 | New `shared/filename_sanitizer.py` for `sanitize_identifier_for_filename` | Needed by both `data_core/host/sensor_flush_service.py` and `backend/services/studies/results_service.py` (now `runtime_core/studies/`) after the Phase 4 merge; a pure, dependency-free helper neither area should own on the other's behalf |
| 2026-09-08 | Phase 4 executes with the per-package full-suite-green gate intact in practice, despite the operator's instruction to drop it for speed | Every package attempted so far surfaced a real latent bug (a broken subprocess-blocker rule, a fresh import cycle, a stale hardcoded test path) that would have been strictly more expensive to find later, mixed across several moves at once. The instruction to move fast was honored by keeping moves large and shim-free, not by skipping the one check that has caught something every single time |
| 2026-09-08 | Skip the per-package full-suite-green gate during Phase 4 in principle; Phase 3 and Phase 5c-5j deferred behind Phase 4 | Operator-directed: incremental, fully re-tested small steps were judged too slow relative to the rebuild's goal. `git mv` + same-commit import rewrite per package stays; only the interleaved test run was intended to be dropped (in practice it was kept — see the entry above). `main` still receives Phase 4 only as one merge once the branch suite is green again — the "main must keep working" constraint is unchanged, only its timing moved to the end of the phase |
| 2026-09-08 | 5b (preflight) complete; checkpoint structural baseline again | New `system_clock_probe.py`/`recording_capacity.py` plus wiring raise cross-package edges 151 to 152, `backend/` 19680 to 19965 lines, `shared/` 1536 to 1680; cycles remain 0. Expected growth from genuinely new preflight logic, not debt |
| 2026-09-08 | Complete R1-R5 and checkpoint structural baseline | Explicit contracts/generic upload checkpoint dependencies raise cross-package edges 147 to 151; framework LOC 3924 to 2603, contracts 1376; cycles remain 0 and largest module remains 2283 LOC. All metric gates remain enabled. |
| 2026-09-08 | User approved review package R1–R5 and requirements/ownership corrections; documentation updated before implementation | Restore trustworthy gates and fix concrete upload/recovery regressions before broad restructuring |
| 2026-09-08 | Prepare repairs in `.tmp/v1-review` on `fix/architecture-review`, then integrate into the existing rebuild branch | Existing `C:\SR-1.0` is outside this session's writable workspace; the additional worktree isolates development from operator data |
| 2026-09-08 | Keep session state separate from finalization/publication job states | Matches initial §10; publication failure does not invalidate an intact scientific seal |
| 2026-09-07 | Single top-level package `study_runner`, structure nested inside (D1) | Deviates from the target doc §3; invariants are about import edges, and the literal layout costs the PyInstaller spec, conftest bootstrap and 328 test references for no enforcement benefit |
| 2026-09-07 | Flat result folders stay readable (D2) | Contradicts target doc §13, but that section contradicts the project's own constraint that existing recordings stay readable |
| 2026-09-07 | One `1.0.0` release, full scope | Sized at 9–14 weeks; accepted. Phase 0 lands on `main` separately to preserve a hotfix path |
| 2026-09-07 | Cards ship in 1.0 with a hard stop after the pilot type | Highest data-corruption risk in the programme; the stop is the mitigation |

---

## Open questions -- both answered 2026-09-14

Both come from the target document §16 and had to be answered before the 1.0
tag. Kept below for the reasoning trail, not because they are still open.

- **Licence -- resolved: MIT.** The repository was public with `LICENSE`
  proprietary and full reservation of rights, while the stated goal was open
  source under EFRE funding -- visible but not usable, the worst of both.
  Owner decision: switch to MIT (`LICENSE`, `README.md`,
  `docs/release-and-update.md`, `release_tools/build_source_release.py` and
  its tests, `licenses/README.md`, `THIRD_PARTY_NOTICES.md`). One asset
  needed a separate decision on the way there: the Materiability heading
  font's rights belong to the Materiability Research Group, a third party,
  so it could not honestly ship under an MIT grant it does not hold. Owner
  decision: Materiability no longer ships in the repository at all (removed
  from git); Geist, already vendored under the SIL Open Font License, is now
  the shipped default for both headings and body text. `main.css`'s heading
  font stack already tried `'Materiability'` before falling back to
  `'Geist'`, so an operator with their own rights to the font can still add
  the three files back to `apps/ui/fonts/` and nothing else changes -- see
  that folder's own `README.md`.
- **Platform matrix -- resolved: Windows x64 + macOS canonical, Linux
  fail-closed.** Canonical recording already ran only on Windows x64 and
  macOS; Linux was already deliberately fail-closed
  (`recording/worker_binary.py`, `_core_target`), and
  `.github/workflows/ci.yml` already ran a three-platform `recording-core`
  matrix. Owner decision: keep this as the permanent shape, not a gap --
  Linux stays a source-regression platform only. Hardware smoke and measured
  power-loss tests remain separate release gates (Phase 6).

Additional questions raised during planning:

- Does target doc §3's `Extensions ──▶ DataCore` edge exist? No — extensions
  publish LSL streams that the worker consumes. There is no API dependency, which
  is precisely why invariant #2 is achievable. The diagram should show a dashed
  edge labelled "LSL".
- Target doc §8 promises the maximum power-loss data loss "as a number in the
  operator documentation". That number must come from the plug-pull test, not be
  derived from the flush interval — real loss also depends on the OS cache and
  the storage device.


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

Checkpoint: 4.7 complete. All six built-ins now live below categorized
`extensions/`; the obsolete `plugins` package is removed. One trusted-root
resolver serves catalog, child drivers, UI assets, self-check and packaging.
Cross-category candidates share the global conflict pass. Targeted evidence:
84 Python, 4 packaging and 4 JavaScript tests passed. Next: 4.8 `apps/ui`.

Checkpoint: 4.8 complete. `frontend` is now `apps/ui`; runtime data lookup,
PyInstaller, source-release licences and JS/Python path contracts moved with it.
HTTP paths did not change. Evidence: 73 targeted Python, 27 JavaScript and 25
release/packaging tests passed. Next: 4.9 `apps/server`.

Checkpoint: 4.9 complete. The Flask factory, routes and runtime implementation
live under `apps/server`; both `software/server.py` and the permanent
`study_runner/app_server.py` entrypoint remain. The old `backend` package is
removed. Evidence: 80 targeted Python and 4 packaging tests passed. Next is the
separate preflight UI/error-handling commit, then the Phase 4 tail sweep.

Checkpoint: the separate 5b usability repair is complete. Planned duration is
editable in study recording settings and remains the existing
`study_settings.planned_session_duration_minutes` contract. Capacity blockers
open/focus that setting; invalid UI values, invalid/non-finite stream rates and
storage-query errors fail closed with useful messages. Evidence: 109 targeted
Python and focused JavaScript tests passed; both locale files parse.

Final Phase 4 checkpoint: the path sweep, structural file guide, development
version, and real bundle gate are complete. The structure baseline moves from
152 to 235 visible edges because nested areas now expose the former monolithic
server and plugin relationships; cycles remain 0 and no forbidden dependency
was added. `contracts` is 1515 lines after the planned `plugin_api.py` move;
the largest module remains the 2283-line BrainBit adapter. Evidence: 814 Python
passed/4 skipped, 27 JavaScript passed, 25 release/packaging passed, structure
and `1.0.0-dev` version checks passed. A fresh Windows onedir build with only
the harmless packaging-probe extension passed UI/root resolution and its real
child-process initialize/status/shutdown RPC. Phase 4 is closed; next is the
deferred Phase 3 compatibility/plugin-contract work.

## B5 completion decision — 2026-09-09

Codex completed Claude's unfinished implementation, as explicitly requested by
the owner. Card workers alone gain timeout termination/recovery; sensor and upload
timeout behavior stays unchanged. Defaults are fetched from Python and cached in
the browser, not independently authored in JavaScript. Card calls are stateless
and never receive the application context or secrets. Discovery supplies the
type registry; no handwritten per-card lists remain. The initial
architecture proposal is historical; this decision supersedes the earlier B5
notes about generic timeout termination and duplicate defaults.
