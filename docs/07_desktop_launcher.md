# Desktop Launcher Plan And Build Notes

Study Runner can now be launched as a desktop app without replacing the existing browser workflow.

The desktop app is a small Tauri launcher. It starts the Python Study Runner server as a sidecar, then opens the admin page. Participant tablets and other browsers still connect to the same local server over the private network.

## Runtime Model

- The Python server remains the source of truth.
- The built-in integration plugin registry stays in `plugins/registry.py`.
- The desktop launcher sets:
  - `STUDY_RUNNER_APP_MODE=desktop`
  - `STUDY_RUNNER_HOST=0.0.0.0`
  - `STUDY_RUNNER_PORT=3000`
  - `STUDY_RUNNER_DATA_DIR=<platform app data folder>`
  - `STUDY_RUNNER_DISABLE_RUNTIME_PIP=1`
- In desktop mode, writable files are stored in the app data folder:
  - `settings/`
  - `saved_studies/`
  - `saved_results/`
- On first desktop start, default settings and saved-study examples are copied from the bundled project files.

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

- the Tauri launcher from `desktop_app/`
- a bundled Python server executable built with PyInstaller

PyInstaller bundles the active Python app structure from `Software/`:

- `server.py`
- `server_app/`
- `plugins/`
- `web_interface/`
- `settings/`
- `saved_studies/`

That means users do not need to install Python or run `python server.py` when they use the desktop package.

The bundled `settings/` and `saved_studies/` files are defaults. On first desktop start, Study Runner copies those defaults into the platform app data folder. After that, user edits, results, local secrets, and saved studies live in that writable app data folder.

## What To Edit Before Rebuilding

Normal development should still happen in the regular project files, not inside the generated desktop output.

Edit these files and folders as usual:

- `server.py`
- `server_app/`
- `plugins/`
- `web_interface/`
- `settings/`
- `saved_studies/`
- `desktop_app/` when the launcher itself needs to change

Then run the build again from `Software/desktop_app/`. The build first creates a fresh PyInstaller sidecar from the current project files, then Tauri bundles that sidecar into the desktop app.

Do not edit generated files in `dist/`, `build/`, or `desktop_app/src-tauri/target/` as a source of truth. They are build outputs and will be replaced by later builds.

## Build Commands

From `Software/desktop_app/`:

```bash
npm install
python -m pip install -r ../packaging/requirements-build.txt
npm run build:server:onedir
npm run build:sidecar
npm run build
```

`build:server:onedir` creates a PyInstaller one-folder server build for debugging and smoke checks.

`build:sidecar` creates the single executable sidecar that Tauri can bundle through `externalBin`.

`build` creates the platform desktop bundle through Tauri.

Native builds should be produced on each target operating system because Python and PyInstaller bundles are platform-specific.

## Which File To Share

Builds are native per operating system. A Windows build cannot be sent to a Mac user and expected to work.

- Windows: send the NSIS setup file from `desktop_app/src-tauri/target/release/bundle/nsis/`, for example `Study Runner_0.1.0_x64-setup.exe`.
- macOS: build on macOS and send the generated DMG from `desktop_app/src-tauri/target/release/bundle/dmg/`. A generated `.app` can also work, but a DMG is usually easier to share.
- Linux: build on Linux and send the generated AppImage from `desktop_app/src-tauri/target/release/bundle/appimage/`.

Unsigned macOS builds may show Gatekeeper warnings. Signing and notarization should be added before broad distribution outside trusted test users.

## Packaging Notes

- Windows: start with the Tauri NSIS installer.
- macOS: start with `.app` or DMG; signing and notarization are phase two.
- Linux: start with AppImage; add DEB or RPM later only if needed.

## Integration Notes

Runtime `pip install` is disabled in desktop mode. Optional dependencies must be included in the package or configured manually.

BrainBit has one special rule: in a frozen desktop build, it must not use the frozen server executable as a Python interpreter for its separate CLI script. If BrainBit is needed in a packaged desktop build, set `brainbit.python_executable` in `settings/hardware_settings.json` to a real Python interpreter or package that integration as its own sidecar later.
