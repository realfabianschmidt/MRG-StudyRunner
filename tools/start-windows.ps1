#requires -Version 5.1

<#
.SYNOPSIS
Starts an already installed Study Runner source checkout on Windows.
#>
[CmdletBinding()]
param(
    [string]$BindAddress,
    [ValidateRange(1, 65535)][int]$Port,
    [switch]$NoBrowser,
    [switch]$DisableHttps
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$SoftwareRoot = Join-Path $RepositoryRoot "software"
$VenvPython = Join-Path $RepositoryRoot ".venv\Scripts\python.exe"
$ServerScript = Join-Path $SoftwareRoot "server.py"

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "This script supports Windows only. On macOS use: bash tools/start-macos.sh"
}
if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    throw "Study Runner is not installed in this checkout. Run .\tools\install-windows.ps1 first."
}
if (-not (Test-Path -LiteralPath $ServerScript -PathType Leaf)) {
    throw "Incomplete checkout: missing $ServerScript"
}

if ($BindAddress) { $env:STUDY_RUNNER_HOST = $BindAddress }
if ($PSBoundParameters.ContainsKey("Port")) { $env:STUDY_RUNNER_PORT = [string]$Port }
if ($NoBrowser) { $env:STUDY_RUNNER_NO_BROWSER = "1" }
if ($DisableHttps) { $env:STUDY_RUNNER_HTTPS = "0" }

Write-Host "Starting Study Runner. Press Ctrl+C to stop it."
Push-Location $SoftwareRoot
try {
    & $VenvPython $ServerScript
    $ServerExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $ServerExitCode
