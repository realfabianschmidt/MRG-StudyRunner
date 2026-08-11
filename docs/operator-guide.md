# Operator Guide

Study Runner is a local Python server with an admin page, a participant page,
and trusted built-in plugins for lab integrations. After the one-time source
installation, start it with `tools/start-windows.ps1` or
`bash tools/start-macos.sh`; no virtual-environment activation is needed.

## Main Parts

- `software/server.py`: starts the local server.
- `software/study_runner/backend/`: routes and focused backend services.
- `software/study_runner/plugins/`: manifest-driven (API v4) plugins —
  BrainBit, MR60 mini-radar, camera/emotion, Notion, Nextcloud, and
  OSC/TouchDesigner.
- `software/study_runner/recording_worker/`: detached Python recording worker.
- `software/recording_worker/native/`: small native XDF-core source.
- `software/study_runner/frontend/`: browser pages, styles, scripts, cards, and
  locales.
- `software/study_content/settings/`: active study, machine settings, and local
  secrets.
- `software/study_content/studies/`: saved study presets.
- `software/saved_results/`: canonical participant sessions.
- `docs/plugin-recording-architecture.md`: detailed recording/data contract.

## First Install And Daily Start

From a fresh GitHub checkout, use the platform installer once:

```powershell
.\tools\install-windows.ps1 -InstallSystemDependencies
```

```bash
bash tools/install-macos.sh --install-system-dependencies
```

The Windows script uses WinGet for Python 3.12, CMake, and the Visual Studio C++
workload. The macOS script uses Homebrew for Python 3.12/CMake and requires the
Apple Command Line Tools (`xcode-select --install`). Both create `.venv`,
install `software/requirements.txt`, build the small native XDF core, run CTest,
and perform a synthetic XDF smoke test. Re-running the installer repairs or
updates the environment without deleting study content or results.

The installers resolve requirements through the checked-in CPython 3.12
compatibility constraints under `software/constraints/`. The common set is
used everywhere; the local-emotion set is added on Windows x64 and macOS Apple
Silicon. They pin the release-tested direct and high-risk inference packages,
not every transitive wheel or its hash. Clean platform release jobs remain the
final compatibility check.

Daily start:

```powershell
.\tools\start-windows.ps1
```

```bash
bash tools/start-macos.sh
```

Windows x64 and macOS Intel/Apple Silicon are the supported recording
platforms. If the core is absent or stale, non-recording studies still run; a
study requiring recording is blocked with the setup hint. Advanced developers
can invoke `python tools/setup_recording_worker.py` directly inside the active
environment.

## How A Study Run Works

1. Start the Python server and open `/admin`.
2. Load or edit the study.
3. Confirm required plugin readiness. Every required recording source must be
   connected, have its XDF segment open, and have delivered a fresh sample.
4. Open the participant URL on the assigned tablet over trusted HTTPS.
5. Start the run from Admin. The participant enters the pseudonymous ID.
6. The detached worker records each plugin to its own XDF, plus the slowest-grid
   QC backup and hidden marker/clock streams.
7. Browser timers use real monotonic deadlines. A hidden tab does not pause a
   stimulus.
8. On Submit, the participant submission is committed locally. Only then does
   the completion page appear.
9. The Admin finalization widget shows freeze, source validation, merge, merge
   parity, card statistics, manifest, Notion, Nextcloud, and guarded purge.
10. A valid run ends with `COMPLETE.json`; a quality problem uses
    `ATTENTION_REQUIRED.json`.

## Plugin-Driven UI

The server discovers trusted integration folders and validates their manifests
before import. Sensor choices, settings, readiness, status, and actions come
from the public plugin catalog. Notion/Nextcloud may appear only in destination
settings and finalization. XDF, markers, and clock diagnostics are
infrastructure and have no separate user menu.

Camera capture and emotion analysis are one `camera_emotion` plugin. Its local
and remote workers are operating modes, not additional plugins.

## Camera And HTTPS

The tablet camera requires HTTPS and trust in the local Study Runner Root CA.
Camera frames can update the admin monitor before a run; raw frames are not
stored as session video. During a selected run, derived emotion values are sent
through the plugin's host LSL bridge.

The local DeepFace worker starts and restarts behind `camera_emotion` on Windows
x64 and macOS Apple Silicon. Current TensorFlow/tf-keras wheels do not provide a
CPython 3.12 macOS Intel build; on Intel, configure `remote_worker`. This does
not limit the server or XDF recording. Repair or dependency actions are exposed
only when declared by the plugin manifest.

Useful diagnostic server flags remain:

- `--emotion-worker-self-test --json`
- `--emotion-worker`
- `--apply-update`

## If Something Goes Wrong

- A missing required plugin blocks Start and shows concrete readiness details.
- A sensor disconnect triggers reconnect attempts and records a visible gap;
  it does not stop the participant timer.
- A lost tablet or Flask process starts a 15-minute recording lease. The worker
  continues and can be reattached after a Flask restart.
- A worker restart creates a new XDF segment instead of modifying an existing
  fragment.
- If the local submission commit fails, the participant completion page is not
  shown and the current browser/snapshot data stays available.
- Source, merge, or statistics failure becomes `attention_required`, never
  silent completion.
- Notion and Nextcloud failures are independent, persistent, retryable jobs and
  do not affect the participant screen.

Open the finalization details to inspect warnings and artifacts. Retry the
specific failed step first. If data loss is real and scientifically acceptable,
an admin may confirm degraded completion with a written reason. This creates
`completed_degraded` and preserves the warning in published output.

## Data And Purge Safety

Each repeated participant run receives a new UTC/session-ID folder. Nextcloud
mirrors only that folder and uploads the completion marker last. Raw source XDFs
are removed locally only after valid merge parity and SHA-256-verified remote
copies. Without verified Nextcloud publication, raw sources remain local.

The backup XDF is deliberately reduced to the slowest declared rate and is a
recovery/QC artifact, not a substitute for native raw streams.

## Where To Change Common Things

- Change a study in Admin or edit `study_config.json`.
- Change plugin machine settings through the manifest-generated settings UI or
  `hardware_settings.json`.
- Add a card type below `web/scripts/cards/` and register the type in the card
  index.
- Add a plugin package with `manifest.json` and `plugin.py`; no central sensor
  import list is needed.
- Install or repair the complete source environment with the platform script in
  `tools/`; build only the recording core with
  `python tools/setup_recording_worker.py`.
- See `docs/release-and-update.md` for source updates and release acceptance.
