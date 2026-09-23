#requires -Version 5.1

<#
.SYNOPSIS
Installs or repairs the Study Runner source environment on Windows x64.

.DESCRIPTION
Downloads the pinned uv tool, installs the pinned Python 3.12 into the
project-local .tools folder, creates or reuses .venv, installs the
version-controlled Python requirements, and installs the tested, prebuilt XDF
recording core. No administrator rights, WinGet, or Visual Studio are needed.
Everything stays inside this folder; the script is safe to run again.
#>
[CmdletBinding()]
param(
    [switch]$InstallSystemDependencies,
    [switch]$SkipRecordingCore,
    [switch]$BuildCoreFromSource
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$RepositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$RequirementsPath = Join-Path $RepositoryRoot "software\requirements.txt"
$UvBootstrapPath = Join-Path $RepositoryRoot "software\constraints\uv-bootstrap.txt"
$BootstrapConstraintsPath = Join-Path $RepositoryRoot "software\constraints\py312-bootstrap.txt"
$CommonConstraintsPath = Join-Path $RepositoryRoot "software\constraints\py312-common.txt"
$LocalEmotionConstraintsPath = Join-Path $RepositoryRoot "software\constraints\py312-local-emotion.txt"
$BuildToolsConstraintsPath = Join-Path $RepositoryRoot "software\constraints\py312-build-tools.txt"
$SetupScript = Join-Path $RepositoryRoot "tools\setup_recording_worker.py"
$ToolsPath = Join-Path $RepositoryRoot ".tools"
$VenvPath = Join-Path $RepositoryRoot ".venv"
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
$PlatformArch = "windows-x64"
# uv splits --constraint values at spaces, so constraints are passed relative to
# the repository root (which uv commands run in) and never as absolute paths.
$BootstrapConstraints = "software\constraints\py312-bootstrap.txt"
$CommonConstraints = "software\constraints\py312-common.txt"
$LocalEmotionConstraints = "software\constraints\py312-local-emotion.txt"
$BuildToolsConstraints = "software\constraints\py312-build-tools.txt"

function Assert-LastCommandSucceeded {
    param([Parameter(Mandatory = $true)][string]$Description)
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE. Read the output above, fix the reported problem, and run this script again."
    }
}

function Get-PinValue {
    param([Parameter(Mandatory = $true)][string]$Name)
    foreach ($Line in (Get-Content -LiteralPath $UvBootstrapPath -Encoding UTF8)) {
        if ($Line.StartsWith("$Name=")) {
            return $Line.Substring($Name.Length + 1).Trim()
        }
    }
    return ""
}

function Invoke-Download {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$OutFile
    )
    Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "This script supports Windows only. On macOS use: bash tools/install-macos.sh"
}
if (-not [Environment]::Is64BitOperatingSystem) {
    throw "Study Runner supports Windows x64 only; this operating system is not 64-bit."
}
foreach ($RequiredInstallFile in @(
    $RequirementsPath,
    $UvBootstrapPath,
    $BootstrapConstraintsPath,
    $CommonConstraintsPath,
    $LocalEmotionConstraintsPath,
    $BuildToolsConstraintsPath,
    $SetupScript
)) {
    if (-not (Test-Path -LiteralPath $RequiredInstallFile -PathType Leaf)) {
        throw "Run the script from a complete Study Runner folder; missing $RequiredInstallFile"
    }
}
if ($InstallSystemDependencies) {
    Write-Host "NOTE: -InstallSystemDependencies is no longer needed and is ignored."
}

$UvVersion = Get-PinValue "uv_version"
$PythonVersion = Get-PinValue "python_version"
$UvPin = (Get-PinValue $PlatformArch) -split "\s+"
if ((-not $UvVersion) -or (-not $PythonVersion) -or ($UvPin.Count -ne 2) -or ($UvPin[1].Length -ne 64)) {
    throw "software\constraints\uv-bootstrap.txt is incomplete for $PlatformArch."
}
$UvAsset = $UvPin[0]
$UvSha256 = $UvPin[1].ToLowerInvariant()

Write-Host "Study Runner first-install/repair (Windows x64)"
Write-Host "Folder: $RepositoryRoot"

# Keep uv, Python, and every cache inside this folder and ignore user-wide uv settings.
$env:UV_CACHE_DIR = Join-Path $ToolsPath "uv-cache"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $ToolsPath "python"
$env:UV_NO_CONFIG = "1"
$env:UV_PYTHON = $null
$env:UV_INDEX_URL = $null
$env:UV_EXTRA_INDEX_URL = $null
$env:VIRTUAL_ENV = $null

$DownloadDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("study-runner-setup-" + [guid]::NewGuid().ToString("N"))
$null = New-Item -ItemType Directory -Path $DownloadDirectory
Push-Location -LiteralPath $RepositoryRoot
try {
    $UvDirectory = Join-Path $ToolsPath "uv\$UvVersion"
    $UvPath = Join-Path $UvDirectory "uv.exe"
    $UvReady = $false
    if (Test-Path -LiteralPath $UvPath -PathType Leaf) {
        $UvReported = & $UvPath --version 2>$null
        $UvReady = ($LASTEXITCODE -eq 0) -and ("$UvReported" -like "uv $UvVersion*")
    }
    if ($UvReady) {
        Write-Host "Reusing uv $UvVersion."
    } else {
        Write-Host "Downloading uv $UvVersion..."
        $UvArchive = Join-Path $DownloadDirectory $UvAsset
        Invoke-Download -Url "https://github.com/astral-sh/uv/releases/download/$UvVersion/$UvAsset" -OutFile $UvArchive
        $ActualSha256 = (Get-FileHash -LiteralPath $UvArchive -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($ActualSha256 -ne $UvSha256) {
            throw "The uv download has the wrong checksum (expected $UvSha256, found $ActualSha256)."
        }
        $UvUnpacked = Join-Path $DownloadDirectory "uv"
        Expand-Archive -LiteralPath $UvArchive -DestinationPath $UvUnpacked
        $null = New-Item -ItemType Directory -Force -Path $UvDirectory
        Move-Item -LiteralPath (Join-Path $UvUnpacked "uv.exe") -Destination $UvPath -Force
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
        Write-Host "Installing Python $PythonVersion into $env:UV_PYTHON_INSTALL_DIR..."
        & $UvPath python install $PythonVersion --no-bin --no-registry
        Assert-LastCommandSucceeded "Installing Python $PythonVersion"
        Write-Host "Creating $VenvPath..."
        & $UvPath venv --seed --managed-python --python $PythonVersion $VenvPath
        Assert-LastCommandSucceeded "Creating the virtual environment"
    }

    Write-Host "Installing Study Runner Python dependencies..."
    & $UvPath pip install --python $VenvPython --constraint $BootstrapConstraints pip
    Assert-LastCommandSucceeded "Installing pip"
    & $UvPath pip install --python $VenvPython --constraint $CommonConstraints --constraint $LocalEmotionConstraints --requirement $RequirementsPath
    Assert-LastCommandSucceeded "Installing software\requirements.txt"

    if (-not $SkipRecordingCore) {
        Write-Host "Checking the XDF recording core..."
        $null = & $VenvPython $SetupScript --probe-only --require-canonical --json 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Reusing the current verified XDF recording core."
        } elseif ($BuildCoreFromSource) {
            Write-Host "Installing the project-local CMake build tools..."
            & $UvPath pip install --python $VenvPython --constraint $BuildToolsConstraints cmake
            Assert-LastCommandSucceeded "Installing CMake"
            $env:Path = "$(Join-Path $VenvPath 'Scripts');$env:Path"
            Write-Host "Building and testing the XDF recording core from source (needs Visual Studio C++ Build Tools)..."
            & $VenvPython $SetupScript --require-canonical
            Assert-LastCommandSucceeded "Building the XDF recording core"
        } else {
            $CoreSource = @(& $VenvPython $SetupScript --prebuilt-source)
            Assert-LastCommandSucceeded "Determining the XDF recording core download"
            $CoreAsset = [string]$CoreSource[0]
            $CoreUrl = [string]$CoreSource[1]
            $ChecksumsUrl = [string]$CoreSource[2]
            $CoreArchive = Join-Path $DownloadDirectory $CoreAsset
            $ChecksumsPath = Join-Path $DownloadDirectory "SHA256SUMS"
            if ($env:STUDY_RUNNER_CORE_ASSET_DIR) {
                Write-Host "Using the XDF recording core from $env:STUDY_RUNNER_CORE_ASSET_DIR..."
                Copy-Item -LiteralPath (Join-Path $env:STUDY_RUNNER_CORE_ASSET_DIR $CoreAsset) -Destination $CoreArchive
                $LocalChecksums = Join-Path $env:STUDY_RUNNER_CORE_ASSET_DIR "SHA256SUMS"
                if (Test-Path -LiteralPath $LocalChecksums -PathType Leaf) {
                    Copy-Item -LiteralPath $LocalChecksums -Destination $ChecksumsPath
                }
            } else {
                Write-Host "Downloading the tested XDF recording core ($CoreAsset)..."
                Invoke-Download -Url $CoreUrl -OutFile $CoreArchive
                try {
                    Invoke-Download -Url $ChecksumsUrl -OutFile $ChecksumsPath
                } catch {
                    Write-Host "SHA256SUMS is unavailable; relying on the release description in this folder."
                }
            }
            $ChecksumArguments = @()
            if (Test-Path -LiteralPath $ChecksumsPath -PathType Leaf) {
                $ChecksumArguments = @("--checksums", $ChecksumsPath)
            }
            Write-Host "Verifying and testing the XDF recording core on this PC..."
            & $VenvPython $SetupScript --install-prebuilt $CoreArchive @ChecksumArguments --require-canonical
            Assert-LastCommandSucceeded "Verifying the downloaded XDF recording core (developers with changed native sources can use -BuildCoreFromSource)"
        }
    } else {
        Write-Warning "Recording-core setup was skipped. Studies without XDF recording can run, but required recording studies will remain blocked."
    }
} finally {
    Pop-Location
    if (Test-Path -LiteralPath $DownloadDirectory) {
        [System.IO.Directory]::Delete($DownloadDirectory, $true)
    }
}

Write-Host ""
Write-Host "Study Runner is ready. Later starts need only:"
Write-Host "  .\tools\start-windows.cmd"
