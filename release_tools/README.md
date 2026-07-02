# Release Tools

This folder contains the Study Runner release automation.

Recommended Windows entrypoint from the repository root:

```powershell
.\release.ps1 patch
```

Direct Node entrypoint:

```bash
node release_tools/release-study-runner.mjs release patch
```

The release command bumps the desktop and Python version files, runs fast local
checks, commits on `main`, pushes `main`, and pushes an `app-v<version>` tag.
GitHub Actions builds and publishes the platform installers and Python-only
update ZIPs after the tag is pushed.

A normal push to `main` is not an app update. The updater can only see a release
after the `app-v<version>` tag has been pushed and the GitHub Actions release
workflow has finished successfully.

Use a dry run to preview the next version:

```powershell
.\release.ps1 patch -DryRun
```

Use full local checks when you also want the PyInstaller sidecar and Rust crate checked before the tag is pushed:

```powershell
.\release.ps1 patch -FullChecks
```

Required release secrets or variables:

- Tauri updater: `TAURI_SIGNING_PRIVATE_KEY`, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`
- Python updater: `PYTHON_UPDATER_PUBLIC_KEY`, `PYTHON_UPDATER_SIGNING_PRIVATE_KEY`

`PYTHON_UPDATER_PUBLIC_KEY` may be a GitHub variable. The signing private key
must be a GitHub secret.
