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

The release command bumps the desktop version files, runs fast local checks, commits on `main`, pushes `main`, and pushes an `app-v<version>` tag. GitHub Actions builds and publishes the platform installers after the tag is pushed.

Use a dry run to preview the next version:

```powershell
.\release.ps1 patch -DryRun
```

Use full local checks when you also want the PyInstaller sidecar and Rust crate checked before the tag is pushed:

```powershell
.\release.ps1 patch -FullChecks
```
