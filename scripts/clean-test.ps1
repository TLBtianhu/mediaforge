# Run a packaged/clean build against a throwaway data + WebView2 profile.
# Use this to verify "first run with no personalized config" without touching
# the developer profile or the dogfood/user profile.
param(
  [string]$Exe = "src-tauri\target\release\pymss-studio.exe"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$exePath = [IO.Path]::GetFullPath((Join-Path $root $Exe))
if (-not (Test-Path -LiteralPath $exePath)) {
  throw "Executable not found: $exePath (run pnpm tauri build first)"
}

$tempRoot = Join-Path $env:TEMP ("MediaForge-clean-" + [Guid]::NewGuid().ToString("N"))
$dataRoot = Join-Path $tempRoot "data"
$webViewRoot = Join-Path $tempRoot "webview"
New-Item -ItemType Directory -Force -Path $dataRoot, $webViewRoot, (Join-Path $dataRoot "models") | Out-Null

$names = @("MEDIAFORGE_DATA_ROOT", "MEDIAFORGE_DATA_DIR", "MEDIAFORGE_MODEL_DIR", "WEBVIEW2_USER_DATA_FOLDER")
$previous = @{}
foreach ($name in $names) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process") }

try {
  $env:MEDIAFORGE_DATA_ROOT = $dataRoot
  $env:MEDIAFORGE_DATA_DIR = $dataRoot
  $env:MEDIAFORGE_MODEL_DIR = Join-Path $dataRoot "models"
  $env:WEBVIEW2_USER_DATA_FOLDER = $webViewRoot
  Write-Host "Clean-profile test"
  Write-Host "  exe:      $exePath"
  Write-Host "  data:     $dataRoot"
  Write-Host "  webview2: $webViewRoot"
  Write-Host "Close the app window to remove this temporary profile."
  $process = Start-Process -FilePath $exePath -PassThru
  Wait-Process -Id $process.Id
}
finally {
  foreach ($name in $names) {
    if ($null -eq $previous[$name]) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
    else { Set-Item "Env:$name" $previous[$name] }
  }
  Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
  Write-Host "Removed temporary profile: $tempRoot"
}
