# 覆盖升级真实 E2E：编译 A/B 两个安装包，装 A → 造数据 → 覆盖装 B → 校验 → 卸载。
#
# 验证的是"老用户点升级会发生什么"，不是功能：
#   * 同一 AppId 覆盖安装后，配置 / 模型 / 运行时资产原样保留
#   * 新版文件确实落盘（不是装了个寂寞）
#   * pyvenv.cfg 被重指到新程序目录，且写在 MEDIAFORGE_DATA_ROOT 里，不碰真实 ~/.mediaforge
#   * 不写入旧安装目录、不写入无关临时目录
#   * 卸载只删程序、保留数据
#
# 隔离：数据根、安装目录、stage 全部在临时 WorkRoot 下；DSH 注册脚本从 stage 副本里剔除，
# 不会改写 DeepSeek Harness 配置；HKCU\Software\MediaForge\InstallDir 先备份再恢复。
param(
    [string]$StageDir,
    [string]$WorkRoot = (Join-Path $env:TEMP ('mediaforge-upgrade-' + [guid]::NewGuid().ToString('N'))),
    [string]$VersionA = '0.0.1',
    [string]$VersionB = '0.0.2',
    [switch]$KeepWork
)

$ErrorActionPreference = 'Stop'
$failures = New-Object System.Collections.Generic.List[string]
$root = Split-Path -Parent $PSScriptRoot

function Check([string]$Name, [bool]$Ok, [string]$Detail = '') {
    if ($Ok) { Write-Host ("  [ok]   {0} {1}" -f $Name, $Detail) }
    else {
        Write-Host ("  [FAIL] {0} {1}" -f $Name, $Detail)
        $script:failures.Add("$Name $Detail")
    }
}
function Hash-Of([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash }
function Write-Stub([string]$Path) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    [System.IO.File]::WriteAllBytes($Path, [byte[]](0x4D, 0x5A, 0x90, 0x00, 0x03, 0x00, 0x00, 0x00))
}
function Snapshot-Tree([string]$Dir) {
    if (-not (Test-Path $Dir)) { return @{} }
    $map = @{}
    Get-ChildItem $Dir -Recurse -File | ForEach-Object { $map[$_.FullName] = Hash-Of $_.FullName }
    return $map
}
function Test-SameTree([hashtable]$Before, [string]$Dir) {
    $after = Snapshot-Tree $Dir
    if ($after.Count -ne $Before.Count) { return $false }
    foreach ($k in $Before.Keys) {
        if (-not $after.ContainsKey($k)) { return $false }
        if ($after[$k] -ne $Before[$k]) { return $false }
    }
    return $true
}

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe'
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw '没有找到 Inno Setup 编译器 ISCC.exe' }

$installDir = Join-Path $WorkRoot 'install'
$dataRoot = Join-Path $WorkRoot 'data'
$outDir = Join-Path $WorkRoot 'out'
$oldInstallDir = Join-Path $WorkRoot 'old-install'
$foreignTemp = Join-Path $WorkRoot 'foreign-temp'
New-Item -ItemType Directory -Force -Path $installDir, $dataRoot, $outDir, $oldInstallDir, $foreignTemp | Out-Null

Write-Host "覆盖升级冒烟"
Write-Host "  work root : $WorkRoot"
Write-Host "  install   : $installDir"
Write-Host "  data root : $dataRoot"
Write-Host "  iscc      : $iscc"

# ---- stage：默认夹具；真实 stage 先复制再剔除 DSH 注册脚本 ----
$stageA = Join-Path $WorkRoot 'stageA'
$stageB = Join-Path $WorkRoot 'stageB'
if ($StageDir) {
    $resolved = (Resolve-Path $StageDir).Path
    Copy-Item $resolved $stageA -Recurse -Force
} else {
    Write-Stub (Join-Path $stageA 'MediaForge.exe')
    Write-Stub (Join-Path $stageA 'python-runtime\python.exe')
    New-Item -ItemType Directory -Force -Path (Join-Path $stageA 'manifest') | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $stageA 'manifest\runtime.json'), '{"schemaVersion":1}')
}
Copy-Item $stageA $stageB -Recurse -Force
foreach ($stage in $stageA, $stageB) {
    # 绝不让安装包去改 DeepSeek Harness 的配置
    Remove-Item (Join-Path $stage 'integrations\dsh-mediaforge\register.ps1') -Force -ErrorAction SilentlyContinue
}
[System.IO.File]::WriteAllText((Join-Path $stageA 'app-marker.txt'), "version=$VersionA`n")
[System.IO.File]::WriteAllText((Join-Path $stageB 'app-marker.txt'), "version=$VersionB`n")

# ---- 编译 A/B ----
$localIss = Join-Path $root 'installer\mediaforge.upgrade-smoke.iss'
$previousDataRoot = $env:MEDIAFORGE_DATA_ROOT
$regPath = 'HKCU:\Software\MediaForge'
$regBackup = Get-ItemProperty -Path $regPath -Name InstallDir -ErrorAction SilentlyContinue
$realVenv = Join-Path $env:USERPROFILE '.mediaforge\runtime-envs\cuda\pyvenv.cfg'
$realVenvHash = if (Test-Path $realVenv) { Hash-Of $realVenv } else { $null }
# 兜底：即使数据根修复失效误写了真实 profile，也要能原样恢复
$venvBackups = @{}
foreach ($backend in 'cpu', 'cuda', 'rocm') {
    $cfg = Join-Path $env:USERPROFILE ".mediaforge\runtime-envs\$backend\pyvenv.cfg"
    if (Test-Path $cfg) { $venvBackups[$cfg] = [System.IO.File]::ReadAllBytes($cfg) }
}

function Build-Installer([string]$Stage, [string]$Version) {
    # 本机 Inno 不认 SetupArchitecture；部分 CI 镜像的 Inno 不带简体中文语言文件。
    # 只对冒烟副本做兼容处理，仓库里的 .iss 保持原样。
    $hasChinese = Test-Path (Join-Path (Split-Path -Parent $iscc) 'Languages\ChineseSimplified.isl')
    Get-Content (Join-Path $root 'installer\mediaforge.iss') |
        Where-Object {
            $_ -notmatch '^SetupArchitecture=' -and ($hasChinese -or $_ -notmatch 'ChineseSimplified\.isl')
        } |
        Set-Content $localIss -Encoding UTF8
    $env:MEDIAFORGE_PORTABLE_DIR = $Stage
    $env:MEDIAFORGE_VERSION = $Version
    $env:MEDIAFORGE_INSTALLER_OUTPUT = $outDir
    $log = & $iscc $localIss 2>&1
    if ($LASTEXITCODE -ne 0) { throw "ISCC 编译 $Version 失败：`n$($log -join "`n")" }
    $exe = Join-Path $outDir "MediaForge-$Version-windows-x64-cuda-setup.exe"
    if (-not (Test-Path $exe)) { throw "没有找到安装包 $exe" }
    return $exe
}

try {
    $env:MEDIAFORGE_DATA_ROOT = $dataRoot
    $installerA = Build-Installer $stageA $VersionA
    $installerB = Build-Installer $stageB $VersionB
    Write-Host "  A: $installerA"
    Write-Host "  B: $installerB"

    Write-Host "`n[1/4] 安装 A"
    $a = Start-Process -FilePath $installerA -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=`"$installDir`"" -Wait -PassThru
    Check 'A 安装退出码为 0' ($a.ExitCode -eq 0) "exit=$($a.ExitCode)"
    Check 'A 程序已落盘' (Test-Path (Join-Path $installDir 'app\MediaForge.exe'))
    Check 'A 版本标记正确' ((Get-Content (Join-Path $installDir 'app\app-marker.txt') -Raw).Trim() -eq "version=$VersionA")

    Write-Host "`n[2/4] 造一份老用户数据"
    $settings = Join-Path $dataRoot 'settings\settings.json'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $settings) | Out-Null
    [System.IO.File]::WriteAllText($settings, '{"theme":"dark","__e2e__":"keep-me"}')
    $weights = Join-Path $dataRoot 'models\demo-model\weights.bin'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $weights) | Out-Null
    $bytes = New-Object byte[] 4096
    (New-Object Random 42).NextBytes($bytes)
    [System.IO.File]::WriteAllBytes($weights, $bytes)
    $venvDir = Join-Path $dataRoot 'runtime-envs\cuda'
    Write-Stub (Join-Path $venvDir 'Scripts\python.exe')
    $venvCfg = Join-Path $venvDir 'pyvenv.cfg'
    [System.IO.File]::WriteAllText($venvCfg, "home = C:\stale\python-runtime`ninclude-system-site-packages = false`n")
    $settingsHash = Hash-Of $settings
    $weightsHash = Hash-Of $weights
    # 旧安装目录与无关临时目录：各放一个哨兵，升级后必须原样
    Write-Stub (Join-Path $oldInstallDir 'MediaForge.exe')
    [System.IO.File]::WriteAllText((Join-Path $foreignTemp 'scratch.txt'), 'do-not-touch')
    $oldBefore = Snapshot-Tree $oldInstallDir
    $foreignBefore = Snapshot-Tree $foreignTemp

    Write-Host "`n[3/4] 覆盖安装 B"
    $b = Start-Process -FilePath $installerB -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=`"$installDir`"" -Wait -PassThru
    Check 'B 安装退出码为 0' ($b.ExitCode -eq 0) "exit=$($b.ExitCode)"
    Check 'B 版本标记已替换' ((Get-Content (Join-Path $installDir 'app\app-marker.txt') -Raw).Trim() -eq "version=$VersionB")
    Check '配置文件保留' ((Test-Path $settings) -and ((Hash-Of $settings) -eq $settingsHash))
    Check '模型权重保留' ((Test-Path $weights) -and ((Hash-Of $weights) -eq $weightsHash))
    Check '运行时解释器保留' (Test-Path (Join-Path $venvDir 'Scripts\python.exe'))
    $cfgText = if (Test-Path $venvCfg) { Get-Content $venvCfg -Raw } else { '' }
    $expectedHome = 'home = ' + (Join-Path $installDir 'app\python-runtime')
    Check 'pyvenv.cfg 重指到新程序目录' ($cfgText.Contains($expectedHome)) $expectedHome
    Check 'pyvenv.cfg 仍在测试数据根内' ($venvCfg.StartsWith($dataRoot, [System.StringComparison]::OrdinalIgnoreCase))
    if ($realVenvHash) {
        Check '真实用户数据根未被改写' ((Hash-Of $realVenv) -eq $realVenvHash) $realVenv
    } else {
        Check '没有误建真实用户数据根' (-not (Test-Path (Join-Path $env:USERPROFILE '.mediaforge\runtime-envs\cuda\pyvenv.cfg')))
    }
    Check '旧安装目录未被写入' (Test-SameTree $oldBefore $oldInstallDir)
    Check '无关临时目录未被写入' (Test-SameTree $foreignBefore $foreignTemp)

    Write-Host "`n[4/5] 安装态默认数据根"
    Remove-Item Env:MEDIAFORGE_DATA_ROOT -ErrorAction SilentlyContinue
    $probeOut = Join-Path $WorkRoot 'probe.stdout.log'
    $probeErr = Join-Path $WorkRoot 'probe.stderr.log'
    $probeProcess = Start-Process -FilePath (Join-Path $installDir 'app\MediaForge.exe') -ArgumentList '--smoke-print-data-root' -Wait -PassThru -RedirectStandardOutput $probeOut -RedirectStandardError $probeErr
    $env:MEDIAFORGE_DATA_ROOT = $dataRoot
    $expectedDataRoot = Join-Path $installDir 'data'
    $probeText = @((Get-Content $probeOut -Raw -ErrorAction SilentlyContinue), (Get-Content $probeErr -Raw -ErrorAction SilentlyContinue)) -join "`n"
    Write-Host "  probe: $probeText"
    Check '安装态默认数据根位于安装根 data' ($probeText -match [regex]::Escape($expectedDataRoot)) $probeText

    Write-Host "`n[5/5] 卸载"
    $uninstaller = Join-Path $installDir 'unins000.exe'
    Check '卸载程序存在' (Test-Path $uninstaller)
    if (Test-Path $uninstaller) {
        $un = Start-Process -FilePath $uninstaller -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru
        Check '卸载退出码为 0' ($un.ExitCode -eq 0) "exit=$($un.ExitCode)"
        Start-Sleep -Seconds 2
        Check '卸载后程序目录消失' (-not (Test-Path (Join-Path $installDir 'app\MediaForge.exe')))
        Check '卸载保留配置与模型' ((Test-Path $settings) -and (Test-Path $weights))
    }
} finally {
    Remove-Item $localIss -Force -ErrorAction SilentlyContinue
    if ($previousDataRoot) { $env:MEDIAFORGE_DATA_ROOT = $previousDataRoot } else { Remove-Item Env:MEDIAFORGE_DATA_ROOT -ErrorAction SilentlyContinue }
    if ($regBackup) {
        # 卸载会把整个键删掉（uninsdeletekey），恢复前必须重建
        New-Item -Path $regPath -Force | Out-Null
        Set-ItemProperty -Path $regPath -Name InstallDir -Value $regBackup.InstallDir
    } else {
        Remove-ItemProperty -Path $regPath -Name InstallDir -ErrorAction SilentlyContinue
    }
    foreach ($cfg in $venvBackups.Keys) {
        $current = [System.IO.File]::ReadAllBytes($cfg)
        if ([Convert]::ToBase64String($current) -ne [Convert]::ToBase64String($venvBackups[$cfg])) {
            [System.IO.File]::WriteAllBytes($cfg, $venvBackups[$cfg])
            Write-Host "  [restore] 已恢复真实数据根 $cfg"
        }
    }
}

if ($failures.Count -gt 0) {
    Write-Host ""
    Write-Host ("FAILED: {0} 项，现场保留在 {1}" -f $failures.Count, $WorkRoot)
    $failures | ForEach-Object { Write-Host ("  - {0}" -f $_) }
    exit 1
}
if (-not $KeepWork) {
    . (Join-Path $PSScriptRoot 'temp-work-guard.ps1')
    $resolvedWork = Assert-TempWorkDir -Work $WorkRoot -Prefix 'mediaforge-upgrade-'
    Remove-Item -LiteralPath $resolvedWork -Recurse -Force
}
Write-Host ""
Write-Host 'PASS: 覆盖升级保留数据、不重指错数据根、不越界写入'
exit 0
