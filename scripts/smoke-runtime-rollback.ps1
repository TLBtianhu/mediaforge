# 小资产运行时回滚端到端：A 安装 → B 安装 → 不下载直接激活 A。
# 使用本地 HTTP 与隔离数据根；两个资产都保留，active-runtime.json 必须可逆切换。
param(
    [string]$AppExe = "$PSScriptRoot\..\release\stage-0.0.23\MediaForge.exe",
    [int]$Port = 8766
)

$ErrorActionPreference = 'Stop'
$failures = New-Object System.Collections.Generic.List[string]
function Check([string]$Name, [bool]$Ok, [string]$Detail = '') {
    if ($Ok) { Write-Host ("  [ok]   {0} {1}" -f $Name, $Detail) }
    else { Write-Host ("  [FAIL] {0} {1}" -f $Name, $Detail); $script:failures.Add($Name) }
}
function Invoke-RuntimeSmoke([string]$Mode, [string]$DataRoot, [hashtable]$Descriptor) {
    $env:MEDIAFORGE_DATA_ROOT = $DataRoot
    $env:MEDIAFORGE_SMOKE_RUNTIME_DESCRIPTOR = ($Descriptor | ConvertTo-Json -Compress)
    $process = Start-Process -FilePath $script:exe -ArgumentList $Mode -Wait -PassThru -WindowStyle Hidden
    return $process.ExitCode
}
function Read-ActiveAsset([string]$DataRoot) {
    $pointer = Join-Path $DataRoot 'runtime-envs\active-runtime.json'
    if (-not (Test-Path $pointer)) { return '' }
    return (Get-Content $pointer -Raw | ConvertFrom-Json).assetId
}

$script:exe = (Resolve-Path $AppExe).Path
$work = Join-Path $env:TEMP ('mediaforge-runtime-rollback-' + [guid]::NewGuid().ToString('N'))
$httpRoot = Join-Path $work 'http'
$dataRoot = Join-Path $work 'data'
New-Item -ItemType Directory -Force -Path $httpRoot, $dataRoot | Out-Null
Write-Host "运行时回滚冒烟（小资产，本地 HTTP）"
Write-Host "  exe: $script:exe"
Write-Host "  work: $work"

# 解释器自检只要求可执行文件成功退出。现场编译一个微型 python.exe，
# 避免把 3.6 GB 真运行时或用户现有环境带进测试。
$fixtureSource = Join-Path $work 'fake-python.rs'
Set-Content $fixtureSource 'fn main() { std::process::exit(0); }' -Encoding ASCII
$fakePython = Join-Path $work 'python.exe'
& rustc --edition 2021 -C opt-level=1 -o $fakePython $fixtureSource
if ($LASTEXITCODE -ne 0) { throw '无法编译小资产解释器' }

$assets = @{}
foreach ($name in @('runtime-a', 'runtime-b')) {
    $stage = Join-Path $work "stage-$name"
    $scripts = Join-Path $stage 'Scripts'
    New-Item -ItemType Directory -Force -Path $scripts | Out-Null
    Copy-Item $fakePython (Join-Path $scripts 'python.exe')
    Set-Content (Join-Path $stage 'asset.txt') $name -Encoding ASCII
    $zip = Join-Path $httpRoot "$name.zip"
    Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -Force
    $assets[$name] = @{
        assetId = $name
        backend = 'fixture'
        sha256 = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLowerInvariant()
        size = (Get-Item $zip).Length
        urls = @("http://127.0.0.1:$Port/$name.zip")
    }
}

$python = "$env:USERPROFILE\.mediaforge\runtime-envs\cuda\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = 'python' }
$server = Start-Process -FilePath $python -ArgumentList @('-m','http.server',"$Port",'--bind','127.0.0.1','--directory',$httpRoot) -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 2
if ($server.HasExited) { throw "本地 HTTP 服务启动失败，端口 $Port 可能已被占用" }

try {
    Write-Host '  [1/3] 安装资产 A'
    $exit = Invoke-RuntimeSmoke '--smoke-install-runtime' $dataRoot $assets['runtime-a']
    Check 'A 安装成功' ($exit -eq 0) "exit=$exit"
    Check 'A 成为活动资产' ((Read-ActiveAsset $dataRoot) -eq 'runtime-a') (Read-ActiveAsset $dataRoot)

    Write-Host '  [2/3] 安装资产 B，A 必须保留'
    $exit = Invoke-RuntimeSmoke '--smoke-install-runtime' $dataRoot $assets['runtime-b']
    Check 'B 安装成功' ($exit -eq 0) "exit=$exit"
    Check 'B 成为活动资产' ((Read-ActiveAsset $dataRoot) -eq 'runtime-b') (Read-ActiveAsset $dataRoot)
    Check 'A 目录仍然存在' (Test-Path (Join-Path $dataRoot 'runtime-envs\runtime-a\Scripts\python.exe'))
    Check 'B 目录仍然存在' (Test-Path (Join-Path $dataRoot 'runtime-envs\runtime-b\Scripts\python.exe'))

    Write-Host '  [3/3] 走 ready 分支直接激活 A，不重新下载'
    $exit = Invoke-RuntimeSmoke '--smoke-activate-runtime' $dataRoot $assets['runtime-a']
    Check 'ready 复用分支成功' ($exit -eq 0) "exit=$exit"
    Check '激活指针回滚到 A' ((Read-ActiveAsset $dataRoot) -eq 'runtime-a') (Read-ActiveAsset $dataRoot)
    Check '回滚后 B 仍保留' (Test-Path (Join-Path $dataRoot 'runtime-envs\runtime-b\Scripts\python.exe'))
    Check 'A 内容未被 B 覆盖' ((Get-Content (Join-Path $dataRoot 'runtime-envs\runtime-a\asset.txt') -Raw).Trim() -eq 'runtime-a')
} finally {
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }
    $env:MEDIAFORGE_DATA_ROOT = $null
    $env:MEDIAFORGE_SMOKE_RUNTIME_DESCRIPTOR = $null
}

if ($failures.Count -gt 0) {
    Write-Host ("FAIL: {0}; 现场保留在 {1}" -f ($failures -join ', '), $work)
    exit 1
}
$resolvedWork = (Resolve-Path $work).Path
$expectedPrefix = [System.IO.Path]::GetFullPath($env:TEMP) + [System.IO.Path]::DirectorySeparatorChar + 'mediaforge-runtime-rollback-'
if (-not $resolvedWork.StartsWith($expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) { throw "拒绝清理意外路径：$resolvedWork" }
Remove-Item -LiteralPath $resolvedWork -Recurse -Force
Write-Host 'PASS: 运行时 A→B→A 复用与回滚端到端成立'
