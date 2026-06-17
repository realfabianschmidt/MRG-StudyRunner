# Study Runner

Study Runner is a local app for running small user studies in a lab, classroom, workshop, or design research setting.

It has two ways to run:

- Browser mode for development and direct local use with Python.
- Desktop mode for installed Windows, macOS, and Linux apps with automatic updates through GitHub Releases.

The project is written to stay understandable for researchers, designers, and assistants who are not full-time software developers.

## Start The Browser App

Use this when you are developing, changing study files, or running the app directly from this repository.

```bash
pip install -r requirements.txt
python server.py
```

The terminal prints the available addresses.

- Admin page: `http://localhost:3000/admin`
- Participant page: `http://<computer-ip>:3000`

Participant tablets or phones need to be on the same local network as the computer that runs Study Runner.

For local HTTPS testing, for example when a tablet browser needs camera access:

```powershell
$env:STUDY_RUNNER_HTTPS='1'
python server.py
```

Optional runtime settings:

```bash
STUDY_RUNNER_HOST=0.0.0.0
STUDY_RUNNER_PORT=3000
STUDY_RUNNER_DATA_DIR=/path/to/writable/study-runner-data
```

## Use The Desktop App

The desktop app is a Tauri launcher. It starts the bundled Python Study Runner server and opens the launcher window. Users do not need to install Python.

Installed apps can update themselves when a newer public GitHub Release exists. Normal pushes to `main` do not become user updates. A user update only exists after a release tag such as `app-v0.2.0` has built successfully.

Latest release page:

```text
https://github.com/realfabianschmidt/MRG-StudyRunner/releases/latest
```

Expected release assets for a desktop release:

- `latest.json`
- `Study.Runner_<version>_x64-setup.exe`
- `Study.Runner_<version>_x64-setup.exe.sig`
- `Study.Runner_<version>_amd64.AppImage`
- `Study.Runner_<version>_amd64.AppImage.sig`
- `Study.Runner_<version>_x64.dmg`
- `Study.Runner_<version>_aarch64.dmg`
- macOS `.app.tar.gz` updater archives and `.sig` files for Intel and Apple Silicon

Use the DMG files for manual macOS installation. The installed macOS app updates through the `.app.tar.gz` updater artifacts referenced by `latest.json`.

## Folder Map

```text
Software/
|-- server.py
|   Starts the local Flask server.
|-- server_app/
|   Backend routes and services.
|-- plugins/
|   Built-in integration plugins such as BrainBit, mini radar, OSC, LSL,
|   LabRecorder, camera emotion, local emotion worker, and Notion upload.
|-- web_interface/
|   Browser pages, styles, scripts, card UI, and fonts.
|-- settings/
|   Editable default settings for local browser mode and desktop first start.
|-- saved_studies/
|   Saved study presets.
|-- saved_results/
|   Local participant results and optional sidecar files. Ignored by Git.
|-- desktop_app/
|   Tauri desktop launcher, updater UI, release scripts, and Rust wrapper.
|-- packaging/
|   PyInstaller build files for the bundled Python sidecar.
|-- docs/
|   Human-readable project notes and guides.
`-- requirements.txt
    Python dependencies for browser mode and the bundled server.
```

Generated build folders are not source files:

- `build/`
- `dist/`
- `desktop_app/node_modules/`
- `desktop_app/src-tauri/target/`
- `desktop_app/src-tauri/binaries/study-runner-server-*`

Do not edit generated files as the source of truth. They are recreated by builds.

## Typical Study Workflow

1. Open `/admin`.
2. Edit the study ID, settings, and question cards.
3. Save the study. The active study is written to `settings/study_config.json`; a reusable preset is written to `saved_studies/`.
4. Open the participant page on the tablet or another browser.
5. Run the study. Results are written to `saved_results/<study_id>/<participant_id>/`.

The admin page also shows an Access card with copyable admin and participant links.

## Local Desktop Build

From `Software/desktop_app/`:

```bash
npm install
python -m pip install -r ../packaging/requirements-build.txt
npm run build:sidecar
npm run build
```

The build creates a fresh PyInstaller sidecar first, then bundles the Tauri app. Changes in `server.py`, `server_app/`, `plugins/`, `web_interface/`, `settings/`, `saved_studies/`, or `desktop_app/` are included in the next desktop build.

Native installers must be built on the matching operating system. GitHub Actions handles this for official releases.

## Release And Update Workflow

Official update builds are tag driven.

1. Make the code or documentation changes on a branch.
2. Keep these desktop versions equal:
   - `desktop_app/package.json`
   - `desktop_app/src-tauri/tauri.conf.json`
   - `desktop_app/src-tauri/Cargo.toml`
3. Run the local checks listed below.
4. Push the branch and merge through a pull request.
5. Create and push a release tag after the version is final:

```bash
git tag -a app-v0.3.0 -m "Study Runner 0.3.0"
git push origin app-v0.3.0
```

Pushing `app-v0.3.0` starts `.github/workflows/release.yml`. The workflow builds Windows, Linux, macOS Intel, and macOS Apple Silicon packages, uploads updater signatures, uploads `latest.json`, and publishes the GitHub Release when all platform builds pass.

The installed desktop app checks:

```text
https://github.com/realfabianschmidt/MRG-StudyRunner/releases/latest/download/latest.json
```

If `latest.json` describes a newer SemVer version than the installed app, the launcher shows an update action.

## Required Release Secrets

The Tauri updater needs signing even when the Windows and macOS installers are not OS-code-signed.

Required GitHub repository secrets:

- `TAURI_SIGNING_PRIVATE_KEY`
- `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`

Optional Windows signing secrets:

- `WINDOWS_CERTIFICATE`
- `WINDOWS_CERTIFICATE_PASSWORD`

Optional Apple signing and notarization secrets:

- `APPLE_CERTIFICATE`
- `APPLE_CERTIFICATE_PASSWORD`
- `KEYCHAIN_PASSWORD`
- `APPLE_API_ISSUER`
- `APPLE_API_KEY`
- `APPLE_API_KEY_PRIVATE_KEY`

Never commit private keys, certificates, passwords, or `.pfx`, `.p12`, `.key`, `.pem`, or `.p8` files. `desktop_app/.secrets/` is intentionally ignored by Git.

## Checks Before Pushing

Run from `Software/` unless noted otherwise:

```bash
python -m pip install pytest
```

```bash
python -m pytest
node --check desktop_app/web/main.js
node --check desktop_app/scripts/verify-release-version.mjs
node desktop_app/scripts/verify-release-version.mjs app-v0.2.2
npm --prefix desktop_app run build:sidecar
```

Run from `Software/desktop_app/src-tauri/`:

```bash
cargo check -q
```

The sidecar build is needed before `cargo check` in a clean checkout because Tauri validates the configured `externalBin` path. For a future version, replace `app-v0.2.2` with the matching tag name.

## AI Assistant Notes

Use these rules when an AI assistant or a new contributor edits the project:

- Treat this README, `PROJECT_RULES.md`, and `docs/07_desktop_launcher.md` as the release source of truth.
- Keep active documentation and code comments in English.
- Do not commit local study results, generated build output, private secrets, or bundled sidecar executables.
- Prefer small, explicit changes over broad rewrites.
- Keep built-in integrations registered in `plugins/registry.py`; do not add dynamic plugin discovery.
- If a release fails, fix the branch and create a new version tag. Do not move an already published release tag unless the release has not been used.

## More Docs

- `PROJECT_RULES.md`
- `docs/01_project_overview_for_everyone.md`
- `docs/02_data_and_terms_explained.md`
- `docs/03_plan_for_clearer_code.md`
- `docs/05_integration_plugin_guide.md`
- `docs/07_desktop_launcher.md`
