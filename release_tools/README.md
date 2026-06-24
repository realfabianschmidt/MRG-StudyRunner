# Release Tools

This folder contains the Study Runner release automation.

Recommended Windows entrypoint from the repository root:

```powershell
.\release.ps1 patch
```

One-time prerequisite:

```powershell
winget install --id GitHub.cli
gh auth login
gh auth status
```

Direct Node entrypoint:

```bash
node release_tools/release-study-runner.mjs release patch
```

The release command creates a release branch, opens a pull request, waits for CI, merges when checks are green, pushes the release tag, waits for the GitHub release workflow, and verifies the finished release assets.
