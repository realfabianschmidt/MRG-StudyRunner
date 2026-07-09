# Study Runner

Study Runner is a local app for running small user studies in a lab, classroom,
workshop, or design research setting.

Start here if you are not developing the code:

```text
docs/start-here-noncoder.md
```

## Project Layout

Most app work happens in `software/`. Python-only packaging and release helpers
live in `release_tools/`.

In the local lab workspace, `../Sensorik/` is intentionally kept next to this
repo as the hardware reference and experiment folder. Runtime-ready copies live
inside `software/study_runner/integrations/`.

```text
Software/
|-- README.md              This file.
|-- CONTRIBUTING.md        How we keep the code readable.
|-- release.ps1            One-command release from the repo root.
|-- docs/
|   |-- start-here-noncoder.md  German guide for non-coders.
|   |-- README.md               Documentation index.
|   `-- archive/                Historical notes and audits.
|-- software/              THE PROGRAM.
|   |-- server.py          Run locally with: cd software && python server.py
|   |-- requirements.txt   Python dependencies.
|   |-- study_runner/      Python backend, browser UI, and integrations.
|   |-- study_content/     Editable default studies and settings.
|   `-- tests/             Automated checks.
`-- release_tools/         Versioning, PyInstaller packaging, manifests, release automation.
```

Local study results are written to `software/saved_results/` and are ignored by
Git.

## Install And Run From Source

Use this path when you cloned the GitHub repository. It works on Windows and
macOS when Python 3.11+ is installed.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r software/requirements.txt
cd software
python server.py
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r software/requirements.txt
cd software
python server.py
```

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

## Packaged App

Official non-coder builds are PyInstaller one-dir ZIPs from GitHub Releases:

```text
https://github.com/realfabianschmidt/MRG-StudyRunner/releases/latest
```

Assets:

- `study-runner-server-windows-x86_64.zip`
- `study-runner-server-linux-x86_64.zip`
- `study-runner-server-macos-x86_64.zip`
- `study-runner-server-macos-arm64.zip`

Unpack the ZIP and start `study-runner-server(.exe)`. Packaged builds open the
Admin page in the default browser automatically. They use the same per-computer
HTTPS certificate flow described above.

## Sensor And Camera Runtime

Current built-in lab integrations:

- BrainBit EEG through the repo-local NeuroSDK CLI.
- MR60 radar through ESP32-C6 BLE firmware in
  `software/study_runner/integrations/mr60_mini_radar/firmware/`.
- Tablet camera emotion through browser `getUserMedia` and a local DeepFace
  worker on the server computer.
- LSL markers and LabRecorder/XDF for synchronized raw recording.

DeepFace is installed through `software/requirements.txt`. The required emotion
model weight `facial_expression_model_weights.h5` is vendored in the repo under
`software/study_runner/integrations/local_emotion_worker/model_assets/`, so a
normal clone or release build does not depend on downloading that model from
GitHub at runtime.

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

## Local Package Build

From the repository root:

```bash
python -m pip install -r software/requirements.txt -r release_tools/pyinstaller/requirements-build.txt
python release_tools/build-python-onedir.py
python release_tools/package-python-onedir.py --source software/dist/study-runner-server --output study-runner-server-local.zip
```

## One-Command Release

GitHub CLI is not required for the normal release helper. The helper pushes
`main` and then pushes a tag; GitHub Actions builds the ZIPs after that tag
arrives.

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
5. pushes `app-v<version>` to start the GitHub Release workflow.

Normal commits and pushes do not create app updates. Only tags named
`app-vX.Y.Z` trigger updater releases. After the GitHub Actions release workflow
finishes, packaged builds can update from the Admin page update card.

## Manual Checks

```bash
python -m unittest discover software/tests
node --check release_tools/verify-release-version.mjs
node --check release_tools/release-study-runner.mjs
python -m py_compile release_tools/package-python-onedir.py release_tools/write-python-update-key.py release_tools/build-python-update-manifest.py release_tools/build-python-onedir.py
node release_tools/verify-release-version.mjs app-v0.2.4
git diff --check
```

Optional full local build check:

```bash
python release_tools/build-python-onedir.py
```

## Source Of Truth

- Non-coder start: `docs/start-here-noncoder.md`
- Editable study defaults: `software/study_content/`
- Runtime app code: `software/study_runner/`
- Release automation: `release_tools/`
- Local hardware references in the lab workspace: `../Sensorik/`
- Packaging details: `docs/07_desktop_launcher.md`
- Python update details: `docs/09_python_auto_update.md`
- Docs index: `docs/README.md`
- Historical plans and audits: `docs/archive/`

Never commit local study results, generated build output, private keys,
certificates, passwords, `.pfx`, `.p12`, `.key`, `.pem`, or `.p8`.
