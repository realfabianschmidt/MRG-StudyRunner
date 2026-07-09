# Python-Only Auto Update

This archived note describes how Study Runner updates through the Python server. The update UI lives in the Admin
hub and always requires user confirmation before download or restart.

## Simple Answer

A normal push to GitHub is not enough for an app update.

Use the release command:

```powershell
.\release.ps1 patch
```

That command pushes `main` and then an `app-vX.Y.Z` tag. The tag starts GitHub
Actions. When the release workflow is green, packaged Study Runner builds can
see the new version from the Admin update card.

## User Steps

For a fresh install:

1. Download the ZIP for the computer from GitHub Releases.
2. Unpack it into a normal folder.
3. Start `study-runner-server(.exe)`.
4. The Admin page opens in the browser.

For an update:

1. Open `/admin`.
2. In the update card, click `Check`.
3. If a newer version is available, click `Download`.
4. Confirm the download.
5. After verification, click `Restart`.

The app never downloads or installs an update without confirmation.

## Runtime Flow

- `/api/admin/update/status` shows local update state.
- `/api/admin/update/check` reads the signed manifest from GitHub Releases.
- `/api/admin/update/download` downloads the selected platform ZIP into
  `updates/downloads`.
- The ZIP must match the manifest SHA-256 and Ed25519 signature.
- The verified ZIP is extracted into `updates/staged/<version>`.
- `/api/admin/update/install` starts a helper process, shuts down the running
  server, and relaunches the staged executable.

The running app does not overwrite itself in place.

## Release Artifacts

Each successful `app-vX.Y.Z` release publishes:

- `study-runner-server-windows-x86_64.zip`
- `study-runner-server-linux-x86_64.zip`
- `study-runner-server-macos-x86_64.zip`
- `study-runner-server-macos-arm64.zip`
- `study-runner-python-latest.json`

The repository no longer publishes Tauri updater artifacts or `latest.json`.

## Secrets

GitHub needs:

- `PYTHON_UPDATER_PUBLIC_KEY`: embedded into packaged builds before PyInstaller
  runs.
- `PYTHON_UPDATER_SIGNING_PRIVATE_KEY`: signs the manifest entries in the
  publish job.

Local development can override the manifest URL and public key with:

```bash
STUDY_RUNNER_UPDATE_MANIFEST_URL=...
STUDY_RUNNER_UPDATE_PUBLIC_KEY=...
```

## Data Safety

User data must live outside the install folder when possible:

```bash
STUDY_RUNNER_DATA_DIR=/path/to/study-runner-data
```

Updates must not touch local settings, studies, saved results, or local secrets.

## Legacy Tauri Installs

Existing Tauri users must switch manually once: download the current Python-only
ZIP, unpack it, and start `study-runner-server(.exe)`. After that, updates happen
from the Admin update card.
