param(
  [Parameter(Mandatory = $true)]
  [string] $File
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $File)) {
  throw "Cannot sign missing file: $File"
}

$thumbprint = $env:WINDOWS_CERTIFICATE_THUMBPRINT
if ([string]::IsNullOrWhiteSpace($thumbprint)) {
  Write-Host "WINDOWS_CERTIFICATE_THUMBPRINT is not set; skipping Windows code signing."
  exit 0
}

$digestAlgorithm = if ($env:WINDOWS_DIGEST_ALGORITHM) { $env:WINDOWS_DIGEST_ALGORITHM } else { "sha256" }
$timestampUrl = if ($env:WINDOWS_TIMESTAMP_URL) { $env:WINDOWS_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }

function Resolve-SignTool {
  $fromPath = Get-Command signtool.exe -ErrorAction SilentlyContinue
  if ($fromPath) {
    return $fromPath.Source
  }

  $roots = @(
    "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
    "$env:ProgramFiles\Windows Kits\10\bin"
  ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }

  $candidate = Get-ChildItem -Path $roots -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" } |
    Sort-Object FullName -Descending |
    Select-Object -First 1

  if ($candidate) {
    return $candidate.FullName
  }

  return $null
}

$signTool = Resolve-SignTool
if (-not $signTool) {
  throw "signtool.exe was not found. Install the Windows SDK or add signtool.exe to PATH."
}

Write-Host "Signing $File with certificate $thumbprint"
& $signTool sign /fd $digestAlgorithm /td $digestAlgorithm /tr $timestampUrl /sha1 $thumbprint "$File"
