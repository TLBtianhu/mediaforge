# Sign an existing release update with an explicit key path. Passwords are read
# only from the current process environment and injected into the signer child.
param(
    [string]$Version = '',
    [string]$OutputDir = '',
    [string]$KeyPath = ''
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..").Path
. "$PSScriptRoot\release-signing.ps1"
if (-not $Version) { $Version = (Get-Content "$root\package.json" -Raw | ConvertFrom-Json).version }
if ($Version -cnotmatch '\A(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?(?:\+[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?\z') {
    throw 'Invalid release version'
}
$releaseRoot = [IO.Path]::GetFullPath((Join-Path $root 'release')).TrimEnd('\', '/')
$output = if (-not $OutputDir) { $releaseRoot } elseif ([IO.Path]::IsPathRooted($OutputDir)) {
    [IO.Path]::GetFullPath($OutputDir)
} else { [IO.Path]::GetFullPath((Join-Path $root $OutputDir)) }
$output = $output.TrimEnd('\', '/')
if ($output -ne $releaseRoot -and -not $output.StartsWith($releaseRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Signing output must stay inside the repository release directory'
}
$zip = Join-Path $output "MediaForge-$Version-windows-x64-update.zip"
if (-not $KeyPath) { $KeyPath = [Environment]::GetEnvironmentVariable('TAURI_SIGNING_PRIVATE_KEY_PATH', 'Process') }
if (-not $KeyPath) { throw 'Set TAURI_SIGNING_PRIVATE_KEY_PATH or pass -KeyPath explicitly' }
Invoke-ReleaseArchiveSigning -Archive $zip -ExpectedVersion $Version -Root $root -KeyPath $KeyPath `
    -Password ([Environment]::GetEnvironmentVariable('TAURI_SIGNING_PRIVATE_KEY_PASSWORD', 'Process'))
Write-Host 'Update archive signed'
