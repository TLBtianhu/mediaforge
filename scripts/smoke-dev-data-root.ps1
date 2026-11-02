# 开发形态数据根冒烟：真实开发 exe 必须选择仓库 .dev-data，且不能写用户 profile。
param(
    [string]$AppExe = "$PSScriptRoot\..\src-tauri\target\debug\pymss-studio.exe"
)

$ErrorActionPreference = 'Stop'
$failures = New-Object System.Collections.Generic.List[string]
function Check([string]$Name, [bool]$Ok, [string]$Detail = '') {
    if ($Ok) { Write-Host ("  [ok]   {0} {1}" -f $Name, $Detail) }
    else { Write-Host ("  [FAIL] {0} {1}" -f $Name, $Detail); $script:failures.Add($Name) }
}

$exe = (Resolve-Path $AppExe).Path
$projectRoot = Split-Path -Parent $PSScriptRoot
$expected = [IO.Path]::GetFullPath((Join-Path $projectRoot '.dev-data')).TrimEnd('\')
$work = Join-Path $env:TEMP ('mediaforge-dev-data-root-' + [guid]::NewGuid().ToString('N'))
$fakeUser = Join-Path $work 'user'
New-Item -ItemType Directory -Force -Path $fakeUser | Out-Null
$stdout = Join-Path $work 'dev-data-root.stdout.log'
$stderr = Join-Path $work 'dev-data-root.stderr.log'
$environmentNames = @(
    'MEDIAFORGE_DATA_ROOT', 'PYMSS_STUDIO_DATA_ROOT', 'MEDIAFORGE_DATA_DIR',
    'MEDIAFORGE_MODEL_DIR', 'MEDIAFORGE_RUNTIME_ENVS_DIR',
    'USERPROFILE', 'HOME', 'APPDATA', 'LOCALAPPDATA', 'WEBVIEW2_USER_DATA_FOLDER'
)
$environment = @{}
foreach ($name in $environmentNames) {
    $environment[$name] = [Environment]::GetEnvironmentVariable($name)
}

Write-Host '开发数据根冒烟（真实进程，无数据根覆盖）'
Write-Host "  exe:      $exe"
Write-Host "  expected: $expected"
Write-Host "  work:     $work"

try {
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $null)
    }
    $env:USERPROFILE = $fakeUser
    $env:HOME = $fakeUser
    $env:APPDATA = Join-Path $fakeUser 'AppData\Roaming'
    $env:LOCALAPPDATA = Join-Path $fakeUser 'AppData\Local'
    $env:WEBVIEW2_USER_DATA_FOLDER = Join-Path $work 'webview2'

    $process = Start-Process -FilePath $exe -ArgumentList '--smoke-print-data-root' -Wait -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $output = if (Test-Path $stdout) { Get-Content $stdout -Raw } else { '' }
    $errors = if (Test-Path $stderr) { Get-Content $stderr -Raw } else { '' }
    if ($output) { Write-Host $output.TrimEnd() }
    if ($errors) { Write-Host $errors.TrimEnd() }

    Check '真实进程退出成功' ($process.ExitCode -eq 0) "exit=$($process.ExitCode)"
    $rootLine = @($output -split '\r?\n' | Where-Object { $_ -like 'SMOKE_DATA_ROOT=*' }) | Select-Object -Last 1
    $actual = if ($rootLine) { $rootLine.Substring('SMOKE_DATA_ROOT='.Length).Trim().TrimEnd('\') } else { '' }
    Check '开发 exe 使用仓库 .dev-data' ($actual -and $actual.Equals($expected, [StringComparison]::OrdinalIgnoreCase)) $actual
    Check '目标 exe 被识别为开发形态' ($output -match '(?m)^SMOKE_DEVELOPMENT=true\s*$')
    Check '临时用户目录未出现 .mediaforge' (-not (Test-Path (Join-Path $fakeUser '.mediaforge')))
} finally {
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $environment[$name])
    }
}

if ($failures.Count -gt 0) {
    Write-Host ("FAIL: {0}; 现场保留在 {1}" -f ($failures -join ', '), $work)
    exit 1
}
. (Join-Path $PSScriptRoot 'temp-work-guard.ps1')
$resolvedWork = Assert-TempWorkDir -Work $work -Prefix 'mediaforge-dev-data-root-'
Remove-Item -LiteralPath $resolvedWork -Recurse -Force
Write-Host 'PASS: 开发 exe 只解析到仓库 .dev-data，未使用用户数据根'
