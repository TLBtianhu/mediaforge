# Run a packaged/clean build against a throwaway data + WebView2 profile.
# Use this to verify "first run with no personalized config" without touching
# the developer profile or the dogfood/user profile.
param(
  [Parameter(Mandatory = $true)][string]$Exe
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$exePath = [IO.Path]::GetFullPath($(if ([IO.Path]::IsPathRooted($Exe)) { $Exe } else { Join-Path $root $Exe }))
$targetRoot = [IO.Path]::GetFullPath((Join-Path $root 'src-tauri\target')).TrimEnd('\') + '\'
if ($exePath.StartsWith($targetRoot, [StringComparison]::OrdinalIgnoreCase)) {
  throw 'Clean-package verification requires a packaged copy outside src-tauri/target.'
}
if (-not (Test-Path -LiteralPath $exePath)) {
  throw "Executable not found: $exePath (run pnpm tauri build first)"
}

$tempRoot = Join-Path $env:TEMP ("MediaForge-clean-" + [Guid]::NewGuid().ToString("N"))
$dataRoot = Join-Path $tempRoot "data"
$webViewRoot = Join-Path $tempRoot "webview"
New-Item -ItemType Directory -Force -Path $dataRoot, $webViewRoot, (Join-Path $dataRoot "models"), (Join-Path $dataRoot 'runtime-envs') | Out-Null

$names = @('MEDIAFORGE_DATA_ROOT', 'MEDIAFORGE_DATA_DIR', 'PYMSS_STUDIO_DATA_ROOT', 'MEDIAFORGE_MODEL_DIR', 'PYMSS_MODEL_DIR', 'MEDIAFORGE_RUNTIME_ENVS_DIR', 'WEBVIEW2_USER_DATA_FOLDER')
$previous = @{}
foreach ($name in $names) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process") }

try {
  $env:MEDIAFORGE_DATA_ROOT = $dataRoot
  $env:MEDIAFORGE_DATA_DIR = $dataRoot
  $env:MEDIAFORGE_MODEL_DIR = Join-Path $dataRoot "models"
  $env:PYMSS_STUDIO_DATA_ROOT = $dataRoot
  $env:PYMSS_MODEL_DIR = $env:MEDIAFORGE_MODEL_DIR
  $env:MEDIAFORGE_RUNTIME_ENVS_DIR = Join-Path $dataRoot 'runtime-envs'
  $env:WEBVIEW2_USER_DATA_FOLDER = $webViewRoot
  Write-Host "Clean-profile test"
  Write-Host "  exe:      $exePath"
  Write-Host "  data:     $dataRoot"
  Write-Host "  webview2: $webViewRoot"
  Write-Host "Close the app window to remove this temporary profile."
  $process = Start-Process -FilePath $exePath -PassThru -WindowStyle Hidden
  Wait-Process -Id $process.Id
}
finally {
  foreach ($name in $names) {
    if ($null -eq $previous[$name]) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
    else { Set-Item "Env:$name" $previous[$name] }
  }
  . (Join-Path $PSScriptRoot 'temp-work-guard.ps1')
  $resolvedTemp = Assert-TempWorkDir -Work $tempRoot -Prefix 'MediaForge-clean-'
  try {
    Remove-Item -LiteralPath $resolvedTemp -Recurse -Force -ErrorAction Stop
    Write-Host "Removed temporary profile: $resolvedTemp"
  } catch {
    Write-Warning "Temporary profile cleanup failed; retained at ${resolvedTemp}: $_"
  }
}
