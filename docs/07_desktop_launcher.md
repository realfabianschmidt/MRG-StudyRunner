# Desktop Launcher And Auto-Update Notes

Study Runner can run as an installed desktop app without replacing the existing browser workflow.

The desktop app is a small Tauri launcher. It starts the Python Study Runner server as a bundled sidecar, then opens the launcher UI. Participant tablets and other browsers still connect to the same local server over the private network.

## Runtime Model

- The Python server remains the source of truth.
- The built-in integration plugin registry stays in `software/study_runner/integrations/registry.py`.
- The desktop launcher sets:
  - `STUDY_RUNNER_APP_MODE=desktop`
  - `STUDY_RUNNER_HOST=0.0.0.0`
  - `STUDY_RUNNER_PORT=3000`
  - `STUDY_RUNNER_DATA_DIR=<platform app data folder>`
  - `STUDY_RUNNER_DISABLE_RUNTIME_PIP=1`
- In desktop mode, writable files are stored in the app data folder:
  - `settings/`
  - `studies/`
  - `saved_results/`
- On first desktop start, default settings and saved-study examples are copied from the bundled project files.

Bundled defaults are never the long-term user data store. Updates must not overwrite existing local studies, settings, results, or local secrets in the app data folder.

## Browser Access

The admin page shows an Access card with:

- the admin link
- the participant link for tablets or browsers on the same private network
- the active data folder

The backend also exposes:

- `GET /api/health`
- `GET /api/runtime-info`

## What Gets Bundled

The desktop app contains two parts:

- the Tauri launcher from `desktop/`
- a bundled Python server executable built with PyInstaller

PyInstaller bundles the active Python app structure from `software/`:

- `software/server.py`
- `software/study_runner/backend/`
- `software/study_runner/integrations/`
- `software/study_runner/web/`
- `software/study_content/settings/`
- `software/study_content/studies/`

That means users do not need to install Python or run `python server.py` when they use the desktop package.

## What To Edit Before Rebuilding

Normal development should happen in the regular project files, not inside generated desktop output.

Edit these files and folders as needed:

- `software/server.py`
- `software/study_runner/backend/`
- `software/study_runner/integrations/`
- `software/study_runner/web/`
- `software/study_content/settings/`
- `software/study_content/studies/`
- `desktop/` when the launcher itself needs to change

Then build from `desktop/`. The build first creates a fresh PyInstaller sidecar from the current `software/` files, then Tauri bundles that sidecar into the desktop app.

Do not edit generated files in `software/dist/`, `software/build/`, `desktop/src-tauri/target/`, or `desktop/src-tauri/binaries/` as source files. They are build outputs and will be replaced by later builds.

## Local Build Commands

From `desktop/`:

```bash
npm install
python -m pip install -r ../software/requirements.txt -r build_tools/pyinstaller/requirements-build.txt
npm run build:server:onedir
npm run build:sidecar
npm run build
```

`build:server:onedir` creates a PyInstaller one-folder server build for debugging and smoke checks.

`build:sidecar` creates the single executable sidecar that Tauri bundles through `externalBin`.

`build` creates the platform desktop bundle through Tauri.

Native builds should be produced on each target operating system because Python and PyInstaller bundles are platform-specific. The official release workflow does this on GitHub-hosted Windows, Linux, macOS Intel, and macOS Apple Silicon runners.

## Auto-Update Model

Study Runner uses the Tauri v2 updater with public GitHub Releases.

The app checks this endpoint:

```text
https://github.com/realfabianschmidt/MRG-StudyRunner/releases/latest/download/latest.json
```

If `latest.json` describes a newer SemVer version than the installed desktop app, the launcher shows an update action. Downloaded update bundles are verified with the Tauri updater signature before installation.

Important rules:

- Normal commits and pushes do not create installed-app updates.
- Only tags matching `app-vX.Y.Z` start the release workflow.
- The release tag must match the desktop app version.
- Tauri updater signing is required.
- Windows and macOS OS code signing are optional for the current setup, but recommended before broad distribution.

## Official Release Workflow

The release workflow lives in `.github/workflows/release.yml`.

It runs only on tags:

```text
app-v*
```

The workflow does this:

1. Verifies that the tag matches the app version.
2. Installs Node, Python, Rust, and Linux system dependencies.
3. Installs Python dependencies from `software/requirements.txt` and `desktop/build_tools/pyinstaller/requirements-build.txt`.
4. Builds the PyInstaller sidecar.
5. Builds Tauri bundles for:
   - Windows x64 NSIS
   - Linux x64 AppImage
   - macOS Intel app bundle and DMG
   - macOS Apple Silicon app bundle and DMG
6. Uploads updater artifacts and `latest.json`.
7. Publishes the GitHub Release only after all platform builds pass.

The action is pinned to a known working release:

```yaml
uses: tauri-apps/tauri-action@v0.6.2
```

Do not change this pin unless the new action version exists and has been tested.

## Versioning Rules

These three version fields must always match:

- `desktop/package.json`
- `desktop/src-tauri/tauri.conf.json`
- `desktop/src-tauri/Cargo.toml`

The release tag must be:

```text
app-v<version>
```

For example, version `0.3.0` must use tag `app-v0.3.0`.

The guard script is:

```bash
node release_tools/verify-release-version.mjs app-v0.3.0
```

## Required And Optional Secrets

Required for updater signatures:

- `TAURI_SIGNING_PRIVATE_KEY`
- `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`

Optional for Windows OS code signing:

- `WINDOWS_CERTIFICATE`
- `WINDOWS_CERTIFICATE_PASSWORD`

Optional for macOS signing and notarization:

- `APPLE_CERTIFICATE`
- `APPLE_CERTIFICATE_PASSWORD`
- `KEYCHAIN_PASSWORD`
- `APPLE_API_ISSUER`
- `APPLE_API_KEY`
- `APPLE_API_KEY_PRIVATE_KEY`

If optional Windows or Apple secrets are missing, the workflow still builds unsigned OS packages. Updater signatures are still required.

Private keys and certificates must never be committed. `desktop/.secrets/` is ignored by Git and is only for local key material.

## Latest Release

Release:

```text
https://github.com/realfabianschmidt/MRG-StudyRunner/releases/latest
```

The first successful updater release was `app-v0.2.0`:

```text
https://github.com/realfabianschmidt/MRG-StudyRunner/actions/runs/27611155278
```

Expected release assets for each desktop release:

- `latest.json`
- `Study.Runner_<version>_x64-setup.exe`
- `Study.Runner_<version>_x64-setup.exe.sig`
- `Study.Runner_<version>_amd64.AppImage`
- `Study.Runner_<version>_amd64.AppImage.sig`
- `Study.Runner_<version>_x64.dmg`
- `Study.Runner_<version>_aarch64.dmg`
- macOS `.app.tar.gz` updater archives and `.sig` files for Intel and Apple Silicon

The macOS DMG files are for manual installation. The Tauri updater uses the app-bundle updater archives referenced by `latest.json`, so macOS release jobs must build both `app` and `dmg` bundles.

## Future Release Checklist

Use the one-command helper for the next release:

```powershell
.\release.ps1 patch
```

For an explicit version:

```powershell
.\release.ps1 0.3.0
```

The helper bumps versions on `main`, runs fast local checks, commits the version bump, pushes `main`, and then pushes `app-v<version>`. The tag starts the GitHub release workflow, which builds and publishes the platform installers.

Use a dry run first when you only want to see what would happen:

```powershell
.\release.ps1 patch -DryRun
```

Use full local checks when you also want to build the PyInstaller sidecar and run the Rust check before the tag is pushed:

```powershell
.\release.ps1 patch -FullChecks
```

Manual checklist:

1. Change source files, docs, studies, or launcher files.
2. Keep generated folders out of Git.
3. Bump all desktop version fields to the same SemVer value.
4. Run fast local checks:

```bash
python -m unittest discover software/tests
node --check desktop/web/main.js
node --check release_tools/verify-release-version.mjs
node --check release_tools/release-study-runner.mjs
node release_tools/verify-release-version.mjs app-v0.3.0
git diff --check
```

5. Optionally run full local build checks:

```bash
npm --prefix desktop run build:sidecar
cargo check -q --manifest-path desktop/src-tauri/Cargo.toml
```

The sidecar build is required before `cargo check` in a clean checkout because Tauri validates that the configured `externalBin` exists.

6. Commit and push `main`.
7. Create and push the annotated release tag.
8. Wait for all release jobs to pass.
9. Verify that the release is published and contains `latest.json`, installers, and signature files.
10. Install an older build and confirm that the launcher shows the update.

The one-command helper performs steps 3, 4, 6, and 7 automatically. GitHub Actions performs the platform builds and publishes the release after the tag is pushed.

## Troubleshooting

- If a release workflow does not start, check that the pushed ref is a tag named `app-vX.Y.Z`.
- If the version guard fails, make the three desktop version fields equal and push a new tag for the corrected version.
- If updater signatures fail, check `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`.
- If macOS warns on install, the build is likely unsigned or not notarized. This is expected for test distribution without Apple signing secrets.
- If the macOS launcher window opens but no button works, the bundled Python server was
  blocked by Gatekeeper because the build is unsigned. The launcher now shows a
  "Server did not start" state and writes a log to `<app data folder>/logs/server.log`.
  Fix it once per machine by removing the quarantine flag:
  `xattr -dr com.apple.quarantine "/Applications/Study Runner.app"`. The permanent fix is
  Apple Developer ID signing plus notarization (the release workflow already supports it
  through the optional `APPLE_*` secrets).
- If macOS clients on the same network cannot reach the participant page, allow Study
  Runner under System Settings -> Privacy & Security -> Local Network. The bundle ships
  `NSLocalNetworkUsageDescription` so macOS prompts for this.
- If a release tag is already public and may have been installed, do not force-move it. Create a new patch version instead.
- If `release.ps1` stops before the tag is pushed, fix the cause and re-run the same
  command. If the tag was already pushed but the release did not finish, do not reuse that
  version number; bump to the next patch and release again.

## Integration Notes

Runtime `pip install` is disabled in desktop mode. Optional dependencies must be included in the package or configured manually.

BrainBit has one special rule: in a frozen desktop build, it must not use the frozen server executable as a Python interpreter for its separate CLI script. If BrainBit is needed in a packaged desktop build, set `brainbit.python_executable` in `software/study_content/settings/hardware_settings.json` before bundling, or configure the matching app-data settings file after first launch.
