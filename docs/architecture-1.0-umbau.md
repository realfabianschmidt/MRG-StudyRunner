# Architecture 1.0 Rebuild — Working State

> **Current implementation, 2026-09-08:** this `main` copy records the
> Phase 0 foundation. Phases 1/2, approved repairs R1-R5, preflight, and the
> Phase 4 directory move are implemented on `feature/architecture-1.0` at
> `64e7ffd`, in `C:\SR-1.0`.
> Continue with that checkout's `docs/architecture-1.0-umbau.md` and
> `docs/architecture-1.0-handoff.md`. The next package is deferred Phase 3
> compatibility/plugin contracts, then Phase 5c lifecycle and recovery.
> [Shared Claude/Codex entry point](architecture-1.0-handoff.md).
> The [versioned initial plan](architecture-1.0-initial.md) preserves the input;
> approved deviations and acceptance evidence live on the development branch.

This is the shared working document for the 1.0 architecture rebuild. Two agents
(Claude Code and Codex) work on it in parallel, so **this file is the single
source of truth for what is done, what is in progress, and who owns which
files.**

The target architecture is described in [the initial plan](architecture-1.0-initial.md)
(German). This document does not restate it; it tracks execution against it.

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

Status: **Phase 0 complete, on `main`.** Phase 1 (branch) not yet started.
Version stays `0.7.0` until Phase 4.

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
| D1 | **`study_runner` stays the single importable top-level package.** The 1.0 structure is nested *inside* it, not placed beside it as `software/{apps,packages,extensions}`. | Every invariant in §14 of the target document is about **import edges**, and edges are enforceable in any layout. The literal top-level layout would break `collect_submodules("study_runner.backend")`, the single `sys.path` entry in `software/tests/conftest.py`, `DEFAULT_PACKAGE_NAME`, 328 `study_runner.` references across 66 test modules, and the `(root/"study_runner").exists()` software-root probe in the PyInstaller spec — in exchange for a nicer directory listing. **This deviates from the target document; §3 there should be corrected.** |
| D2 | **Legacy flat result folders are NOT removed** (contra target doc §13). | `software/saved_results/Example_Sensors_Study/` is in that shape on disk right now, as is every session recorded before the layout change. A major release that removes the ability to read a researcher's earlier data is not cleanup. Keep the read path, pin it with a fixture test, and rename it in the docs from "legacy" to "archival compatibility surface". Remove only remaining *write* paths. |
| D3 | **`upload_destination.legacy` is migrated before it is removed.** | It reads fields out of live operator `.study-runner` files. Order: migrate and write forward → ship one release → then remove the read path. |
| D4 | **Work happens in a second worktree**, `git worktree add C:\SR-1.0 -b feature/architecture-1.0`, with an external data directory. | The production install must keep working. See [Development environment](#development-environment). |
| D5 | **No compatibility shims for moved modules**, with one exception: `software/server.py` and `study_runner/app_server.py` stay permanently, and the **built executable name never changes** (`study-runner-server` / `study-runner-server.exe`). | Every consumer of the old paths is in-repo and editable. A shim would re-introduce exactly the import edge the rebuild removes, and would defeat git rename detection. `server.py` is not a shim — it is the documented entry point baked into the install/start scripts and the PyInstaller root probe, and the packaged app re-invokes *itself* with `server.py --flags`. The executable name is a separate, harder constraint: the 0.7.0 updater already deployed in the field finds the new build by literally globbing for that filename (see Phase 0.6) — nothing in the 1.0 codebase can fix a 0.7.0 client that already shipped. |
| D6 | **Target layout (per D1):** | `study_runner/{apps/{server,cli,ui}, contracts, runtime_core, data_core/{contract,host,worker}, plugin_framework, extensions/{sensors,cards,destinations,outputs}, shared, updates}` |
| D7 | **`study_runner/updates/` does not move in Phase 4.** | See Phase 0.6 findings below — it is the one path a live field machine actually executes with 0.7.0 code during an update, moving it buys no architectural benefit and adds real risk. |

---

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

## Current state (verified against the code)

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
rename breaks (`"study_runner/frontend"`, `"study_runner.backend"`), but
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
- [ ] **0.1** Record the import-root decision (done: D1) and confirm no
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

### Phase 1 — Invariant harness (branch)

- [ ] **1.1** AST-based import walker at `software/tests/support/import_graph.py`
      (must catch function-local imports; keep the subprocess-blocker technique
      from `test_area_boundaries.py` — it catches transitive edges static
      analysis misses)
- [ ] **1.2** `test_import_boundaries.py` with a `KNOWN_VIOLATIONS` allowlist of
      exactly the 11 edges. Fails when an edge is **added**, and when a listed
      edge is fixed but not removed from the list. **Not a permanently red
      test** — that trains everyone to ignore red and cannot merge to `main`
- [ ] **1.3** Invariants already true, as green regression locks: #5 (only
      `xdf.py` and `lsl_recording.py` write XDF bytes), #4 (widen the existing
      `test_no_core_module_names_a_plugin.py`), #3
- [ ] **1.4** Invariant #6 in a mechanically checkable form (field-name denylist
      over persisted JSON; "no computed global time" is not testable as written)
- [ ] **1.5** `tools/measure_structure.py` — cross-package import edges, cycles,
      lines per package, largest file. **Ratchet against a committed baseline,
      not an absolute threshold.** Current outliers: `plugin_catalog.py` 1709,
      `finalization_service.py` 1516, `validation.py` 1351, `recording_runtime.py` 1150
- [ ] Invariants #7 and the lifecycle-dependent parts defer to Phase 5a

### Phase 2 — Break the import edges in place (branch, zero moves)

Best value-to-risk ratio in the programme. Afterwards every import invariant is
true with no path changes and no packaging changes.

- [ ] **2.1** `is_frozen` / `get_app_mode` / `get_project_base_dir` → `shared/`,
      `runtime_config` re-exports. Kills 5 edges. Safe only after 0.3
- [ ] **2.2** `PluginContext.secret` takes an injected `secret_resolver`, wired
      in `registry.build_context()`. Kills the last `plugin_framework → backend` edge
- [ ] **2.3** `notion_upload`: `PARTICIPANT_FIELD_ORDER` → `contracts`; then
      design a `request_study_config` / `write_study_plugin_config` capability
      for `:854-877`. **Real design work — budget for it**
- [ ] **2.4** `validate_and_normalize_manifest` → `contracts`,
      `ensure_requirements` → `shared`. Side benefit: core recording stops
      importing 1709 lines of `plugin_catalog.py` at startup
- [ ] **2.5** Break the host↔worker cycle: create `recording/contract/` with the
      wire types, lease schema, backup projection model, and `CoreProbe` split
      out of `core.py` (the ctypes binding stays). `worker_binary.py:60` already
      has the injection seam (`core_probe: Callable[...]`)

### Phase 3 — Legacy removal (branch)

- [ ] **3.1** Remove `_import_plugin`; make `entry_point` optional — see [T4](#t4--entry_point-cannot-be-removed-before-the-v3-import-path) and [T3](#t3--narrowing-supported_plugin_api_versions-to-4-breaks-the-server)
- [ ] **3.2** `upload_destination.legacy`: migrate and write forward (D3)
- [ ] **3.3** Rename "legacy flat result folders" to "archival compatibility
      surface" in the docs and pin with a fixture test. **Do not remove** (D2)
- [ ] **3.4** Merge capabilities `readiness` / `runtime_control` / `health` into
      one lifecycle contract, `api_version: 5`. This is a manifest-contract
      rewrite touching all six plugins, `study_readiness_service.py`,
      `plugin_health_poll_service.py` and the generic admin UI — **not a
      removal**, despite where the target doc files it. 3–5 days
- [ ] **3.5** Fix doc drift: `plugins/README.md` still claims "Every manifest
      uses api_version: 3"

### Phase 4 — Directory move (branch)

Two rules: **never mix a move with a behaviour change in one commit**, and **one
commit per target package**.

Per package: `git mv` → in the *same* commit, mechanically rewrite imports for
that one prefix across the whole tree → confirm rename detection with
`git show --stat -M90%` → full suite green → next package.

Order: `shared` → `contracts` → `data_core/contract` → `data_core/worker` →
`data_core/host` → `plugin_framework` → `runtime_core` → `extensions/*` (one
commit per category, derived from each manifest's `category`) → `apps/ui` →
`apps/server`.

- [ ] **4.0** `git config merge.renameLimit 4000` and `diff.renameLimit 4000`
- [ ] **4.1**–**4.10** one commit per package, in the order above
- [ ] **4.11** Hand-edit the two dynamic import sites: `driver_runtime.py:28`
      (`f"study_runner.plugins.{…}.plugin"`) and `plugin_catalog.py:28`/`:31`.
      `discover_plugin_catalog` already parameterises `plugins_dir` and
      `package_name` (`:177-185`), so multi-root discovery is a change to
      defaults and callers, not to the discovery logic
- [ ] **4.12** Extend `_MOVED_PLUGIN_PATHS` — see [T7](#t7--operator-stored-plugin-paths-break-on-a-folder-move)
- [ ] **4.13** Pull the rest along: `study_runner_server_common.py` (15 path
      literals) · `build_source_release.py` · `build_python_onedir.py` ·
      `build_python_update_manifest.py` ·
      `tools/{setup_recording_worker,study_runner_manager,make_timeline_fixture}.py` ·
      `tools/{install,start}-{windows.ps1,macos.sh}` · `ci.yml` ·
      `.gitattributes` · `release_tools/tests/test_pyinstaller_common.py` ·
      the ~30 `Path(…)/"study_runner"/…` literals in `software/tests/`
- [ ] **4.14** Rewrite `docs/file-guide.md` structurally, **once, at the end**
      (the test only checks name presence, so it stays green throughout), and
      add ~20 lines asserting every backticked path in the guide exists on disk
- [ ] **4.15** `version.py` → `1.0.0-dev`
- [ ] **4.16** **Build and start a bundle now**, not at the end of Phase 6 —
      otherwise a packaging break sits undetected in the branch for weeks

Keep the branch fresh with `git merge main` daily. **Do not rebase** — it
re-derives rename detection on every replay and will eventually lose a file's
history.

### Phase 5 — New capabilities (branch, partly parallel)

- [ ] **5a** Session lifecycle enum. Keystone for everything else. This is a
      *merge* of two state machines that currently disagree: `recording-plan.json`
      (`starting/recording/frozen/recovering/attention_required`) and the
      finalization job states — `attention_required` means different things in
      each. Design work, not a rename
- [ ] **5b** Preflight: free disk space vs planned duration × measured write
      rate; system-clock plausibility and a running time service. ~200 LOC, no
      coupling, highest operator value per line in the whole document.
      **Build early**, right after Phase 2
- [ ] **5c** `quality.jsonl` + `timing.jsonl` during recording. **Not merely a
      relocation**: `recording_quality.py:8-15` imports `markers` (→
      `plugin_framework`) and reads plugin manifests, so moving it into the
      worker violates invariant #2. Its signatures also consume fully parsed XDF
      artifacts, while live QC needs streaming counters. Needs its own data
      model, and must come after 2.5 and 5e
- [ ] **5d** Stream contracts: freeze at start, persist as
      `stream-contracts.json`, write into the XDF header. Partly built already —
      `plugins/brainbit/adapter.py:741` has `_actual_stream_contracts` and every
      manifest declares `streams[]`. See [T8](#t8--the-stream-contract-in-the-xdf-header-changes-recorded-bytes)
- [ ] **5e** Shared `event_id` across journal and LSL markers, dedup and compare
      at finalization, mismatch is a quality event. ~70% of the substance exists
- [ ] **5f** `mrg` CLI. Last. "the same local command API as the UI" **does not
      exist** — the UI speaks HTTPS to Flask. Either the CLI does TLS and auth,
      or a new IPC surface gets designed. `software/server.py` keeps working
      regardless (D5)
- [ ] **5g** Card extensions. **Only after Phase 4 is complete, never in
      parallel.** 13 registered types across 14 files in
      `frontend/scripts/cards/`, hand-written normalizers per type in
      `validation.py` (~lines 848–1000 plus ~15 `type ==` branches), and
      `participant/study-controller.js:5-8` reaches into four card modules **by
      name** — the registry is not a complete abstraction today. Every existing
      `.study-runner` encodes the current type strings.
      Order: pilot `card-slider` completely → freeze the contract against it →
      one golden fixture per card type → the remaining twelve.
      **Hard stop after the pilot** if the contract cannot reproduce
      `validation.py`'s semantics exactly; cards come out of 1.0 rather than
      being forced through

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
| Phase 0 (complete) | Claude Code | `main` | 2026-09-07 |

Rules:
- **Phases 0–4 are serial, one agent.** Moves and import rewrites are
  tree-wide; two concurrent runs guarantee conflicts.
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
- DeepFace weights are gitignored (`model_assets/*.h5`) and
  `study_runner_server_common.py:41-47` raises without them. The
  `packaging-smoke` job needs `release_tools/fetch_deepface_model_assets.py` to
  run first, and should cache the result.

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
| 2026-09-07 | Single top-level package `study_runner`, structure nested inside (D1) | Deviates from the target doc §3; invariants are about import edges, and the literal layout costs the PyInstaller spec, conftest bootstrap and 328 test references for no enforcement benefit |
| 2026-09-07 | Flat result folders stay readable (D2) | Contradicts target doc §13, but that section contradicts the project's own constraint that existing recordings stay readable |
| 2026-09-07 | One `1.0.0` release, full scope | Sized at 9–14 weeks; accepted. Phase 0 lands on `main` separately to preserve a hotfix path |
| 2026-09-07 | Cards ship in 1.0 with a hard stop after the pilot type | Highest data-corruption risk in the programme; the stop is the mitigation |

---

## Open questions

Both come from the target document §16 and must be answered before the 1.0 tag.

- **Licence.** The repository is public, `LICENSE` is proprietary with full
  reservation of rights, the stated goal is open source, and the project runs
  under EFRE funding. The current combination — visible but not usable — is the
  worst of both. Also to clarify: whether the funding agreement mandates a
  licence.
- **Platform matrix.** Canonical recording runs on Windows x64 and macOS; Linux
  is deliberately fail-closed (`recording/worker_binary.py`, `_core_target`), and
  `.github/workflows/ci.yml` already runs a three-platform `recording-core`
  matrix. The decision is therefore narrower than the document implies: **does a
  canonical Linux build get added?** Today's consequence is that the cheap CI
  runner cannot exercise the most critical path, so the real safety net is the
  hardware smoke test as a release gate.

Additional questions raised during planning:

- Does target doc §3's `Extensions ──▶ DataCore` edge exist? No — extensions
  publish LSL streams that the worker consumes. There is no API dependency, which
  is precisely why invariant #2 is achievable. The diagram should show a dashed
  edge labelled "LSL".
- Target doc §8 promises the maximum power-loss data loss "as a number in the
  operator documentation". That number must come from the plug-pull test, not be
  derived from the flush interval — real loss also depends on the OS cache and
  the storage device.
