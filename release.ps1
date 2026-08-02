<#
.SYNOPSIS
Creates a tagged Study Runner source-server release.

.DESCRIPTION
Bumps the Python version, promotes CHANGELOG.md, runs checks, commits and pushes
main, then pushes app-v<version>. GitHub publishes source archives only; it does
not build a PyInstaller bundle or require signing/notarization credentials.

.PARAMETER FullChecks
Also builds and verifies the local canonical XDF core with the current-platform
C++ toolchain. No desktop bundle is built.

.PARAMETER SkipChecks
Skips local checks. Do not use this option for a production release.
#>
param(
  [Parameter(Position = 0)]
  [string]$VersionOrBump = "patch",
  [switch]$DryRun,
  [switch]$FullChecks,
  [switch]$SkipChecks
)

$ErrorActionPreference = "Stop"

$script = Join-Path $PSScriptRoot "release_tools\release-study-runner.mjs"
$arguments = @($script, "release", $VersionOrBump)

if ($DryRun) {
  $arguments += "--dry-run"
}

if ($FullChecks) {
  $arguments += "--full-checks"
}

if ($SkipChecks) {
  $arguments += "--skip-checks"
}

node @arguments
exit $LASTEXITCODE
