#requires -Version 5.1

<#
.SYNOPSIS
Installs the Study Runner source environment on Windows x64.

.DESCRIPTION
Creates or reuses the repository-local .venv, installs the version-controlled
Python requirements, and builds/verifies the canonical XDF recording core.
System packages are changed only when -InstallSystemDependencies is supplied.
#>
[CmdletBinding()]
param(
    [switch]$InstallSystemDependencies,
    [switch]$SkipRecordingCore
)

$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$RepositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$RequirementsPath = Join-Path $RepositoryRoot "software\requirements.txt"
$BootstrapConstraintsPath = Join-Path $RepositoryRoot "software\constraints\py312-bootstrap.txt"
$CommonConstraintsPath = Join-Path $RepositoryRoot "software\constraints\py312-common.txt"
$LocalEmotionConstraintsPath = Join-Path $RepositoryRoot "software\constraints\py312-local-emotion.txt"
$SetupScript = Join-Path $RepositoryRoot "tools\setup_recording_worker.py"
$VenvPath = Join-Path $RepositoryRoot ".venv"
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"

function Assert-LastCommandSucceeded {
    param([Parameter(Mandatory = $true)][string]$Description)
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE. Read the output above, fix the reported prerequisite, and run this script again."
    }
}

function Invoke-WinGetInstall {
    param(
        [Parameter(Mandatory = $true)][string]$PackageId,
        [string[]]$ExtraArguments = @(),
        [switch]$Force
    )
    Write-Host "Installing $PackageId with WinGet when missing..."
    $ModeArguments = if ($Force) { @("--force") } else { @("--no-upgrade") }
    & winget install --id $PackageId --exact --source winget --disable-interactivity --accept-source-agreements --accept-package-agreements @ModeArguments @ExtraArguments
    Assert-LastCommandSucceeded "WinGet package $PackageId"
}

function Test-VCToolsWorkload {
    $VsWhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path -LiteralPath $VsWhere -PathType Leaf)) {
        return $false
    }
    $Installation = & $VsWhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null
    return (($LASTEXITCODE -eq 0) -and -not [string]::IsNullOrWhiteSpace(($Installation | Select-Object -Last 1)))
}

function Install-VCToolsWorkload {
    if (Test-VCToolsWorkload) {
        Write-Host "Visual Studio C++ Build Tools workload is already installed."
        return
    }
    # --force is intentional here: an existing bare Build Tools installation
    # must run its installer again so the required workload can be added.
    Invoke-WinGetInstall -PackageId "Microsoft.VisualStudio.2022.BuildTools" -Force -ExtraArguments @(
        "--override",
        "--wait --passive --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
    )
    if (-not (Test-VCToolsWorkload)) {
        throw "Visual Studio Build Tools is installed without the C++ workload. Open Visual Studio Installer, add 'Desktop development with C++', finish the installation, and run this script again."
    }
}

function Update-ProcessPath {
    $MachinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$MachinePath;$UserPath"
    $CMakeBin = Join-Path $env:ProgramFiles "CMake\bin"
    if ((Test-Path -LiteralPath $CMakeBin) -and ($env:Path -notlike "*$CMakeBin*")) {
        $env:Path = "$CMakeBin;$env:Path"
    }
}

function Resolve-Python312 {
    $Candidates = @(
        @{ Executable = "py"; Prefix = @("-3.12") },
        @{ Executable = (Join-Path $env:LocalAppData "Programs\Python\Python312\python.exe"); Prefix = @() },
        @{ Executable = (Join-Path $env:ProgramFiles "Python312\python.exe"); Prefix = @() },
        @{ Executable = "python"; Prefix = @() }
    )
    foreach ($Candidate in $Candidates) {
        $Executable = [string]$Candidate.Executable
        if ([System.IO.Path]::IsPathRooted($Executable)) {
            if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
                continue
            }
        } elseif (-not (Get-Command $Executable -ErrorAction SilentlyContinue)) {
            continue
        }
        $Prefix = [string[]]$Candidate.Prefix
        $Version = & $Executable @Prefix -c "import struct, sys; print('{}.{}|{}'.format(sys.version_info.major, sys.version_info.minor, struct.calcsize('P') * 8))" 2>$null
        if (($LASTEXITCODE -eq 0) -and (($Version | Select-Object -Last 1) -eq "3.12|64")) {
            return $Candidate
        }
    }
    throw @"
Python 3.12 x64 was not found. Re-run with:
  .\tools\install-windows.ps1 -InstallSystemDependencies
or install the official WinGet package manually:
  winget install --id Python.Python.3.12 --exact --source winget
"@
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "This script supports Windows only. On macOS use: bash tools/install-macos.sh"
}
if (-not [Environment]::Is64BitOperatingSystem) {
    throw "Recording is supported on Windows x64 only; this operating system is not 64-bit."
}
foreach ($RequiredInstallFile in @(
    $RequirementsPath,
    $BootstrapConstraintsPath,
    $CommonConstraintsPath,
    $LocalEmotionConstraintsPath
)) {
    if (-not (Test-Path -LiteralPath $RequiredInstallFile -PathType Leaf)) {
        throw "Run the script from a complete Study Runner checkout; missing $RequiredInstallFile"
    }
}

Write-Host "Study Runner first-install/repair (Windows x64)"
Write-Host "Repository: $RepositoryRoot"

if ($InstallSystemDependencies) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "WinGet is unavailable. Install or update Microsoft's App Installer, then run this script again: https://learn.microsoft.com/windows/package-manager/winget/"
    }
    Invoke-WinGetInstall -PackageId "Python.Python.3.12"
    if (-not $SkipRecordingCore) {
        Invoke-WinGetInstall -PackageId "Kitware.CMake"
        Install-VCToolsWorkload
    }
    Update-ProcessPath
}

if (Test-Path -LiteralPath $VenvPath) {
    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        throw "$VenvPath exists but is not a valid Windows virtual environment. Move it aside manually and run this script again."
    }
    $VenvVersion = & $VenvPython -c "import struct, sys; print('{}.{}|{}'.format(sys.version_info.major, sys.version_info.minor, struct.calcsize('P') * 8))"
    Assert-LastCommandSucceeded "Checking the existing virtual environment"
    if (($VenvVersion | Select-Object -Last 1) -ne "3.12|64") {
        throw "$VenvPath uses Python $VenvVersion, but Study Runner requires Python 3.12 x64. Move it aside manually and run this script again."
    }
    Write-Host "Reusing $VenvPath"
} else {
    $Python = Resolve-Python312
    $PythonExecutable = [string]$Python.Executable
    $PythonPrefix = [string[]]$Python.Prefix
    Write-Host "Creating $VenvPath with Python 3.12..."
    & $PythonExecutable @PythonPrefix -m venv $VenvPath
    Assert-LastCommandSucceeded "Creating the virtual environment"
}

Write-Host "Installing Study Runner Python dependencies..."
& $VenvPython -m pip install --upgrade --constraint $BootstrapConstraintsPath pip
Assert-LastCommandSucceeded "Upgrading pip"
& $VenvPython -m pip install --constraint $CommonConstraintsPath --constraint $LocalEmotionConstraintsPath --requirement $RequirementsPath
Assert-LastCommandSucceeded "Installing software/requirements.txt"

if (-not $SkipRecordingCore) {
    if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) {
        throw "CMake is unavailable. Re-run with -InstallSystemDependencies or install Kitware.CMake with WinGet."
    }
    Write-Host "Checking the canonical XDF recording core..."
    $ProbeOutput = & $VenvPython $SetupScript --probe-only --require-canonical --json 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "No current verified core was found; building and testing it now..."
        & $VenvPython $SetupScript --require-canonical
        Assert-LastCommandSucceeded "Building the canonical XDF recording core"
    } else {
        Write-Host "Reusing the current verified XDF recording core."
    }
} else {
    Write-Warning "Recording-core setup was skipped. Studies without XDF recording can run, but required recording studies will remain blocked."
}

Write-Host ""
Write-Host "Study Runner is ready. Later starts need only:"
Write-Host "  .\tools\start-windows.ps1"
