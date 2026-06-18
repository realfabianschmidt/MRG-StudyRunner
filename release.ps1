param(
  [Parameter(Position = 0)]
  [string]$VersionOrBump = "patch",
  [switch]$DryRun,
  [switch]$SkipChecks
)

$ErrorActionPreference = "Stop"

$script = Join-Path $PSScriptRoot "release_tools\release-study-runner.mjs"
$arguments = @($script, "release", $VersionOrBump)

if ($DryRun) {
  $arguments += "--dry-run"
}

if ($SkipChecks) {
  $arguments += "--skip-checks"
}

node @arguments
