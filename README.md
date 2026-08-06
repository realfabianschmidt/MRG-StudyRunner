# Study Runner

Study Runner is a local app for running small user studies in a lab, classroom,
workshop, or design research setting.

Start here if you are not developing the code:

```text
docs/start-here.de.md
```

## Project Layout

Most app work happens in `software/`. Source-archive and release helpers live in
`release_tools/`; old packaged-app helpers are not part of the active release.

In the local lab workspace, `../Sensorik/` is intentionally kept next to this
repo as the hardware reference and experiment folder. Runtime-ready copies live
inside `software/study_runner/plugins/`.

```text
Software/
|-- README.md              This file.
|-- CONTRIBUTING.md        How we keep the code readable.
|-- release.ps1            One-command release from the repo root.
|-- docs/
|   |-- start-here.de.md        German guide for non-coders.
|   |-- operator-guide.md       Daily operation and project overview.
|   |-- sensors-and-data.md     Sensor flow, timing, XDF/LSL, data files.
|   |-- plugin-recording-architecture.md  Plugin/worker/XDF architecture.
|   |-- developer-guide.md      Backend/frontend/plugin development rules.
|   |-- release-and-update.md   Source releases, updates, acceptance gates.
|   |-- README.md               Documentation index.
|   `-- archive/                Historical plans and audits.
|-- software/              THE PROGRAM.
|   |-- server.py          Run locally with: cd software && python server.py
|   |-- requirements.txt   Python dependencies.
|   |-- constraints/       Release-tested Python 3.12 compatibility pins.
|   |-- study_runner/      The application itself, see the map below.
|   |-- study_content/     Editable default studies and settings.
|   |-- recording_worker/  C++ source of the XDF core the worker is built on.
|   `-- tests/             Automated checks.
`-- release_tools/         Versioning, source archives, validation, release automation.
```

Inside `software/study_runner/`, each folder is one area and carries its own
README:

```text
study_runner/
|-- backend/           The server: routes/ says what a URL means,
|                      services/ does the work behind it.
|-- frontend/          Everything the browser loads: pages, scripts,
|                      styles, locales, fonts.
|-- recording/         Host side of recording: starts the worker, owns the
|                      session folder, reads the XDF back.
|-- recording_worker/  The separate process that writes the XDF.
|-- plugins/           One folder per plugin, all equal. Drop a folder with
|                      a manifest.json in and it appears everywhere.
|-- plugin_framework/  The machinery that finds and runs those plugins.
`-- updates/           Verifying and applying a signed update.
```

Local study results are written to `software/saved_results/` and are ignored by
Git.

## Quick Install From GitHub

The supported source-server setup uses Python 3.12 and a repository-local
`.venv`. The install scripts also build and test the small native XDF core.
They are safe to run again after an update and never delete studies or results.
No Apple signing or notarization is needed for this workflow.

Both installers use the checked-in Python 3.12 compatibility constraints in
`software/constraints/`. Windows and Apple Silicon install the local emotion
stack; macOS Intel intentionally uses only the common set and `remote_worker`.
These constraints pin the release-tested direct and high-risk ML versions, but
are not a hash-locked offline wheel bundle.

### Windows x64

Open PowerShell. On a new computer, install Git once:

```powershell
winget install --id Git.Git --exact --source winget
```

Open a new PowerShell window, then clone and install:

```powershell
git clone https://github.com/realfabianschmidt/MRG-StudyRunner.git
cd MRG-StudyRunner
.\tools\install-windows.ps1 -InstallSystemDependencies
```

The explicit switch installs missing Python 3.12, CMake, and Visual Studio C++
Build Tools through WinGet. Windows may show a UAC prompt. If PowerShell blocks
local scripts, run the installer once with:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\install-windows.ps1 -InstallSystemDependencies
```

Every later start is one command:

```powershell
.\tools\start-windows.ps1
```

### macOS Intel or Apple Silicon

On a new Mac, install Apple's compiler tools and complete the dialog:

```bash
xcode-select --install
```

Install Homebrew with its official installer, follow the printed `Next steps`
for your shell, then clone and install Study Runner:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Continue with:

```bash
git clone https://github.com/realfabianschmidt/MRG-StudyRunner.git
cd MRG-StudyRunner
bash tools/install-macos.sh --install-system-dependencies
```

The script runs `brew install python@3.12 cmake` idempotently. Every later start
is one command:

```bash
bash tools/start-macos.sh
```

On Apple Silicon, `camera_emotion` supports its local DeepFace worker. Current
TensorFlow/tf-keras wheels do not support CPython 3.12 on macOS Intel, so the
Intel installer provides the server and full XDF recording but intentionally
skips the local analysis stack. Configure `camera_emotion` with
`remote_worker` on an Intel Mac.

There is no need to activate `.venv`; the scripts always use its interpreter
directly. For a non-recording installation, pass `-SkipRecordingCore` on
Windows or `--skip-recording-core` on macOS. Required sensor-recording studies
will remain blocked until the full installer has successfully built the core.

### Update A Source Checkout

Updates preserve the ignored local study data and results:

```bash
git pull --ff-only
```

Then rerun the platform's install script without the system-dependency switch,
and use its start script. The installer refreshes Python dependencies and only
rebuilds the XDF core when its verified build is missing or stale.

The terminal prints the available addresses:

- Admin page: `https://localhost:3000/admin`
- Participant page: `https://<computer-ip>:3000`

HTTPS is enabled by default so tablet camera access can work. Study Runner
creates a persistent local Root CA in `software/study_content/settings/ssl/` and
prints the exact `.crt` path on startup. The certificate files and private keys
are local machine state and are intentionally ignored by Git.

For iPad camera access:

1. Start Study Runner once.
2. Copy the printed `study-runner-local-root-ca.crt` to the iPad. If iPadOS does
   not recognize it as a certificate, rename the copy to `.cer`.
3. Install it under `Settings > General > VPN & Device Management`.
4. Enable full trust under `Settings > General > About > Certificate Trust Settings`.
5. Open the printed `https://<computer-ip>:3000` participant URL.

Every server computer creates its own Root CA. When moving to another computer,
install that computer's newly generated certificate on the tablet. To disable
HTTPS for a local non-camera debug run, set `STUDY_RUNNER_HTTPS=0` before
starting.

Optional runtime settings:

```bash
STUDY_RUNNER_HOST=0.0.0.0
STUDY_RUNNER_PORT=3000
STUDY_RUNNER_HTTPS=0
STUDY_RUNNER_CONTENT_DIR=/path/to/study-content
STUDY_RUNNER_DATA_DIR=/path/to/writable/app-data
```

## GitHub Source Releases

The active release workflow publishes auditable source archives, not a desktop
app, Manager, PyInstaller server, or automatic updater:

```text
https://github.com/realfabianschmidt/MRG-StudyRunner/releases/latest
```

Use `study-runner-source.zip` on Windows or
`study-runner-source.tar.gz` on macOS. `SHA256SUMS` and
`study-runner-source-release.json` identify and verify the exact release. The
archives never contain generated native libraries, local results, credentials,
or certificates. First install builds the XDF core locally and proves it with
the same platform smoke tests used for release acceptance.

Signing and notarization are unnecessary for this source-server workflow. Old
PyInstaller/Manager/updater code is retained only as legacy or possible future
work and is not published by the current release workflow.

## Sensor And Camera Runtime

Current built-in lab integrations:

- BrainBit EEG through the repo-local NeuroSDK CLI.
- MR60 radar through ESP32-C6 BLE firmware in
  `software/study_runner/plugins/mr60_mini_radar/firmware/`.
- Camera and emotion through the single `camera_emotion` plugin, using browser
  `getUserMedia` plus a local or remote analysis worker.
- Per-plugin LSL acquisition and the detached Python recording worker for
  synchronized native and merged XDF data.

On Windows x64 and macOS Apple Silicon, the source installer installs DeepFace,
TensorFlow/tf-keras, OpenCV and the local Emotion Worker from
`software/requirements.txt`. The separately licensed emotion-model weight is
not bundled or silently downloaded. Review `THIRD_PARTY_NOTICES.md`; if its
upstream non-commercial-research terms fit the study, provision the pinned,
SHA-256-verified model explicitly with
`python release_tools/fetch_deepface_model_assets.py
--accept-vgg-face-non-commercial-research-terms`. Alternatively, use
`remote_worker` with a model for which the operator has suitable rights. macOS
Intel always uses `remote_worker`. The platform install scripts also invoke the native-core setup and tests;
`python tools/setup_recording_worker.py` remains the advanced core-only command.
See `docs/plugin-recording-architecture.md` for readiness and recovery.

Study settings define which sensors are intended for a study. The Admin
dashboard can set temporary runtime overrides for the current server session.
Those overrides are useful during setup and diagnostics, and they do not rewrite
the study file unless the study is explicitly saved in the editor.

Tablet camera behavior:

- If camera emotion is effectively enabled, the normal participant page starts
  live camera monitoring as soon as it is open and camera permission is granted.
- Before the Participant ID is entered and the study starts, frames only update
  the dashboard live monitor.
- After study start, emotion samples are recorded with the active study/card
  context.
- The separate `/camera-preview` page has been removed.

## Release Artifacts

`release_tools/build_source_release.py` creates and verifies the source ZIP,
tar.gz, metadata, release notes, and SHA-256 file from an exact tagged commit.
It rejects generated native binaries, secrets, runtime state, and local data.
The tag workflow then extracts the clean archive and runs the real installation
plus native recording smoke tests on Windows x64 and both macOS architectures.
It also reruns the non-recording Python/JavaScript/schema suite from the
extracted source on Linux before publication.

## One-Command Release

GitHub CLI is not required locally. The helper pushes `main` and then a tag;
GitHub Actions builds and validates the source release after that tag arrives.

Windows-friendly release command:

```powershell
.\release.ps1 patch
```

Other supported inputs:

```powershell
.\release.ps1 minor
.\release.ps1 major
.\release.ps1 0.3.0
.\release.ps1 patch -DryRun
.\release.ps1 patch -FullChecks
```

The release helper:

1. bumps `software/study_runner/version.py`,
2. runs fast local checks by default,
3. commits the version bump on `main`,
4. pushes `main`,
5. pushes `app-v<version>` to start the source-release workflow.

Normal commits and pushes do not create public releases. Only tags named
`app-vX.Y.Z` publish the source archives, and publication happens only after all
platform recording gates pass. Source installations update explicitly with
`git pull --ff-only` followed by the platform install script.

## Manual Checks

```bash
python -m pytest software
python -m unittest -v release_tools.tests.test_build_source_release
node --test software/tests/js/*.test.mjs
node --check release_tools/verify-release-version.mjs
node --check release_tools/release-study-runner.mjs
python -m py_compile release_tools/build_source_release.py tools/setup_recording_worker.py
git diff --check
```

## Source Of Truth

- Non-coder start: `docs/start-here.de.md`
- Operator guide: `docs/operator-guide.md`
- Sensor and data model: `docs/sensors-and-data.md`
- Plugin/recording architecture: `docs/plugin-recording-architecture.md`
- Developer guide: `docs/developer-guide.md`
- Editable study defaults: `software/study_content/`
- Runtime app code: `software/study_runner/`
- Release automation: `release_tools/`
- Local hardware references in the lab workspace: `../Sensorik/`
- Source release and update details: `docs/release-and-update.md`
- Docs index: `docs/README.md`
- Historical plans and audits: `docs/archive/`

Never commit local study results, generated build output, private keys,
certificates, passwords, `.env`, `local_secrets.json`, `settings/ssl/`,
`.crt`, `.cer`, `.pfx`, `.p12`, `.key`, `.pem`, or `.p8`.

## License

Copyright (c) 2026 Fabian Schmidt. Proprietary software; all rights reserved.
Study Runner is currently proprietary and all rights are reserved. See
[`LICENSE`](LICENSE). Included and optional third-party components retain their
own terms; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
