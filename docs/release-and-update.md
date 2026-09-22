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

- `study-runner-source.zip` and `study-runner-source.tar.gz` -- identical
  content in two archive formats, built from the exact same tagged commit.
  Pick whichever your system opens more conveniently (`.zip` on Windows,
  `.tar.gz` on macOS/Linux); either works on any of the three platforms;
- `study-runner-source-release.json` with version, commit, platform, install,
  recording, and license metadata;
- `SHA256SUMS` for manual integrity verification.

Both archives contain one versioned root folder. They intentionally exclude
`.git`, `.venv`, generated native libraries, `.build`, results, runtime state,
credentials, certificates, and private keys. The repository is MIT-licensed;
every archive includes `LICENSE`, `THIRD_PARTY_NOTICES.md`, and the required
vendored license texts. Separately licensed DeepFace model weights, the
optional Materiability heading font, and legacy assets without proven release
provenance are excluded.

## First Install

Installation is described once, in
[Install and start](../README.md#install-and-start): the release download, the
platform installer, the first start, and the macOS desktop shortcut. A Git
clone remains the better choice for operators who want in-place `git pull`
updates while keeping ignored local study data in the same folder.

What the installer does with the files above: it creates `.venv`, installs
`software/requirements.txt` under the constraints below, builds the XDF core
from the pinned vendored LabRecorder/XDFWriter sources, runs CTest, and
imports a synthetic merged XDF with PyXDF.

## Python Dependency Constraints

The source installers and GitHub workflows use the same CPython 3.12 files:

- `software/constraints/py312-bootstrap.txt` for pip itself;
- `software/constraints/py312-common.txt` for the common server, plugins, and
  recording validators;
- `software/constraints/py312-local-emotion.txt` for Windows x64, macOS Apple
  Silicon, and Linux validation. macOS Intel omits this set and uses
  `camera_emotion.remote_worker`.
- `software/constraints/py312-build-tools.txt` for the project-local CMake
  installed by the macOS source installer on Intel and Apple Silicon.

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

**From the admin dashboard** (a git clone on `main`, the recommended install
method): open the Update panel and click Check, then Update now. This runs
exactly the steps below itself -- `git pull --ff-only`, then the platform
install script -- and restarts the server for you once both succeed. It
refuses to run, changing nothing, if: a study session is active, the checkout
has local changes to tracked files, the checkout is not on `main`, or it is
not a git clone at all (see the next paragraph for that last case).

**By hand**, or if the checkout is not a git clone (a downloaded archive
extracted in place, with no `.git` folder -- the admin panel's Update step
does not apply there; download a fresh archive instead). Stop the server,
then run:

```powershell
git pull --ff-only
.\tools\install-windows.cmd
.\tools\start-windows.cmd
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

A downloaded archive (not a git clone) has no in-app update path -- replace it
with a fresh archive instead. For repeated manual archive replacement,
configure `STUDY_RUNNER_DATA_DIR` outside the extracted folder before
collecting real data, or copy the old data directory explicitly. Never delete
an old checkout until its `software/saved_results/` and local settings have
been secured.

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

`release_tools/` still contains PyInstaller, signing and packaged-updater
helpers, and `tools/study_runner_manager.py` is a standalone Tkinter Install &
Repair Wizard for packaged builds. **The active release workflow produces none
of it, and none of it is recording-ready.** Do not describe it as a shipping
path.

Reviving a packaged release would need a fresh acceptance gate covering the
verified native core, all runtime libraries, data-directory preservation and
platform installation -- plus an Ed25519 release-signing key and, for the
Manager on macOS, Apple signing and notarization credentials. This project
holds none of those. The admin dashboard's Update panel, described above, is
the one supported update path today.
