# Release And Update

Study Runner currently ships as an auditable Python source-server release. The
release contains the same source for Windows x64, macOS Intel, and macOS Apple
Silicon, plus one tested XDF recording core per platform. Recording computers
download that core during first install, verify it, and test it again locally;
they never compile anything.

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
- `study-runner-xdf-core-windows-x64.zip`, `study-runner-xdf-core-macos-x64.zip`,
  and `study-runner-xdf-core-macos-arm64.zip` -- the native XDF core for each
  recording platform (library plus `worker-build.json`), built and tested by the
  tag workflow and downloaded by the installers;
- `study-runner-source-release.json` with version, commit, platform, install,
  recording (including every core's SHA-256), and license metadata;
- `SHA256SUMS` for manual integrity verification.

Both archives contain one versioned root folder with a generated
`study-runner-release.json` that lists the SHA-256 and native-source
fingerprint of each core; this is what the installer trusts. The archives
intentionally exclude `.git`, `.venv`, `.tools`, native libraries, `.build`,
results, runtime state,
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

What the installer does with the files above: it downloads the pinned `uv`
(`software/constraints/uv-bootstrap.txt`) into `.tools/`, lets it install the
pinned Python 3.12 there, creates `.venv`, installs `software/requirements.txt`
under the constraints below, then downloads this release's XDF core, checks it
against `study-runner-release.json`, writes and merges a synthetic XDF through
it, and imports the result with PyXDF. No administrator rights, compiler, or
system package manager are involved.

## Python Dependency Constraints

The source installers and GitHub workflows use the same CPython 3.12 files:

- `software/constraints/py312-bootstrap.txt` for pip itself;
- `software/constraints/py312-common.txt` for the common server, plugins, and
  recording validators;
- `software/constraints/py312-local-emotion.txt` for Windows x64, macOS Apple
  Silicon, and Linux validation. macOS Intel omits this set and uses
  `camera_emotion.remote_worker`.
- `software/constraints/py312-build-tools.txt` for the CMake used by the
  release workflow and by developers who build the core from source;
- `software/constraints/uv-bootstrap.txt` for the uv version (with the SHA-256
  of every platform download) and the exact Python 3.12 version.

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

## Updating An Installation

Since 1.2.0 both kinds of installation update themselves. The kind is detected
automatically: a `.git` folder means a git clone, a `study-runner-release.json`
in the install folder means an extracted release archive.

**From the admin dashboard:** Update panel, Check, then Update now. The dialog
lists what will be ended; an active session needs a second, red confirmation.
Then the server:

1. aborts a recording session with the reason "Software update" (data recorded
   so far is kept), ends a running study run and stops sensors. Queued
   finalizations and uploads are durable and continue after the restart;
2. release archive: downloads the platform archive named in
   `study-runner-source-release.json`, checks its SHA-256 and extracts it
   safely into `.tools/update-staging/<version>/`. Git clone: `git pull
   --ff-only` (refused on local changes to tracked files or when not on
   `main`);
3. exits. A helper waits for it, moves the old program files to
   `.tools/update-backup/<old-version>-<time>/`, moves the new ones in, adds new
   shipped study content that does not exist yet, runs the new install script
   and starts Study Runner in a new visible window (Terminal on macOS, a
   console on Windows). The page reloads once the new server answers.

Never touched: `software/study_content/` (studies, settings, credentials,
logos, fonts, certificates), `software/saved_results/`, `software/.build/`,
`.venv/`, `.tools/` and an external `STUDY_RUNNER_DATA_DIR`. If the install
script fails, the old program files are moved back and the old version starts
again; the helper log is `updates/update-helper.log` in the Study Runner data folder.

**From a terminal** (Study Runner stopped; it refuses while the port answers):

```bash
bash tools/update-macos.sh          # --check only reports
```

```powershell
.\tools\update-windows.cmd
```

This runs the same steps without the server.

**Once, from 1.1.x:** these versions cannot update an archive install yet.
Stop Study Runner, extract the new archive into a new folder, copy
`software/study_content` and `software/saved_results` from the old folder over
the new ones, run the install script and start. A git clone just needs
`git pull --ff-only` and the install script. Keep the old folder until the data
is confirmed in the new one.

The installer reuses a compatible `.venv`, refreshes dependencies, and only
replaces a missing or stale native core.

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

1. builds the native core from the tagged commit on Windows x64, macOS Intel,
   and macOS Apple Silicon, runs CTest, the synthetic XDF test, and the native
   smoke tests, and checks the macOS architecture and minimum version (13.0);
2. creates ZIP and tar.gz archives from the exact tagged Git commit and embeds
   `study-runner-release.json` with the three cores' hashes;
3. validates paths, required files, metadata, core assets, SHA-256 sums,
   license, and absence of generated binaries, secrets, and local data;
4. extracts the clean archive into a folder with spaces on all three platforms;
5. runs the real platform install script from each extracted archive -- on
   macOS with no compiler reachable -- and checks that it installed the
   prebuilt core, not a local build;
6. runs the native writer, merge, clock-offset, synthetic LSL, and PyXDF smoke
   tests against that installed core, then reruns the installer and requires it
   to reuse everything;
7. runs the Python, JavaScript, schema, and source-release contract regression
   suite again on Linux from the extracted archive;
8. publishes the archives, cores, and metadata only after all three recording
   platform jobs and the Linux source-regression gate succeed.

`macos-15-intel` is GitHub's last Intel macOS image. When it is retired, build
the Intel core on `macos-15` with `CMAKE_OSX_ARCHITECTURES=x86_64` and run its
tests under Rosetta.

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
