# Study Runner

Study Runner is a local app for running small user studies in a lab, classroom,
workshop, or design research setting.

## Install and start

These steps use the tested files from the
[latest GitHub Release](https://github.com/realfabianschmidt/MRG-StudyRunner/releases/latest).
Choose a permanent folder before installing -- **Documents** is a good
default, not a requirement, see below. Study Runner stores its local setup
and, by default, study data inside this folder, so do not run it permanently
from Downloads or move/delete the folder later.

There is no separate installer step that asks where to put it: the folder you
move the extracted download into is the install, and step 2 below is the only
place that decision gets made. Any writable, permanent folder works, on the
main drive or a network share, named however helps you find it again --
**Documents** is only the concrete example these steps use. Each installed
folder is fully self-contained (its own `.venv`, settings and study data);
nothing ties two installs together or tells the app which one you are running.
If more than one copy ever exists on the same computer, give each a name that
says which is which -- a stale or misplaced install is easy to open by
accident otherwise.

### What the installer does

The same few steps work on Windows x64, macOS Intel, and macOS Apple Silicon.
The installer needs an internet connection the first time, but **no
administrator password, no Xcode, no Visual Studio, no WinGet, and no
Homebrew**. Everything it downloads stays inside the Study Runner folder:

- `.tools/`: the pinned [uv](https://github.com/astral-sh/uv) tool and the
  pinned Python 3.12 (both checked by SHA-256);
- `.venv/`: Study Runner's Python packages;
- `software/.build/xdf_core/`: the XDF recording core. It is built and tested
  on GitHub for every release, then downloaded, checked against the SHA-256
  that ships inside the release archive, and tested again on this computer.

Nothing is installed system-wide, so deleting the folder removes everything.
The installer is safe to run again after an interruption or an update.

### Windows x64

#### First installation

1. Download `study-runner-source.zip` from the latest release and extract it.
2. Move the extracted Study Runner folder to a permanent location --
   **Documents** is a good default.
3. Open that folder in File Explorer, click the address bar, type `powershell`,
   and press Enter. PowerShell now opens in the correct folder.
4. Copy this command into PowerShell and press Enter:

   ```powershell
   .\tools\install-windows.cmd
   ```

5. Wait until the terminal says `Study Runner is ready`. The first
   installation takes a few minutes.
6. Start Study Runner:

   ```powershell
   .\tools\start-windows.cmd
   ```

The Admin page normally opens automatically. If it does not, open
`https://localhost:3000/admin` in a browser.

#### Every later start

Open the permanent Study Runner folder and double-click
`tools\start-windows.cmd`. You can also open PowerShell in the folder and run:

```powershell
.\tools\start-windows.cmd
```

Keep the terminal window open while Study Runner is running. Press `Ctrl+C` in
that window to stop it.

### macOS 13 or newer, Intel or Apple Silicon

#### First installation

1. Download `study-runner-source.tar.gz` from the latest release. Double-click
   it in Finder if the browser did not extract it automatically.
2. Move the extracted Study Runner folder to a permanent location --
   **Documents** is a good default.
3. Open Terminal. Type `cd `, including the space, drag the Study Runner folder
   from Finder into the Terminal window, and press Enter.
4. Install Study Runner:

   ```bash
   bash tools/install-macos.sh
   ```

5. Wait until the terminal says `Study Runner is ready`, then start it:

   ```bash
   bash tools/start-macos.sh
   ```

6. Wait for the Admin page to open. If it does not, open
   `https://localhost:3000/admin` in a browser.

macOS 15 on Intel and Apple Silicon is tested for every release; the recording
core itself runs on macOS 13 or newer.

#### If the installation stops with an error

- **"could not download ..."**: check the internet connection (a university
  proxy or captive portal is a common cause) and run the same command again.
- **"checksum" or "could not be verified"**: the download was damaged or does
  not belong to this release. Run the command again; if it repeats, download the
  release archive again.
- **"... uses Python ..., move it aside"**: an old `.venv` from an earlier
  installation method is incompatible. Rename the `.venv` folder (for example to
  `.venv-old`) and run the installer again.
- **Git checkout with changed native sources**: the published core only matches
  the release sources; developers build it locally with
  `bash tools/install-macos.sh --build-core-from-source` (needs Apple's Command
  Line Tools from `xcode-select --install`) or
  `.\tools\install-windows.cmd -BuildCoreFromSource` (needs Visual Studio C++
  Build Tools).

#### Create the macOS desktop shortcut

1. Leave Study Runner and its Terminal window running after the first start.
2. On the Admin page, open **Settings**, find **System**, and select
   **Create desktop shortcut**.
3. Click **Create shortcut** and wait for the success message. Finder now shows
   `Study Runner.command` on the Desktop.
4. Return to Terminal and press `Ctrl+C` to stop the first start.

The same shortcut works on macOS Intel and Apple Silicon. It points to this
installation folder. Create it again after moving the folder or installing a
new downloaded release.

#### Every later start

Double-click `Study Runner.command` on the Desktop. Confirm **Open** if macOS
asks the first time. Keep the Terminal window open while Study Runner is
running and press `Ctrl+C` there to stop it.

If the shortcut is missing, open Terminal in the Study Runner folder as in step
3 above and run:

```bash
bash tools/start-macos.sh
```

### Repair, update, or use Git

The installers are safe to run again and reuse `.venv` and a verified XDF core.
Run the platform installer again to repair or refresh an installation. Never delete an old downloaded release until its
local studies, settings, and results are secured; follow
[Release and Update](docs/release-and-update.md) when replacing an archive.

Developers and operators who want `git pull` updates can use the
[Git clone alternative](#git-clone-alternative).

Already installed? `tools/start-windows.cmd` / `tools/start-macos.sh` only wrap
one call each. Once a checkout has its `.venv` (from either installer, or the
[Git clone alternative](#git-clone-alternative)), start the server directly:

```bash
cd software && ../.venv/bin/python server.py        # macOS
```

```powershell
cd software; ..\.venv\Scripts\python.exe server.py   # Windows
```

Start here if you are not developing the code:

```text
docs/start-here.de.md
```

## Project Layout

Most app work happens in `software/`. Source-archive and release helpers live in
`release_tools/`; old packaged-app helpers are not part of the active release.

In the local lab workspace, `../Sensorik/` is intentionally kept next to this
repo as the hardware reference and experiment folder. Runtime-ready copies live
inside `software/study_runner/plugins/`.

```text
Software/
|-- README.md              This file.
|-- CONTRIBUTING.md        How we keep the code readable.
|-- CHANGELOG.md           Version history.
|-- LICENSE                Study Runner's own MIT license.
|-- THIRD_PARTY_NOTICES.md What third-party components are used and how.
|-- licenses/              Full text of every third-party license, collected.
|-- release.ps1            One-command release from the repo root.
|-- docs/                  See Documentation below for who each one is for.
|   |-- start-here.de.md        German guide for non-coders.
|   |-- operator-guide.md       Daily operation and project overview.
|   |-- how-recording-quality-works.md  Quality and timing, in plain language.
|   |-- sensors-and-data.md     What you configure, what you get back.
|   |-- plugin-recording-architecture.md  Plugin/worker/XDF contract.
|   |-- developer-guide.md      Server/UI/plugin development rules.
|   |-- file-guide.md           One line per source file.
|   |-- release-and-update.md   Source releases, updates, acceptance gates.
|   `-- archive/                Finished plans and historical records.
|-- software/              THE PROGRAM.
|   |-- server.py          Run locally with: cd software && python server.py
|   |-- requirements.txt   Python dependencies.
|   |-- constraints/       Release-tested Python 3.12 compatibility pins.
|   |-- study_runner/      The application itself, see the map below.
|   |-- study_content/     Editable default studies and settings.
|   |-- recording_worker/  C++ source of the XDF core the worker is built on.
|   `-- tests/             Automated checks.
`-- release_tools/         Versioning, source archives, validation, release automation.
```

Inside `software/study_runner/`, each folder is one area and carries its own
README:

```text
study_runner/
|-- apps/              Flask server/routes and browser UI.
|-- contracts/         Pure shared manifest and plugin contracts.
|-- data_core/         Host, worker and wire-contract recording packages.
|-- runtime_core/      Study, settings and delivery orchestration.
|-- plugins/           Sensors, cards, destinations, and outputs.
|-- plugin_framework/  The machinery that finds and runs those plugins.
|-- shared/            Dependency-light common utilities.
`-- updates/           Verifying and applying a signed update.
```

Local study results are written to `software/saved_results/` and are ignored by
Git.

## Installation details

The supported source-server setup uses Python 3.12 and a repository-local
`.venv`. The install scripts also install and test the small native XDF core.
They are safe to run again after an update and never delete studies or results.
No Apple Developer account, signing, or notarization is needed: the core is a
library that Study Runner's own Python loads, and the installer downloads it
directly instead of through a browser.

Both installers use the checked-in Python 3.12 compatibility constraints in
`software/constraints/`. Windows and Apple Silicon install the local emotion
stack; macOS Intel intentionally uses only the common set and `remote_worker`.
These constraints pin the release-tested direct and high-risk ML versions, but
are not a hash-locked offline wheel bundle.

Python itself comes from `software/constraints/uv-bootstrap.txt`: it pins one
uv release (with the SHA-256 of each platform download) and one exact Python
3.12 version. The installers download that uv binary into `.tools/uv/`, check
it, and let it install Python into `.tools/python/` without touching the
Windows registry, `PATH`, or any system folder. To move to newer versions,
update that one file (see its comments) and let the release workflow test it.

The recording core is `study-runner-xdf-core-<platform>.zip` on each release:
`windows-x64`, `macos-x64`, and `macos-arm64`. GitHub Actions builds it from the
pinned sources, runs CTest and the synthetic XDF test, and publishes it next to
the source archives. `study-runner-release.json` inside each source archive
records the SHA-256 and a fingerprint of the native sources for every core. The
installer only accepts a core that matches both, then repeats the synthetic XDF
test on the computer before using it. The macOS cores run on macOS 13 or newer;
the Windows core has no Visual C++ runtime dependency.

The Windows `.cmd` launchers invoke only their adjacent checked-in PowerShell
scripts with a process-local execution-policy bypass; they do not change the
machine or user policy. A policy enforced through AppLocker, WDAC, or Group
Policy must be resolved by the organization's administrator.

Developers who change `software/recording_worker/native/` build the core
locally instead: `--build-core-from-source` on macOS needs Apple's Command Line
Tools (`xcode-select --install`) or Xcode, and `-BuildCoreFromSource` on Windows
needs the Visual Studio C++ Build Tools. CMake is installed inside `.venv` for
this. With `--skip-recording-core` / `-SkipRecordingCore`, no core is installed.

### Git clone alternative

After installing Git, clone the repository instead of downloading a release
archive:

```bash
git clone https://github.com/realfabianschmidt/MRG-StudyRunner.git
cd MRG-StudyRunner
```

Then run the same platform installer and start commands from the quick-start
guide. A clone can be updated in place with `git pull --ff-only` and therefore
keeps ignored local data in the same folder. A clone has no
`study-runner-release.json`, so the installer downloads the core of the latest
release and uses it only when its native-source fingerprint matches the clone.

On Apple Silicon, `camera_emotion` supports its local DeepFace worker. Current
TensorFlow/tf-keras wheels do not support CPython 3.12 on macOS Intel, so the
Intel installer provides the server and full XDF recording but intentionally
skips the local analysis stack. Configure `camera_emotion` with
`remote_worker` on an Intel Mac.

There is no need to activate `.venv`; the scripts always use its interpreter
directly. For a non-recording installation, pass `-SkipRecordingCore` on
Windows or `--skip-recording-core` on macOS. Required sensor-recording studies
will remain blocked until the full installer has successfully installed the core.

### Update A Source Checkout

Updates preserve the ignored local study data and results:

```bash
git pull --ff-only
```

Then rerun the platform's install script and use its start script. The
installer refreshes Python dependencies and only replaces the XDF core when it
is missing or no longer matches the native sources. For the
admin-panel update flow, version pinning and the rollback path, see
[Release and Update](docs/release-and-update.md#updating-a-source-checkout).

The terminal prints the available addresses:

- Admin page: `https://localhost:3000/admin`
- Participant page: `https://<computer-ip>:3000`

HTTPS is enabled by default so tablet camera access can work. Study Runner
creates a persistent local Root CA in `software/study_content/settings/ssl/` and
prints the exact `.crt` path on startup. The certificate files and private keys
are local machine state and are intentionally ignored by Git.

For iPad camera access:

1. Start Study Runner once.
2. Copy the printed `study-runner-local-root-ca.crt` to the iPad. If iPadOS does
   not recognize it as a certificate, rename the copy to `.cer`.
3. Install it under `Settings > General > VPN & Device Management`.
4. Enable full trust under `Settings > General > About > Certificate Trust Settings`.
5. Open the printed `https://<computer-ip>:3000` participant URL.

Every server computer creates its own Root CA. When moving to another computer,
install that computer's newly generated certificate on the tablet. To disable
HTTPS for a local non-camera debug run, set `STUDY_RUNNER_HTTPS=0` before
starting.

Optional runtime settings:

```bash
STUDY_RUNNER_HOST=0.0.0.0
STUDY_RUNNER_PORT=3000
STUDY_RUNNER_HTTPS=0
STUDY_RUNNER_CONTENT_DIR=/path/to/study-content
STUDY_RUNNER_DATA_DIR=/path/to/writable/app-data
```

## GitHub Source Releases

The active release workflow publishes auditable source archives, not a desktop
app, Manager, PyInstaller server, or automatic updater:

```text
https://github.com/realfabianschmidt/MRG-StudyRunner/releases/latest
```

Use `study-runner-source.zip` on Windows or
`study-runner-source.tar.gz` on macOS. `SHA256SUMS` and
`study-runner-source-release.json` identify and verify the exact release. The
archives never contain generated native libraries, local results, credentials,
or certificates; the tested XDF cores are separate
`study-runner-xdf-core-<platform>.zip` assets that the installers download and
verify automatically.

Signing and notarization are unnecessary for this source-server workflow. Old
PyInstaller/Manager/updater code is retained only as legacy or possible future
work and is not published by the current release workflow.

## Plugin API v5

Every built-in plugin — sensor or upload destination alike — is a manifest
declared, API v5 subprocess: the core supervises one `driver.py` per plugin
and never imports a plugin's Python module directly (see
`docs/plugin-recording-architecture.md`). The admin page's diagnostics modal
gives every plugin a guided status view (device/channel/health at a glance)
plus a line-oriented expert console — no OS shell, read-only during a running
study unless explicitly unlocked with a recorded reason.

Current built-in plugins:

- **BrainBit** EEG through the repo-local NeuroSDK CLI.
- **MR60 mini-radar** through ESP32-C6 BLE firmware in
  `software/study_runner/plugins/sensors/mr60_mini_radar/firmware/`.
- **Camera and emotion** through the single `camera_emotion` plugin, using
  browser `getUserMedia` plus a local or remote analysis worker.
- **Notion** and **Nextcloud** as manifest-declared upload destinations
  (hidden from the sensor dashboard, visible in the settings hub).
- **OSC/TouchDesigner** for live trial-marker forwarding.
- Per-plugin LSL acquisition and the detached Python recording worker for
  synchronized native and merged XDF data.

Each plugin folder has its own `README.md` with its architecture and, where
applicable, exactly which parts of its code come from a third-party SDK
versus project-original code — see
`software/study_runner/plugins/README.md` for the full list.

On Windows x64 and macOS Apple Silicon, the source installer installs DeepFace,
TensorFlow/tf-keras, OpenCV and the local Emotion Worker from
`software/requirements.txt`. The separately licensed emotion-model weight is
not bundled or silently downloaded. Review `THIRD_PARTY_NOTICES.md`; if its
upstream non-commercial-research terms fit the study, provision the pinned,
SHA-256-verified model explicitly with
`python release_tools/fetch_deepface_model_assets.py
--accept-vgg-face-non-commercial-research-terms`. Alternatively, use
`remote_worker` with a model for which the operator has suitable rights. macOS
Intel always uses `remote_worker`. The platform install scripts also install
and test the native core; `python tools/setup_recording_worker.py` remains the
advanced core-only command (build from source, `--probe-only`,
`--install-prebuilt`).
See `docs/plugin-recording-architecture.md` for readiness and recovery.

Study settings define which sensors are intended for a study. The Admin
dashboard can set temporary runtime overrides for the current server session.
Those overrides are useful during setup and diagnostics, and they do not rewrite
the study file unless the study is explicitly saved in the editor.

Tablet camera behavior:

- If camera emotion is effectively enabled, the normal participant page starts
  live camera monitoring as soon as it is open and camera permission is granted.
- Before the Participant ID is entered and the study starts, frames only update
  the dashboard live monitor.
- After study start, emotion samples are recorded with the active study/card
  context.
- The separate `/camera-preview` page has been removed.

## Release Artifacts

The tag workflow first builds and tests the XDF core on Windows x64, macOS
Intel, and macOS Apple Silicon. `release_tools/build_source_release.py` then
creates and verifies the source ZIP, tar.gz, metadata, release notes, and
SHA-256 file from the exact tagged commit, checks the three core assets, and
writes `study-runner-release.json` into both archives. It rejects generated
native binaries, secrets, runtime state, and local data inside the archives.
The workflow then extracts the clean archive into a folder with spaces and runs
the real installer on all three platforms -- on macOS with no compiler
reachable -- plus the native recording smoke tests and a second, reusing
installer run.
It also reruns the non-recording Python/JavaScript/schema suite from the
extracted source on Linux before publication.

## One-Command Release

GitHub CLI is not required locally. The helper pushes `main` and then a tag;
GitHub Actions builds and validates the source release after that tag arrives.

Windows-friendly release command:

```powershell
.\release.ps1 patch
```

Other supported inputs:

```powershell
.\release.ps1 minor
.\release.ps1 major
.\release.ps1 0.3.0
.\release.ps1 patch -DryRun
.\release.ps1 patch -FullChecks
```

The release helper:

1. bumps `software/study_runner/version.py`,
2. runs fast local checks by default,
3. commits the version bump on `main`,
4. pushes `main`,
5. pushes `app-v<version>` to start the source-release workflow.

Normal commits and pushes do not create public releases. Only tags named
`app-vX.Y.Z` publish the source archives, and publication happens only after all
platform recording gates pass. Source installations update explicitly with
`git pull --ff-only` followed by the platform install script.

## Manual Checks

```bash
python -m pytest software
python -m unittest -v release_tools.tests.test_build_source_release
node --test software/tests/js/*.test.mjs
node --check release_tools/verify-release-version.mjs
node --check release_tools/release-study-runner.mjs
python -m py_compile release_tools/build_source_release.py tools/setup_recording_worker.py
git diff --check
```

## Documentation

| Document | Written for | Read it when |
|---|---|---|
| [start-here.de.md](docs/start-here.de.md) | Operators without a coding background (German) | You are setting the app up for the first time and do not work with code. |
| [operator-guide.md](docs/operator-guide.md) | Operators running studies | You run sessions day to day and need the start, HTTPS/iPad, and troubleshooting steps. |
| [how-recording-quality-works.md](docs/how-recording-quality-works.md) | Anyone reading a session's numbers | You want to know what gaps, jitter, effective rate and the lifecycle states actually mean. No code in it. |
| [sensors-and-data.md](docs/sensors-and-data.md) | Study authors | You are choosing sensors and need to know which files a session produces. |
| [plugin-recording-architecture.md](docs/plugin-recording-architecture.md) | Developers | You are changing recording, finalization, recovery, or the API-v5 plugin contract. This is the implementation contract. |
| [developer-guide.md](docs/developer-guide.md) | Developers | You are adding a plugin or card, or need the code layout and naming rules. |
| [file-guide.md](docs/file-guide.md) | Anyone touching a source file | You want to know what one file does and how safely it can be edited. A test keeps it complete. |
| [release-and-update.md](docs/release-and-update.md) | Whoever cuts releases | You are tagging a release, or updating an existing installation. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Everyone changing code | Before your first change. Project rules for keeping it readable. |

Each plugin documents itself next to its code; start at
[plugins/README.md](software/study_runner/plugins/README.md).
Finished plans, audits and implementation records are in
[docs/archive/](docs/archive/) — kept for context, current for nothing.

Where everything else lives:

- Editable study defaults: `software/study_content/`
- Runtime app code: `software/study_runner/`
- Release automation: `release_tools/`
- Version history: `CHANGELOG.md`
- Third-party license texts, collected in one place: `licenses/`
- Local hardware references in the lab workspace: `../Sensorik/`

Never commit local study results, generated build output, private keys,
certificates, passwords, `.env`, `local_secrets.json`, `settings/ssl/`,
`.crt`, `.cer`, `.pfx`, `.p12`, `.key`, `.pem`, or `.p8`. The tracked
`hardware_settings.json` is a shipped template — a test enforces that its
BrainBit `device_address`/`serial_number` stay empty placeholders, never one
lab's real headset identity. Locally authored studies stay untracked except
the two curated examples under `software/study_content/studies/`; local
results stay untracked except the one curated demo under
`software/saved_results/` (see `.gitignore`).

## License

Copyright (c) 2026 Fabian Schmidt. Licensed under the MIT License; see
[`LICENSE`](LICENSE). Included and optional third-party components retain their
own terms; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
