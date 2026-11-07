# Real-process path contract: installed/portable defaults and explicit profile isolation.
# Copies only the executable into temporary layouts. Never installs or changes registry/profile state.
param([Parameter(Mandatory = $true)][string]$AppExe)

$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath $AppExe).Path
$work = Join-Path $env:TEMP ('mediaforge-asset-paths-' + [Guid]::NewGuid().ToString('N'))
$installed = Join-Path $work 'installed'
$portable = Join-Path $work 'portable'
New-Item -ItemType Directory -Force -Path (Join-Path $installed 'app'), $portable | Out-Null
Copy-Item -LiteralPath $source -Destination (Join-Path $installed 'app\MediaForge.exe')
Copy-Item -LiteralPath $source -Destination (Join-Path $portable 'MediaForge.exe')
Set-Content -LiteralPath (Join-Path $portable 'mediaforge.portable') -Value 'portable' -Encoding ASCII

$overrides = @('MEDIAFORGE_DATA_ROOT', 'MEDIAFORGE_DATA_DIR', 'PYMSS_STUDIO_DATA_ROOT', 'MEDIAFORGE_MODEL_DIR', 'PYMSS_MODEL_DIR', 'MEDIAFORGE_RUNTIME_ENVS_DIR')
$names = $overrides + @('USERPROFILE', 'HOME', 'APPDATA', 'LOCALAPPDATA', 'WEBVIEW2_USER_DATA_FOLDER')
$previous = @{}
foreach ($name in $names) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }

function Assert-Path([string]$Actual, [string]$Expected) {
    if (-not $Actual -or -not [string]::Equals([IO.Path]::GetFullPath($Actual), [IO.Path]::GetFullPath($Expected), [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path mismatch: expected '$Expected', got '$Actual'"
    }
}
function Invoke-Probe([string]$Exe, [string]$Case, [string]$Data, [string]$Models, [string]$Runtimes) {
    $stdout = Join-Path $work "$Case.stdout.log"
    $stderr = Join-Path $work "$Case.stderr.log"
    $process = Start-Process -FilePath $Exe -ArgumentList '--smoke-print-data-root' -Wait -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    if ($process.ExitCode -ne 0) { throw "Probe failed ($Case): $(Get-Content -LiteralPath $stderr -Raw)" }
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $stdout) {
        if ($line -match '^(SMOKE_[A-Z_]+)=(.*)$') { $values[$matches[1]] = $matches[2] }
    }
    Assert-Path $values.SMOKE_DATA_ROOT $Data
    Assert-Path $values.SMOKE_MODEL_ROOT $Models
    Assert-Path $values.SMOKE_RUNTIME_ROOT $Runtimes
    Assert-Path ([IO.Path]::GetDirectoryName($values.SMOKE_RUNTIME_ASSET_DIR)) $Runtimes
    if ($values.SMOKE_DEVELOPMENT -ne 'false') { throw 'Packaged fixture was treated as a development executable' }
    Write-Host "  PASS $Case"
}

try {
    $env:USERPROFILE = Join-Path $work 'profile'
    $env:HOME = $env:USERPROFILE
    $env:APPDATA = Join-Path $work 'appdata'
    $env:LOCALAPPDATA = Join-Path $work 'local-appdata'
    $env:WEBVIEW2_USER_DATA_FOLDER = Join-Path $work 'webview'
    New-Item -ItemType Directory -Force -Path $env:USERPROFILE, $env:APPDATA, $env:LOCALAPPDATA, $env:WEBVIEW2_USER_DATA_FOLDER | Out-Null
    foreach ($layout in @(@{ Name = 'installed'; Root = $installed; Exe = (Join-Path $installed 'app\MediaForge.exe') }, @{ Name = 'portable'; Root = $portable; Exe = (Join-Path $portable 'MediaForge.exe') })) {
        foreach ($name in $overrides) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
        Invoke-Probe $layout.Exe "$($layout.Name)-default" (Join-Path $layout.Root 'data') (Join-Path $layout.Root 'models') (Join-Path $layout.Root 'runtime-envs')

        $isolated = Join-Path $work "$($layout.Name)-isolated\data"
        $env:MEDIAFORGE_DATA_ROOT = $isolated
        $env:MEDIAFORGE_DATA_DIR = Join-Path $work 'stale-alias'
        Invoke-Probe $layout.Exe "$($layout.Name)-isolated" $isolated (Join-Path $isolated 'models') (Join-Path $isolated 'runtime-envs')

        $env:MEDIAFORGE_MODEL_DIR = Join-Path $work "$($layout.Name)-weights"
        $env:MEDIAFORGE_RUNTIME_ENVS_DIR = Join-Path $work "$($layout.Name)-runtimes"
        Invoke-Probe $layout.Exe "$($layout.Name)-asset-overrides" $isolated $env:MEDIAFORGE_MODEL_DIR $env:MEDIAFORGE_RUNTIME_ENVS_DIR
    }
} finally {
    foreach ($name in $names) {
        if ($null -eq $previous[$name]) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
        else { Set-Item "Env:$name" $previous[$name] }
    }
    . (Join-Path $PSScriptRoot 'temp-work-guard.ps1')
    $resolvedWork = Assert-TempWorkDir -Work $work -Prefix 'mediaforge-asset-paths-'
    Remove-Item -LiteralPath $resolvedWork -Recurse -Force
}
Write-Host 'PASS: installed/portable asset roots and isolated overrides'
