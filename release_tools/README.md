# Release Tools

This folder contains the Study Runner release automation for the Python-only app.

Recommended Windows entrypoint from the repository root:

```powershell
.\release.ps1 patch
```

Direct Node entrypoint:

```bash
node release_tools/release-study-runner.mjs release patch
```

The release command bumps `software/study_runner/version.py`, runs local checks,
commits on `main`, pushes `main`, and pushes an `app-v<version>` tag. GitHub
Actions builds and publishes the platform ZIPs after the tag is pushed.

A normal push to `main` is not an app update. The updater can only see a release
after the `app-v<version>` tag has been pushed and the GitHub Actions release
workflow has finished successfully.

Use a dry run to preview the next version:

```powershell
.\release.ps1 patch -DryRun
```

Use full local checks when you also want a local PyInstaller one-dir build before
the tag is pushed:

```powershell
.\release.ps1 patch -FullChecks
```

For offline/lab-ready camera emotion releases, fetch the DeepFace emotion model
asset before building:

```powershell
python release_tools/fetch-deepface-model-assets.py
```

The release build includes
`software/study_runner/integrations/local_emotion_worker/model_assets/` when it
exists, so the dashboard repair action can prepare DeepFace without downloading
model weights from GitHub on the lab computer.

Required release secrets or variables:

- `PYTHON_UPDATER_PUBLIC_KEY`
- `PYTHON_UPDATER_SIGNING_PRIVATE_KEY`

`PYTHON_UPDATER_PUBLIC_KEY` may be a GitHub variable. The signing private key
must be a GitHub secret.
