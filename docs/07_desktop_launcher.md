# Python-Only Packaging

Study Runner is distributed as a Python-only PyInstaller one-dir ZIP. There is
no active Tauri wrapper in the repository anymore.

## What Users Download

Each release publishes exactly these app ZIPs:

- `study-runner-server-windows-x86_64.zip`
- `study-runner-server-linux-x86_64.zip`
- `study-runner-server-macos-x86_64.zip`
- `study-runner-server-macos-arm64.zip`

Users download the ZIP for their platform from:

```text
https://github.com/realfabianschmidt/MRG-StudyRunner/releases/latest
```

Then they unpack it and start:

- Windows: `study-runner-server.exe`
- macOS/Linux: `study-runner-server`

Packaged builds open `/admin` in the default browser automatically. Set
`STUDY_RUNNER_NO_BROWSER=1` to suppress this.

## Where User Data Lives

Packaged updates must not overwrite user data. Keep writable data outside the
install folder with `STUDY_RUNNER_DATA_DIR`.

The app stores or reads:

- `settings/study_config.json`
- `settings/hardware_settings.json`
- `settings/local_secrets.json`
- `studies/`
- `saved_results/`
- `updates/`

On first start with an external data dir, default settings and example studies
are copied from the bundled `study_content/` folder only if the destination is
empty.

## Build Locally

From the repository root:

```bash
python -m pip install -r software/requirements.txt -r release_tools/pyinstaller/requirements-build.txt
python release_tools/build-python-onedir.py
python release_tools/package-python-onedir.py --source software/dist/study-runner-server --output study-runner-server-local.zip
```

The executable name must remain `study-runner-server(.exe)` because the updater
looks for that file after staging an update.

Do not edit generated files in `software/build/` or `software/dist/` as source
files. They are build output.

## Release Flow

The release helper is the normal path:

```powershell
.\release.ps1 patch
```

It:

1. bumps `software/study_runner/version.py`,
2. runs local checks,
3. commits on `main`,
4. pushes `main`,
5. pushes `app-vX.Y.Z`.

Only the tag starts the GitHub release workflow. A normal push to `main` is not
visible to installed apps as an update.

## GitHub Actions Output

`.github/workflows/release.yml` builds four ZIPs and publishes:

- the four platform ZIPs,
- `study-runner-python-latest.json`.

The workflow no longer creates `latest.json`, Tauri installers, Rust bundles, or
Tauri updater signatures.

Required release secrets or variables:

- `PYTHON_UPDATER_PUBLIC_KEY`
- `PYTHON_UPDATER_SIGNING_PRIVATE_KEY`

## Updating In The App

In a packaged Python build:

1. Open `/admin`.
2. Click `Check` in the update card.
3. Click `Download`.
4. Confirm the download.
5. After verification, click `Restart`.

The app checks `study-runner-python-latest.json`, downloads the matching ZIP,
verifies SHA-256 and Ed25519 signature, stages the update, then restarts into the
staged executable.

Source checkouts show update state but do not self-install.

## Legacy Tauri Installs

Old Tauri installations are not migrated automatically. Users should download a
current Python-only ZIP once, unpack it, and use that build going forward.
