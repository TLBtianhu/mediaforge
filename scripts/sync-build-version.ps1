param(
  [Parameter(Mandatory = $false)]
  [string]$Version = $env:MEDIAFORGE_VERSION
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($Version)) {
  throw "MEDIAFORGE_VERSION is empty. Provide -Version or set the MEDIAFORGE_VERSION environment variable."
}

$NormalizedVersion = $Version.Trim()
if ($NormalizedVersion.StartsWith('v')) {
  $NormalizedVersion = $NormalizedVersion.Substring(1)
}

if ($NormalizedVersion -cnotmatch '\A(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?(?:\+[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?\z') {
  throw "Invalid semantic version: $Version"
}

$Root = Split-Path -Parent $PSScriptRoot
$PackageJsonPath = Join-Path $Root 'package.json'
$TauriConfigPath = Join-Path $Root 'src-tauri\tauri.conf.json'
$CargoTomlPath = Join-Path $Root 'src-tauri\Cargo.toml'
$CargoLockPath = Join-Path $Root 'src-tauri\Cargo.lock'
# Validate the application lock entry before changing any version files. Keep
# dependency versions pinned; only the workspace application version changes.
$CargoLock = Get-Content -LiteralPath $CargoLockPath -Raw
$LockPattern = '(?m)(^\[\[package\]\]\r?\nname = "pymss-studio"\r?\nversion = ")[^"]+("\r?$)'
if ([regex]::Matches($CargoLock, $LockPattern).Count -ne 1) {
  throw 'Cargo.lock must contain exactly one pymss-studio application entry.'
}
$CargoLockUpdated = [regex]::Replace($CargoLock, $LockPattern, "`${1}$NormalizedVersion`${2}", 1)

function Update-JsonVersion {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path
  )

  $Content = Get-Content -Path $Path -Raw
  $Updated = [regex]::Replace($Content, '(?m)^(\s*"version"\s*:\s*)"[^"]+"', "`${1}`"$NormalizedVersion`"", 1)
  [System.IO.File]::WriteAllText($Path, $Updated, [System.Text.UTF8Encoding]::new($false))
}

Update-JsonVersion -Path $PackageJsonPath
Update-JsonVersion -Path $TauriConfigPath

$CargoToml = Get-Content -Path $CargoTomlPath -Raw
$CargoToml = [regex]::Replace($CargoToml, '(?m)^(version\s*=\s*)"[^"]+"', "`${1}`"$NormalizedVersion`"", 1)
[System.IO.File]::WriteAllText($CargoTomlPath, $CargoToml, [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::WriteAllText($CargoLockPath, $CargoLockUpdated, [System.Text.UTF8Encoding]::new($false))

Write-Host "Synchronized build version to $NormalizedVersion"
Write-Host " - $PackageJsonPath"
Write-Host " - $TauriConfigPath"
Write-Host " - $CargoTomlPath"
Write-Host " - $CargoLockPath"
