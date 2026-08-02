# Release Tools

Study Runner releases are source-server releases. They retain the normal Python
workflow and build the small native XDF core once on the installation machine.
The GitHub release does not contain a prebuilt core, PyInstaller application,
signed updater manifest, Apple signature, or notarization ticket.

## Create a release

The recommended maintainer command from a clean, up-to-date `main` checkout on
Windows is:

```powershell
.\release.ps1 patch
```

The equivalent direct command is:

```bash
node release_tools/release-study-runner.mjs release patch
```

Use `minor`, `major`, or an explicit version such as `0.5.1` instead of
`patch` when appropriate. The helper:

1. updates `software/study_runner/version.py`,
2. moves the current `CHANGELOG.md` Unreleased content below a dated version
   heading and creates a fresh empty Unreleased section,
3. runs checks,
4. commits and pushes `main`,
5. creates and pushes the annotated `app-v<version>` tag.

Preview without modifying anything:

```powershell
.\release.ps1 patch -DryRun
```

Run the local native-core build and smoke test in addition to the normal test
suite:

```powershell
.\release.ps1 patch -FullChecks
```

Full checks require CMake and the current-platform C++ compiler. They do not
build a desktop bundle. Do not use `-SkipChecks` for a production release.

## GitHub release gate

`.github/workflows/release.yml` is triggered only by `app-v*` tags. It creates
the source archives from the exact tagged Git tree and verifies installation
from those archives on:

- Windows x64,
- macOS Intel,
- macOS Apple Silicon.

Each platform runs the real first-install script, builds the local canonical XDF
core, and runs the native writer/merge/synthetic-LSL tests. The release is
published only after all three platforms pass. A separate Linux job extracts
the same archive and runs the full Python, JavaScript, schema, and release-tool
suite without attempting recording.

Published files are:

- `study-runner-source.zip`,
- `study-runner-source.tar.gz`,
- `study-runner-source-release.json`,
- `SHA256SUMS`.

The JSON identifies the exact tag and commit, records archive sizes and hashes,
declares the proprietary repository license, and explicitly declares that the
native core is not bundled and that the artifacts are not compatible with the
old packaged updater. Release notes are rendered from the matching version
section in `CHANGELOG.md`.

The workflow needs only GitHub's standard `GITHUB_TOKEN` with release-content
permission. `PYTHON_UPDATER_PUBLIC_KEY`, `PYTHON_UPDATER_SIGNING_PRIVATE_KEY`,
Apple signing identities, and notarization credentials are not used.

Linux remains useful for Python, JavaScript, schema, and static checks in CI,
but Linux recording and Linux release acceptance are intentionally unsupported.

## Historical packaging helpers

The remaining PyInstaller, update-manifest, manager, and wheelhouse helpers are
kept for the separate future packaged distribution track. The source release
workflow does not call them and makes no compatibility promise for their output.

Never commit private signing keys, certificates, environment files, local
secrets, study results, or generated `.build`, `build`, and `dist` directories.
