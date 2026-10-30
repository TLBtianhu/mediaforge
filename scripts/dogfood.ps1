# Launch an installed/portable release as a normal long-lived user (dogfood profile).
# This intentionally uses the default OS user data and WebView2 cache:
# no MEDIAFORGE_* overrides and no project-local data directory.
#
# Do NOT point this at src-tauri/target/release: the app treats Cargo profile
# executables as development builds and would use the project data directory.
param(
  [string]$Exe = "C:\Program Files\MediaForge\MediaForge.exe"
)

$ErrorActionPreference = "Stop"
$exePath = [IO.Path]::GetFullPath($Exe)
if (-not (Test-Path -LiteralPath $exePath)) {
  throw "Installed executable not found: $exePath. Pass -Exe <installed MediaForge.exe>."
}
$targetRoot = [IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $PSScriptRoot) "src-tauri\target")).TrimEnd('\') + '\'
if ($exePath.StartsWith($targetRoot, [StringComparison]::OrdinalIgnoreCase)) {
  throw "Dogfood must use an installed/portable copy outside src-tauri/target."
}

$names = @("MEDIAFORGE_DATA_ROOT", "MEDIAFORGE_DATA_DIR", "MEDIAFORGE_MODEL_DIR", "WEBVIEW2_USER_DATA_FOLDER")
$previous = @{}
foreach ($name in $names) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process") }

try {
  foreach ($name in $names) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
  Write-Host "Dogfood profile"
  Write-Host "  exe: $exePath"
  Write-Host "  data: default user profile (~/.mediaforge)"
  Write-Host "  webview2: default app profile"
  Write-Host "This profile persists across runs and is the one used to test upgrades."
  $process = Start-Process -FilePath $exePath -PassThru
  Wait-Process -Id $process.Id
}
finally {
  foreach ($name in $names) {
    if ($null -eq $previous[$name]) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
    else { Set-Item "Env:$name" $previous[$name] }
  }
}
