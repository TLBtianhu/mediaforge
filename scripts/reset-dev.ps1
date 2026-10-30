# Remove ignored development state. Never touches packaged/user data.
param(
  [switch]$IncludeLegacyProjectData
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$targets = @(
  (Join-Path $root ".dev-data"),
  (Join-Path $root ".dev-webview")
)
if ($IncludeLegacyProjectData) {
  $targets += (Join-Path $root "data")
}

foreach ($target in $targets) {
  $resolved = [IO.Path]::GetFullPath($target)
  $rootResolved = [IO.Path]::GetFullPath($root).TrimEnd('\') + '\'
  if (-not $resolved.StartsWith($rootResolved, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to delete outside project: $resolved"
  }
  if (Test-Path -LiteralPath $resolved) {
    Remove-Item -LiteralPath $resolved -Recurse -Force
    Write-Host "removed $resolved"
  }
}
