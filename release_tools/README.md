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
Actions builds and publishes the server ZIPs and manager ZIPs after the tag is
pushed.

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

Manual local manager build:

```powershell
python release_tools/build-python-onedir.py --spec release_tools/pyinstaller/study_runner_manager_onedir.spec
python release_tools/package-python-onedir.py --source software/dist/study-runner-manager --output study-runner-manager-local.zip
```

The manager is the Install & Repair Wizard. It verifies the signed
`study-runner-python-latest.json` manifest before installing a server ZIP.

For offline/lab-ready camera emotion releases, the DeepFace emotion model asset
is already committed under
`software/study_runner/integrations/local_emotion_worker/model_assets/`. Fetch a
fresh copy only when the model asset should be refreshed:

```powershell
python release_tools/fetch-deepface-model-assets.py
```

The release build includes that `model_assets/` folder, so the dashboard repair
action can prepare DeepFace without downloading model weights from GitHub on the
lab computer.

Required release secrets or variables:

- `PYTHON_UPDATER_PUBLIC_KEY`
- `PYTHON_UPDATER_SIGNING_PRIVATE_KEY`

`PYTHON_UPDATER_PUBLIC_KEY` may be a GitHub variable. The signing private key
must be a GitHub secret.
