# Study Runner

Study Runner is a local app for running small user studies in a lab, classroom, workshop, or design research setting.

Start here if you are not developing the code:

```text
01_START_HERE.md
```

## Project Roles

The repository has two clear halves: the **software** you edit, and the **desktop
wrapper** that packages it. Most people only ever touch `software/`.

```text
MRG-StudyRunner/
|-- 01_START_HERE.md       German guide for non-coders.
|-- 02_README.md           This file.
|-- 03_PROJECT_RULES.md    How we keep the code readable.
|-- release.ps1            One-command release (run from the repo root).
|-- software/              THE PROGRAM. Edit Python, UI, sensors and studies here.
|   |-- server.py          Run locally with: cd software && python server.py
|   |-- requirements.txt   Python dependencies.
|   |-- study_runner/      Python backend, browser UI, and built-in integrations.
|   |-- study_content/     Editable default studies and settings.
|   `-- tests/             Local automated checks.
|-- desktop/               THE INSTALLABLE WRAPPER. Rarely edited.
|   |-- web/               Launcher window UI.
|   |-- src-tauri/         Rust shell and auto-updater.
|   |-- scripts/           Sidecar build and signing helpers.
|   `-- build_tools/       PyInstaller config that bundles software/ into a sidecar.
|-- release_tools/         Versioning, release checks, GitHub PR/tag/release automation.
`-- docs/                  Technical notes and implementation guides.
```

Local study results are written to `software/saved_results/` and are ignored by Git.

## Run Locally

```bash
cd software
pip install -r requirements.txt
python server.py
```

The terminal prints the available addresses:

- Admin page: `http://localhost:3000/admin`
- Participant page: `http://<computer-ip>:3000`

Optional runtime settings:

```bash
STUDY_RUNNER_HOST=0.0.0.0
STUDY_RUNNER_PORT=3000
STUDY_RUNNER_CONTENT_DIR=/path/to/study-content
STUDY_RUNNER_DATA_DIR=/path/to/writable/app-data
```

## Desktop App

The desktop app is a Tauri wrapper. It starts the bundled Python Study Runner server and shows a launcher window. Users do not need to install Python.

Manual installer files are published here:

```text
https://github.com/realfabianschmidt/MRG-StudyRunner/releases/latest
```

Use:

- Windows: `Study.Runner_<version>_x64-setup.exe`
- Linux: `Study.Runner_<version>_amd64.AppImage`
- Mac Apple Silicon: `Study.Runner_<version>_aarch64.dmg`
- Mac Intel: `Study.Runner_<version>_x64.dmg`

The installed app updates itself through `latest.json` and signed updater artifacts.

Note for macOS: the current builds are not Apple-signed, so the first launch needs a
one-time un-quarantine step. See `01_START_HERE.md` and `docs/07_desktop_launcher.md`.

## Local Desktop Build

From the repository root:

```bash
npm --prefix desktop install
python -m pip install -r desktop/build_tools/pyinstaller/requirements-build.txt
npm --prefix desktop run build:sidecar
npm --prefix desktop run build
```

The build creates a fresh PyInstaller sidecar from `software/` first, then bundles the Tauri app.

## One-Command Release

Prerequisite once per machine:

```bash
gh auth login
gh auth status
```

On Windows, install GitHub CLI first if `gh` is missing:

```powershell
winget install --id GitHub.cli
```

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
```

The release helper:

1. bumps all desktop version files,
2. runs local checks,
3. creates one branch named `release/study-runner-<version>`,
4. opens a PR with `gh`,
5. waits for CI,
6. merges only when CI is green and the PR head is unchanged,
7. pushes `app-v<version>`,
8. waits for the GitHub Release workflow,
9. verifies release assets and updater metadata.

Normal commits and pushes do not create installed-app updates. Only tags named `app-vX.Y.Z` trigger updater releases.

## Manual Checks

```bash
python -m pytest software
node --check desktop/web/main.js
node --check release_tools/verify-release-version.mjs
node release_tools/verify-release-version.mjs app-v0.2.2
npm --prefix desktop run build:sidecar
```

From `desktop/src-tauri/`:

```bash
cargo check -q
```

## Source Of Truth

- Non-coder start: `01_START_HERE.md`
- Editable study defaults: `software/study_content/`
- Runtime app code: `software/study_runner/`
- Desktop shell: `desktop/`
- Release automation: `release_tools/`
- Desktop/release details: `docs/07_desktop_launcher.md`

Never commit local study results, generated build output, private keys, certificates, passwords, `.pfx`, `.p12`, `.key`, `.pem`, `.p8`, or `desktop/.secrets/`.
