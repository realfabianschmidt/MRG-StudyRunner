# Release And Update

Study Runner is distributed as Python-only PyInstaller one-dir ZIPs. There is
no active Tauri wrapper in the repository anymore.

## What Users Download

Recommended for non-coders:

- `study-runner-manager-windows-x86_64.zip`
- `study-runner-manager-macos-x86_64.zip`
- `study-runner-manager-macos-arm64.zip`

The manager is the Install & Repair Wizard. It installs or repairs the latest
stable signed Study Runner release, keeps user data in a separate folder, and
creates a desktop launcher. This is the recommended install path for Windows
and macOS users who should not handle Python, Pip, Git or DeepFace manually.

Manual server ZIPs remain available:

- `study-runner-server-windows-x86_64.zip`
- `study-runner-server-linux-x86_64.zip`
- `study-runner-server-macos-x86_64.zip`
- `study-runner-server-macos-arm64.zip`

Users download the ZIP for their platform from:

```text
https://github.com/realfabianschmidt/MRG-StudyRunner/releases/latest
```

For manual server ZIPs, unpack and start:

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
- `runtime/local_emotion_worker/`
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
python release_tools/build-python-onedir.py --spec release_tools/pyinstaller/study_runner_manager_onedir.spec
python release_tools/package-python-onedir.py --source software/dist/study-runner-manager --output study-runner-manager-local.zip
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

`.github/workflows/release.yml` builds server ZIPs plus manager ZIPs and publishes:

- signed server platform ZIPs,
- manager ZIPs for Windows and macOS,
- `study-runner-python-latest.json`.

The workflow runs the packaged Emotion Worker self-test before packaging the
server ZIP. The self-test starts the built executable with
`--emotion-worker-self-test`, seeds the vendored DeepFace model into a temporary
data-folder cache and loads DeepFace offline. A release must fail if this check
fails.

The workflow no longer creates `latest.json`, Tauri installers, Rust bundles, or
Tauri updater signatures.

Required release secrets or variables:

- `PYTHON_UPDATER_PUBLIC_KEY`
- `PYTHON_UPDATER_SIGNING_PRIVATE_KEY`

`PYTHON_UPDATER_PUBLIC_KEY` is not secret and may be stored as a GitHub
Actions variable or secret. It is embedded into packaged builds so they can
verify signed update manifests. `PYTHON_UPDATER_SIGNING_PRIVATE_KEY` is secret
and must only live as a GitHub Actions secret. Never commit private signing
keys, local certificates, `.env` files, `.pem`, `.pfx`, `.p12` or `.key` files.

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

Source checkouts show update state but do not self-install. In source mode use:

```bash
git pull
python -m pip install -r software/requirements.txt
cd software
python server.py
```

or download a fresh release ZIP. If a source checkout has no updater public key,
that is expected and not a broken installation.

## Packaged DeepFace Repair

Packaged builds bundle DeepFace, TensorFlow/tf-keras, OpenCV and the model
asset. The dashboard action `Repair DeepFace runtime` must not run `pip install`
inside a packaged executable. It only repairs the local model cache and restarts
the worker. If packaged Python dependencies are missing, use the manager action
`Repair existing installation` to reinstall the app while preserving the data
folder.

## Install & Repair Wizard

The manager uses `study-runner-python-latest.json` and the embedded updater
public key to verify the selected server ZIP before installing it. It installs
into versioned folders such as:

```text
StudyRunner/app/versions/0.3.1/
StudyRunner/data/
```

Repair reinstalls the current release and recreates the desktop launcher. It
does not delete the data folder. The advanced development mode can clone/pull
GitHub `main`, but it is not the lab default because it requires local Git,
Python, and dependency installation.

## Desktop Shortcut

The Admin hub can create a desktop shortcut on demand:

- Windows: `Study Runner.lnk` on the Desktop.
- macOS: `Study Runner.command` on the Desktop.

In source mode the shortcut starts `software/server.py` with the current Python
interpreter. In packaged mode it starts the packaged Python server executable.
The app does not create shortcuts automatically.

## Legacy Tauri Installs

Old Tauri installations are not migrated automatically. Users should download a
current Python-only ZIP once, unpack it, and use that build going forward.
