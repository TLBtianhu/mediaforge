# 干净 profile 冒烟：装一次、启动一次、跑一次 worker、卸载一次。
#
# 目的不是测功能，而是测"第一次装的人会遇到什么"：
#   * 安装是否静默成功（per-user，不需要管理员）
#   * python-runtime / bin\vcrt / manifest 是否齐备
#   * 引导解释器能否在干净数据根上跑 worker health
#   * 应用能否启动并创建自己的数据目录
#   * 卸载是否只删程序、保留数据
param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\MediaForge'),
    [string]$DataRoot = (Join-Path $env:TEMP ('mediaforge-smoke-' + [guid]::NewGuid().ToString('N'))),
    [int]$LaunchSeconds = 10
)

$ErrorActionPreference = 'Stop'
$failures = New-Object System.Collections.Generic.List[string]

function Check([string]$Name, [bool]$Ok, [string]$Detail = '') {
    if ($Ok) {
        Write-Host ("  [ok]   {0} {1}" -f $Name, $Detail)
    } else {
        Write-Host ("  [FAIL] {0} {1}" -f $Name, $Detail)
        $script:failures.Add("$Name $Detail")
    }
}

$installer = (Resolve-Path $Installer).Path
Write-Host "干净 profile 冒烟"
Write-Host "  installer: $installer"
Write-Host "  data root: $DataRoot"

# 1. 静默安装
$install = Start-Process -FilePath $installer -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru
Check '安装进程退出码为 0' ($install.ExitCode -eq 0) "exit=$($install.ExitCode)"
Check '程序目录存在' (Test-Path (Join-Path $InstallDir 'MediaForge.exe'))
Check '契约清单随包' (Test-Path (Join-Path $InstallDir 'manifest\runtime.json'))
Check '引导解释器存在' (Test-Path (Join-Path $InstallDir 'python-runtime\python.exe'))

$vcrt = @()
$vcrtDir = Join-Path $InstallDir 'bin\vcrt'
if (Test-Path $vcrtDir) { $vcrt = @(Get-ChildItem $vcrtDir -Filter '*.dll') }
Check 'VC++ 运行库已随包' ($vcrt.Count -ge 6) "dll=$($vcrt.Count)"

# 2. 干净数据根 + 引导解释器跑 worker health
New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
$python = Join-Path $InstallDir 'python-runtime\python.exe'
$worker = Join-Path $InstallDir 'python\worker.py'
$env:MEDIAFORGE_DATA_ROOT = $DataRoot
$health = & $python $worker health 2>&1 | Select-Object -Last 1
$healthOk = $false
try {
    $parsed = $health | ConvertFrom-Json
    $healthOk = $parsed.type -eq 'health' -and $parsed.payload.ok -eq $true
} catch { }
Check 'worker health 在干净数据根上通过' $healthOk

# 3. 启动应用（后台），确认进程活着并创建数据目录
$app = Start-Process -FilePath (Join-Path $InstallDir 'MediaForge.exe') -PassThru
Start-Sleep -Seconds $LaunchSeconds
Check '应用启动后保持运行' (-not $app.HasExited)
Check '应用创建了数据根' (Test-Path (Join-Path $DataRoot 'settings'))
if (-not $app.HasExited) { Stop-Process -Id $app.Id -Force }
Start-Sleep -Seconds 2

# 4. 卸载：程序目录消失，数据根保留
$uninstaller = Join-Path $InstallDir 'unins000.exe'
if (Test-Path $uninstaller) {
    $un = Start-Process -FilePath $uninstaller -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru
    Check '卸载进程退出码为 0' ($un.ExitCode -eq 0) "exit=$($un.ExitCode)"
    Start-Sleep -Seconds 2
    Check '卸载后程序目录消失' (-not (Test-Path (Join-Path $InstallDir 'MediaForge.exe')))
    Check '卸载保留用户数据' (Test-Path (Join-Path $DataRoot 'settings'))
} else {
    Write-Host '  [skip] 没找到卸载程序（可能是便携版）'
}

Remove-Item Env:MEDIAFORGE_DATA_ROOT -ErrorAction SilentlyContinue
if ($failures.Count -gt 0) {
    Write-Host ""
    Write-Host ("FAILED: {0} 项" -f $failures.Count)
    $failures | ForEach-Object { Write-Host ("  - {0}" -f $_) }
    exit 1
}
Write-Host ""
Write-Host 'PASS: 安装 / 启动 / worker / 卸载 全部通过'
exit 0
