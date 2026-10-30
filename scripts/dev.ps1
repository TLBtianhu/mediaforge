# Launch MediaForge in an isolated development profile.
# User settings, library DB, models, and WebView2 cache stay inside the repo
# (ignored by Git) instead of mixing with a packaged/user profile.
param(
  [switch]$Clean
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dataRoot = Join-Path $root ".dev-data"
$webViewRoot = Join-Path $root ".dev-webview"
$runtimeEnvs = Join-Path $dataRoot "runtime-envs"

if ($Clean) {
  # Keep runtime-envs: a Python runtime is expensive to reinstall and is not
  # user state. Only reset settings, models, library data, and WebView2.
  Get-ChildItem $dataRoot -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -notin @("runtime-envs", "models") } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item $webViewRoot -Recurse -Force -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Force -Path $dataRoot, $webViewRoot, (Join-Path $dataRoot "models"), $runtimeEnvs | Out-Null
$env:MEDIAFORGE_DATA_ROOT = $dataRoot
$env:MEDIAFORGE_DATA_DIR = $dataRoot
$env:MEDIAFORGE_MODEL_DIR = Join-Path $dataRoot "models"
$env:WEBVIEW2_USER_DATA_FOLDER = $webViewRoot
$env:MEDIAFORGE_RUNTIME_ENVS_DIR = $runtimeEnvs

Write-Host "MediaForge dev profile"
Write-Host "  data:     $dataRoot"
Write-Host "  models:   $env:MEDIAFORGE_MODEL_DIR"
Write-Host "  runtime:  $runtimeEnvs"
Write-Host "  webview2: $webViewRoot"

Set-Location $root
pnpm tauri dev
