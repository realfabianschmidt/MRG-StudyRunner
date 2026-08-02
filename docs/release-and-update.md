# Release And Update

Study Runner currently ships as an auditable Python source-server release. The
release contains the same source for Windows x64, macOS Intel, and macOS Apple
Silicon. Each recording computer builds and verifies its small native XDF core
locally during first install.

This release path does not publish an application bundle, installer, Manager,
PyInstaller server, or automatic updater feed. It needs no Apple signing,
notarization, updater key, or private release secret. GitHub's built-in token is
used only to attach validated files to the tagged GitHub Release.

## Release Files

The latest GitHub Release provides:

- `study-runner-source.zip` for Windows;
- `study-runner-source.tar.gz` for macOS;
- `study-runner-source-release.json` with version, commit, platform, install,
  recording, and proprietary-license metadata;
- `SHA256SUMS` for manual integrity verification.

Both archives contain one versioned root folder. They intentionally exclude
`.git`, `.venv`, generated native libraries, `.build`, results, runtime state,
credentials, certificates, and private keys. The repository is proprietary and
all rights are reserved; every archive includes `LICENSE`,
`THIRD_PARTY_NOTICES.md`, and the required vendored license texts. Separately
licensed DeepFace model weights and legacy assets without proven release
provenance are excluded.

## First Install

Using `git clone` is recommended because later updates are then simple and keep
ignored local study data in place. The equivalent source archives are available
from:

```text
https://github.com/realfabianschmidt/MRG-StudyRunner/releases/latest
```

Windows PowerShell from a cloned or extracted checkout:

```powershell
.\tools\install-windows.ps1 -InstallSystemDependencies
.\tools\start-windows.ps1
```

macOS after installing the Xcode Command Line Tools and Homebrew:

```bash
bash tools/install-macos.sh --install-system-dependencies
bash tools/start-macos.sh
```

The platform installer creates `.venv`, installs
`software/requirements.txt`, builds the XDF core from the pinned vendored
LabRecorder/XDFWriter sources, runs CTest, and imports a synthetic merged XDF
with PyXDF. See `../README.md` for complete WinGet, Homebrew, and Xcode commands.

## Python Dependency Constraints

The source installers and GitHub workflows use the same CPython 3.12 files:

- `software/constraints/py312-bootstrap.txt` for pip itself;
- `software/constraints/py312-common.txt` for the common server, plugins, and
  recording validators;
- `software/constraints/py312-local-emotion.txt` for Windows x64, macOS Apple
  Silicon, and Linux validation. macOS Intel omits this set and uses
  `camera_emotion.remote_worker`.

The common file preserves the scientific compatibility pins
`numpy==1.26.4`, `pylsl==1.18.2`, and `pyxdf==1.16.8`. The files also pin every
direct requirement and the high-risk local inference stack exercised by the
release jobs.

This is not a hash-locked dependency graph or offline wheelhouse. Some
transitive packages and the PyPI artifacts that satisfy them can still change
or disappear. Consequently, a successful install on one machine is not enough
to publish: the tag workflow resolves from a clean source archive on every
supported target and fails before release publication if a compatible wheel or
combination is unavailable. A future cryptographic lock would require
platform-specific wheel files and hashes maintained for all three targets.

## Updating A Source Checkout

Stop the server, then run:

```powershell
git pull --ff-only
.\tools\install-windows.ps1
.\tools\start-windows.ps1
```

or on macOS:

```bash
git pull --ff-only
bash tools/install-macos.sh
bash tools/start-macos.sh
```

The installer reuses a compatible `.venv`, refreshes dependencies, and only
rebuilds a missing or stale native core. It never removes study content or
results. A merge conflict or incompatible virtual environment stops with a
clear error instead of changing or deleting user files.

A downloaded source archive has no in-app self-update path. For repeated manual
archive replacement, configure `STUDY_RUNNER_DATA_DIR` outside the extracted
folder before collecting real data, or copy the old data directory explicitly.
Never delete an old checkout until its `software/saved_results/` and local
settings have been secured.

## Creating A Release

The Windows-friendly release helper remains the normal maintainer path:

```powershell
.\release.ps1 patch
```

It can also receive `minor`, `major`, or an exact version. Useful checks:

```powershell
.\release.ps1 patch -DryRun
.\release.ps1 patch -FullChecks
```

The helper updates `software/study_runner/version.py`, promotes the current
`## Unreleased` entries in `CHANGELOG.md` to a dated
`## X.Y.Z - YYYY-MM-DD` section, creates a fresh empty Unreleased section, runs
checks, commits the version on `main`, pushes it, and pushes `app-vX.Y.Z`. A
normal branch push never creates a public release. Release notes are taken from
the matching version in `CHANGELOG.md`.

## Release Gates

The tag workflow does not trust a developer's local build directory. It:

1. creates ZIP and tar.gz archives from the exact tagged Git commit;
2. validates their paths, required files, metadata, SHA-256 sums, license, and
   absence of generated binaries, secrets, and local data;
3. extracts the clean archive independently on Windows x64, macOS Intel, and
   macOS Apple Silicon;
4. runs the real platform install script from each extracted archive;
5. builds the native core locally and runs native writer, merge, clock-offset,
   synthetic LSL, and PyXDF smoke tests;
6. runs the Python, JavaScript, schema, and source-release contract regression
   suite again on Linux from the extracted archive;
7. publishes the source archives and metadata only after all three recording
   platform jobs and the Linux source-regression gate succeed.

Linux is a source-regression platform, not a supported recording target.

## Local Release Verification

Run the application and release tests before tagging:

```bash
python -m pytest software
python -m unittest -v release_tools.tests.test_build_source_release
node --test software/tests/js/*.test.mjs
git diff --check
```

The source builder can also validate an already generated output directory:

```bash
python release_tools/build_source_release.py --verify-output release-assets
```

## Legacy Packaging Code

Some PyInstaller, Manager, signing, and packaged-updater helpers remain in
`release_tools/` for historical reference or possible future non-recording
experiments. They are not outputs of the active release workflow and must not be
described as recording-ready. Reintroducing a packaged release requires a new
explicit acceptance gate for the verified native core, all runtime libraries,
data-directory preservation, and platform installation behavior. Apple signing
and notarization can be addressed then; they are deliberately outside the
current source-server release.
