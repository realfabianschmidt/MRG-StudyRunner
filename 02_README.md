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
|-- release_tools/         Versioning, release checks, tag, and release automation.
`-- docs/                  Active docs plus docs/archive/ for historical notes.
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

Python-only server packages are also published during releases. They use the Admin
page update card instead of the Tauri launcher. This path is available for testing
the server-only future, while Tauri remains the normal installed-app wrapper.

Note for macOS: the current builds are not Apple-signed, so the first launch needs a
one-time un-quarantine step. See `01_START_HERE.md` and `docs/07_desktop_launcher.md`.

## Local Desktop Build

From the repository root:

```bash
npm --prefix desktop install
python -m pip install -r software/requirements.txt -r desktop/build_tools/pyinstaller/requirements-build.txt
npm --prefix desktop run build:sidecar
npm --prefix desktop run build
```

The build creates a fresh PyInstaller sidecar from `software/` first, then bundles the Tauri app.

## One-Command Release

GitHub CLI is not required for the normal release helper. The helper pushes `main` and then pushes a tag; GitHub Actions builds the installers after that tag arrives.

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

1. bumps all desktop and Python version files,
2. runs fast local checks by default,
3. commits the version bump on `main`,
4. pushes `main`,
5. pushes `app-v<version>` to start the GitHub Release workflow.

Normal commits and pushes do not create installed-app updates. Only tags named
`app-vX.Y.Z` trigger updater releases. After the GitHub Actions release workflow
finishes, installed Tauri apps can update from the launcher and Python-only builds
can update from the Admin page update card.

## Manual Checks

```bash
python -m unittest discover software/tests
node --check desktop/web/main.js
node --check release_tools/verify-release-version.mjs
node --check release_tools/release-study-runner.mjs
python -m py_compile release_tools/package-python-onedir.py release_tools/write-python-update-key.py release_tools/build-python-update-manifest.py
node release_tools/verify-release-version.mjs app-v0.2.2
git diff --check
```

Optional full local build checks:

```bash
npm --prefix desktop run build:sidecar
cargo check -q --manifest-path desktop/src-tauri/Cargo.toml
```

## Source Of Truth

- Non-coder start: `01_START_HERE.md`
- Editable study defaults: `software/study_content/`
- Runtime app code: `software/study_runner/`
- Desktop shell: `desktop/`
- Release automation: `release_tools/`
- Docs index: `docs/README.md`
- Desktop/release details: `docs/07_desktop_launcher.md`
- Python-only update details: `docs/09_python_auto_update.md`
- Historical plans and audits: `docs/archive/`

Never commit local study results, generated build output, private keys, certificates, passwords, `.pfx`, `.p12`, `.key`, `.pem`, `.p8`, or `desktop/.secrets/`.
