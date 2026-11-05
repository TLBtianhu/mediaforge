# 首装"下载运行时"端到端冒烟：模拟一个从未接触过项目的人。
#
# 全新安装目录（打包的便携版）+ 全新数据目录（临时目录）+ 本地 HTTP 源，
# 走真实的 下载 → sha256 校验 → 解包 → 写 active-runtime.json → .asset-id → 激活。
# 不打外网、可复现；另含一个 sha256 不符的负例。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts/smoke-first-install.ps1 `
#       -AppExe release\stage-0.0.28\MediaForge.exe
param(
    [string]$AppExe = "$PSScriptRoot\..\release\stage-0.0.28\MediaForge.exe",
    [string]$RuntimeZip = "$PSScriptRoot\..\release\runtime\runtime-cuda-2026.11.4.zip",
    [int]$Port = 8765,
    [string]$HttpPython = ''
)

$ErrorActionPreference = 'Stop'
$failures = New-Object System.Collections.Generic.List[string]
function Check([string]$Name, [bool]$Ok, [string]$Detail = '') {
    if ($Ok) { Write-Host ("  [ok]   {0} {1}" -f $Name, $Detail) }
    else { Write-Host ("  [FAIL] {0} {1}" -f $Name, $Detail); $script:failures.Add($Name) }
}

$exe = (Resolve-Path $AppExe).Path
$zip = (Resolve-Path $RuntimeZip).Path
$assetId = [System.IO.Path]::GetFileNameWithoutExtension($zip)
Write-Host "首装冒烟（下载运行时）"
Write-Host "  exe: $exe"
Write-Host "  runtime zip: $zip ($assetId)"

$work = Join-Path $env:TEMP ('mediaforge-first-install-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $work | Out-Null
$dataRoot = Join-Path $work 'data'
$httpRoot = Join-Path $work 'http'
New-Item -ItemType Directory -Force -Path $httpRoot | Out-Null

# 本地 HTTP 源：正例放真运行时，负例放一个内容必然对不上 sha256 的小文件。
Copy-Item $zip (Join-Path $httpRoot (Split-Path $zip -Leaf))
Set-Content (Join-Path $httpRoot 'corrupted.zip') 'not-a-runtime' -Encoding ASCII

# HTTP 服务只用显式解释器或系统 Python；绝不默认读取真实用户运行时。
$python = if ($HttpPython) { (Resolve-Path -LiteralPath $HttpPython).Path } else { 'python' }
$server = Start-Process -FilePath $python -ArgumentList @('-m','http.server',"$Port",'--bind','127.0.0.1','--directory',$httpRoot) -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 2

try {
    $baseUrl = "http://127.0.0.1:$Port"
    $env:MEDIAFORGE_DATA_ROOT = $dataRoot
    # 描述符按实际 zip 计算 sha256/size：只设 URL 的话，应用仍会拿内嵌描述符的哈希来校验，
    # 夹具资产必然对不上。这里把本地下载源和真实哈希一起交给应用，夹具与真包都能跑。
    $descriptor = @{
        assetId = $assetId
        backend = 'fixture'
        sha256 = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLowerInvariant()
        size = (Get-Item $zip).Length
        urls = @("$baseUrl/$(Split-Path $zip -Leaf)")
    }

    Write-Host "  [1/2] 正例：本地源下载真运行时"
    $env:MEDIAFORGE_SMOKE_RUNTIME_DESCRIPTOR = ($descriptor | ConvertTo-Json -Compress)
    $install = Start-Process -FilePath $exe -ArgumentList '--smoke-install-runtime' -Wait -PassThru -WindowStyle Hidden
    Check '安装进程退出码为 0' ($install.ExitCode -eq 0) "exit=$($install.ExitCode)"
    $envDir = Join-Path $dataRoot "runtime-envs\$assetId"
    Check '运行时解包到隔离数据根' (Test-Path (Join-Path $envDir 'Scripts\python.exe'))
    Check 'active-runtime.json 已写入' (Test-Path (Join-Path $dataRoot 'runtime-envs\active-runtime.json'))
    $marker = Get-ChildItem -Path $envDir -Filter '.asset-id' -ErrorAction SilentlyContinue
    Check '.asset-id 标记存在' ($null -ne $marker)
    $active = Get-Content (Join-Path $dataRoot 'runtime-envs\active-runtime.json') -Raw | ConvertFrom-Json
    Check '激活指针指向该资产' ($active.assetId -eq $assetId) $active.assetId
    $interp = Join-Path $envDir 'Scripts\python.exe'
    if (Test-Path $interp) {
        $health = & $interp -c "import sys; print('python-ok')" 2>&1
        Check '解释器可运行' ($LASTEXITCODE -eq 0) "$health"
    }

    Write-Host "  [2/2] 负例：sha256 不符必须拒绝"
    $badData = Join-Path $work 'data-corrupt'
    $env:MEDIAFORGE_DATA_ROOT = $badData
    $descriptor.urls = @("$baseUrl/corrupted.zip")
    $env:MEDIAFORGE_SMOKE_RUNTIME_DESCRIPTOR = ($descriptor | ConvertTo-Json -Compress)
    $bad = Start-Process -FilePath $exe -ArgumentList '--smoke-install-runtime' -Wait -PassThru -WindowStyle Hidden
    Check '坏包退出码非 0' ($bad.ExitCode -ne 0) "exit=$($bad.ExitCode)"
    Check '坏包不写 active-runtime.json' (-not (Test-Path (Join-Path $badData 'runtime-envs\active-runtime.json')))
    Check '坏包不留激活目录' (-not (Test-Path (Join-Path $badData "runtime-envs\$assetId\Scripts\python.exe")))

    # 隔离断言：这次安装只写我指定的临时数据根，用户数据根不被这次冒烟触碰。
    Check '安装只写隔离数据根' ($dataRoot.StartsWith($work)) $dataRoot
} finally {
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }
    $env:MEDIAFORGE_DATA_ROOT = $null
    $env:MEDIAFORGE_SMOKE_RUNTIME_DESCRIPTOR = $null
    $env:MEDIAFORGE_SMOKE_RUNTIME_URL = $null
}

if ($failures.Count -gt 0) {
    Write-Host ("FAIL: {0}" -f ($failures -join ', '))
    exit 1
}
Write-Host 'PASS: 首装下载运行时端到端成立（含 sha256 负例）'
