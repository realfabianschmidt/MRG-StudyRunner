# Python-Only Auto Update

Study Runner now has a Python-server update path that can run beside the Tauri
desktop updater during the transition away from the wrapper.

## Runtime Flow

- The Admin hub calls `/api/admin/update/status` to show local update state.
- A manual check calls `/api/admin/update/check`, which reads the signed Python
  update manifest from GitHub Releases.
- Download only starts after user confirmation. The server streams the selected
  platform ZIP into the app-data `updates/downloads` folder.
- The downloaded ZIP must match the manifest SHA-256 and an Ed25519 signature
  before it is extracted into `updates/staged/<version>`.
- Source checkouts and Tauri desktop sidecars do not self-install. Packaged
  Python builds can restart into the staged executable through
  `study_runner.update_helper`.

For non-coders, the important rule is: updates are not created by a normal push.
They are created by the release workflow after an `app-vX.Y.Z` tag is pushed.

The usual release command is:

```powershell
.\release.ps1 patch
```

After the GitHub Actions release workflow finishes successfully, a Python-only
packaged build can update from the Admin page:

1. Open `/admin`.
2. In the update card, click `Check`.
3. If a newer version is available, click `Download`.
4. Confirm the download.
5. After verification, click `Restart`.

The app never downloads or installs a Python update without user confirmation.

## Release Requirements

GitHub Actions keeps publishing the existing Tauri artifacts and also adds
Python one-dir ZIP assets:

- `study-runner-server-windows-x86_64.zip`
- `study-runner-server-linux-x86_64.zip`
- `study-runner-server-macos-x86_64.zip`
- `study-runner-server-macos-arm64.zip`

The release workflow needs these additional secrets or variables:

- `PYTHON_UPDATER_PUBLIC_KEY`: base64 raw 32-byte Ed25519 public key. This is
  embedded into release builds before PyInstaller runs.
- `PYTHON_UPDATER_SIGNING_PRIVATE_KEY`: base64 raw 32-byte Ed25519 private key
  or PEM private key. This signs the manifest entries in the publish job.

The publish job uploads `study-runner-python-latest.json` beside the ZIP assets.
Local development can override the manifest URL and public key with
`STUDY_RUNNER_UPDATE_MANIFEST_URL` and `STUDY_RUNNER_UPDATE_PUBLIC_KEY`.

If either Python updater secret is missing, the Python-only update artifacts are
not trustworthy and the release workflow should fail. Tauri updates use the
separate Tauri signing secrets.

## What A Push Does

- Push to `main`: updates the repository only. No installed app will see this as
  an update.
- Push tag `app-vX.Y.Z`: starts the release workflow.
- Successful release workflow: publishes the release assets that the updaters
  can see.

In short: code change, then release command, then wait for GitHub Actions, then
the app can pull the update.

## Tauri Transition

Tauri remains in place for now. The Python updater is intended to prove the
cross-platform server-only release path first. After one successful update cycle
on Windows, macOS, and Linux, the `desktop/` wrapper can be frozen or removed in
a separate cleanup.
