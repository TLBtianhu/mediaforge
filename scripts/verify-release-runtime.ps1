#!/usr/bin/env pwsh
<#
.SYNOPSIS
  验证一个"发布形态"的 MediaForge 目录能不能真的跑起来。

.DESCRIPTION
  检查的是**用户拿到的东西**，不是开发树。默认按安装包的布局检查：

      <Root>\Pymss Studio.exe
      <Root>\python\worker.py
      <Root>\python-runtime\runtime-envs\<backend>\Scripts\python.exe
      <Root>\bin\ffmpeg.exe  ffprobe.exe  aria2c.exe

  每一项独立 PASS/FAIL，最后一张表 + 退出码，方便在干净账户 / CI 里一条命令跑完。

  其中第 4 项（自带工具在**没有系统 PATH** 时能否找到）不是洁癖——
  2026-10-29 的真事故：安装包把 ffmpeg 放在 `<install>\bin\`，而 `ffmpeg.py`
  只找 `<root>\tools\...`，从不看 `bin\`；开发机 PATH 上有系统 ffmpeg 所以本地正常，
  干净机器上直接 `ffmpeg not found`，视频抽帧/回封/探测全废。

.EXAMPLE
  # 验证解压出来的发布包（推荐：在干净用户账户里跑）
  .\verify-release-runtime.ps1 -Root D:\stage\Pymss-Studio-0.0.13-windows-x64-cuda `
      -ModelDir C:\Models -Sample C:\samples\cat.png

.EXAMPLE
  # 只验证开发树（不跑真任务）
  .\verify-release-runtime.ps1 -Python .\.dev-data\runtime-envs\cuda\Scripts\python.exe -SkipJob
#>
[CmdletBinding()]
param(
    [string]$Root,
    [string]$Python,
    [string]$ModelDir,
    [string]$Sample,
    [string]$WorkDir,
    [switch]$SkipJob,
    [switch]$KeepSystemPath
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$results = New-Object System.Collections.ArrayList

# 入参可能是相对路径（相对调用者的当前目录），而脚本中途会 Push-Location，
# 所以先统一转成绝对路径，免得"找不到解释器"这种假失败。
foreach ($name in @('Root', 'Python', 'ModelDir', 'Sample', 'WorkDir')) {
    $value = Get-Variable -Name $name -ValueOnly -ErrorAction SilentlyContinue
    if ($value) {
        $resolved = Resolve-Path -LiteralPath $value -ErrorAction SilentlyContinue
        if ($resolved) { Set-Variable -Name $name -Value $resolved.Path }
    }
}

function Add-Result {
    param([string]$Name, [string]$State, [string]$Detail)
    [void]$results.Add([pscustomobject]@{ Check = $Name; Result = $State; Detail = $Detail })
    $color = 'Gray'
    if ($State -eq 'PASS') { $color = 'Green' } elseif ($State -eq 'FAIL') { $color = 'Red' } elseif ($State -eq 'WARN') { $color = 'Yellow' }
    Write-Host ('  [{0}] {1} — {2}' -f $State, $Name, $Detail) -ForegroundColor $color
}

function Invoke-Captured {
    param([string]$Exe, [string[]]$Arguments, [string]$WorkingDirectory, [hashtable]$EnvOverrides)
    $saved = @{}
    if ($EnvOverrides) {
        foreach ($key in $EnvOverrides.Keys) {
            $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
            [Environment]::SetEnvironmentVariable($key, $EnvOverrides[$key], 'Process')
        }
    }
    # 原生命令往 stderr 写东西是常态（Python 的 traceback、ffmpeg 的 banner），
    # 不能因为脚本开了 Stop 就被当成终止错误——我们靠 ExitCode 判断成败。
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    Push-Location -LiteralPath $WorkingDirectory
    try {
        $output = & $Exe @Arguments 2>&1 | ForEach-Object { "$_" }
        return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = @($output) }
    }
    finally {
        Pop-Location
        $ErrorActionPreference = $previous
        if ($EnvOverrides) {
            foreach ($key in $EnvOverrides.Keys) {
                [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process')
            }
        }
    }
}

function Write-Utf8NoBom {
    # PowerShell 5.1 的 Set-Content -Encoding UTF8 会写 BOM，而 json.loads 见 BOM 直接抛。
    # 我们自己写 payload 时就不该带——顺便也别把这锅甩给 worker 的读端。
    param([string]$Path, [string]$Text)
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Text, $encoding)
}

function Get-LastJsonEvent {
    param([string[]]$Lines, [string]$Type)
    for ($index = $Lines.Count - 1; $index -ge 0; $index--) {
        $line = $Lines[$index].Trim()
        if (-not $line.StartsWith('{')) { continue }
        try { $event = $line | ConvertFrom-Json } catch { continue }
        if (-not $Type -or $event.type -eq $Type) { return $event }
    }
    return $null
}

# ── 0. 定位根目录 ───────────────────────────────────────────────────────────
if (-not $Root) {
    $candidate = Join-Path $repoRoot 'src-tauri\target\release'
    if (Test-Path $candidate) { $Root = $candidate }
}
if (-not $Root -or -not (Test-Path $Root)) {
    Write-Host '找不到要验证的目录。用 -Root 指向解压后的发布目录。' -ForegroundColor Red
    exit 2
}
$Root = (Resolve-Path $Root).Path
# 发布形态的标志是 python-runtime/；没有它就是开发树（target\release），
# 缺发布目录属于正常，不该报 FAIL——否则这个脚本在开发机上天天红。
$packaged = Test-Path (Join-Path $Root 'python-runtime')
Write-Host ''
Write-Host ("验证目标：$Root" + $(if ($packaged) { '（发布形态）' } else { '（开发树）' })) -ForegroundColor Cyan
Write-Host ''

# ── 1. 目录形态 ─────────────────────────────────────────────────────────────
$exeNames = @('Pymss Studio.exe', 'pymss-studio.exe', 'MediaForge.exe')
$exe = $null
foreach ($name in $exeNames) {
    $path = Join-Path $Root $name
    if (Test-Path $path) { $exe = $path; break }
}
if ($exe) { Add-Result '应用程序 exe' 'PASS' (Split-Path $exe -Leaf) }
else {
    if ($packaged) { Add-Result '应用程序 exe' 'FAIL' ('期望以下之一：' + ($exeNames -join ' / ')) }
    else { Add-Result '应用程序 exe' 'WARN' '开发树里没有发布 exe（正常）' }
}

$workerPath = Join-Path $Root 'python\worker.py'
$workerNote = 'python\worker.py'
if (-not (Test-Path $workerPath) -and -not $packaged) {
    # 开发树：exe 在 target\release，但 worker 代码在仓库根的 python\
    $devWorker = Join-Path $repoRoot 'python\worker.py'
    if (Test-Path $devWorker) { $workerPath = $devWorker; $workerNote = $devWorker }
}
if (Test-Path $workerPath) { Add-Result 'worker 代码' 'PASS' $workerNote }
else { Add-Result 'worker 代码' 'FAIL' '缺少 python\worker.py' }
$workerDir = Split-Path -Parent $workerPath

# ── 2. 解释器 ───────────────────────────────────────────────────────────────
$interpreter = $Python
if (-not $interpreter) {
    $statePath = Join-Path $Root 'python-runtime\runtime-envs\active-runtime.json'
    if (Test-Path $statePath) {
        $state = Get-Content $statePath -Raw | ConvertFrom-Json
        $declared = [string]$state.pythonPath
        if ([System.IO.Path]::IsPathRooted($declared)) { $interpreter = $declared }
        else { $interpreter = Join-Path (Join-Path $Root 'python-runtime\runtime-envs') $declared }
    }
}
if ($interpreter -and (Test-Path $interpreter)) {
    $version = Invoke-Captured -Exe $interpreter -Arguments @('-c', 'import sys; print(sys.version.split()[0])') -WorkingDirectory $Root
    if ($version.ExitCode -eq 0) { Add-Result 'Python 解释器' 'PASS' ("$interpreter -> " + ($version.Output -join ' ')) }
    else { Add-Result 'Python 解释器' 'FAIL' ("无法执行：$interpreter") }
}
elseif ($interpreter) { Add-Result 'Python 解释器' 'FAIL' "路径不存在：$interpreter" }
else { Add-Result 'Python 解释器' 'WARN' '没找到 python-runtime（开发树正常）；用 -Python 指定解释器' }

# ── 3. 自带工具是否齐 ───────────────────────────────────────────────────────
$binDir = Join-Path $Root 'bin'
foreach ($tool in @('ffmpeg.exe', 'ffprobe.exe')) {
    $path = Join-Path $binDir $tool
    if (Test-Path $path) { Add-Result "自带工具 $tool" 'PASS' $path }
    elseif ($packaged) { Add-Result "自带工具 $tool" 'FAIL' "缺少 $path（安装包应随附）" }
    else { Add-Result "自带工具 $tool" 'WARN' '开发树没有 bin\（正常；发布包必须有）' }
}

if (-not $interpreter -or -not (Test-Path $interpreter)) {
    Add-Result '工具定位（干净 PATH）' 'WARN' '没有可用解释器，跳过'
}
else {
    # ── 4. 自带工具在**没有系统 PATH** 时能否被找到 ─────────────────────────
    $probe = @'
import sys
sys.path.insert(0, sys.argv[1])
from media_ops import ffmpeg
print(ffmpeg._find_tool('ffmpeg'))
'@
    $probePath = Join-Path ([System.IO.Path]::GetTempPath()) ("mf-verify-" + [guid]::NewGuid().ToString('N').Substring(0, 8) + ".py")
    Set-Content -Path $probePath -Value $probe -Encoding UTF8
    try {
        $envOverride = @{}
        if (-not $KeepSystemPath) { $envOverride['PATH'] = "$env:SystemRoot\System32;$env:SystemRoot" }
        $found = Invoke-Captured -Exe $interpreter -Arguments @($probePath, $workerDir) -WorkingDirectory $Root -EnvOverrides $envOverride
        $location = ($found.Output -join ' ').Trim()
        if ($found.ExitCode -eq 0 -and $location) {
            $expected = Join-Path $binDir 'ffmpeg.exe'
            if ($KeepSystemPath -or $location -like "$binDir*") {
                Add-Result '工具定位（干净 PATH）' 'PASS' $location
            }
            else {
                Add-Result '工具定位（干净 PATH）' 'FAIL' ("找到的是 $location，不是自带的 $expected —— PATH 上没有 ffmpeg 的机器会失败")
            }
        }
        else { Add-Result '工具定位（干净 PATH）' 'FAIL' ("找不到 ffmpeg：$location") }
    }
    finally { Remove-Item $probePath -Force -ErrorAction SilentlyContinue }

    # ── 5. worker env_info ──────────────────────────────────────────────────
    $envInfo = Invoke-Captured -Exe $interpreter -Arguments @($workerPath, 'env_info') -WorkingDirectory $workerDir
    $event = Get-LastJsonEvent -Lines $envInfo.Output -Type 'env_info'
    if ($event) {
        $payload = $event.payload
        $torch = 'none'
        if ($payload.torchAvailable) { $torch = "$($payload.torchVersion) / $($payload.torchBackend)" }
        Add-Result 'worker env_info' 'PASS' ("python $($payload.pythonVersion) · torch $torch · pymss $($payload.pymssVersion)")
        if ($payload.torchAvailable -and -not $payload.cudaAvailable) {
            Add-Result 'CUDA 可用性' 'WARN' 'torch 在，但 CUDA 不可用（CPU 版安装包属正常）'
        }
        elseif ($payload.cudaAvailable) {
            Add-Result 'CUDA 可用性' 'PASS' ("$($payload.cudaDeviceCount) 个设备：" + (($payload.cudaDevices | ForEach-Object { $_.name }) -join '; '))
        }
        else { Add-Result 'CUDA 可用性' 'WARN' 'torch 不可用' }
    }
    else { Add-Result 'worker env_info' 'FAIL' ('没拿到 env_info 事件：' + (($envInfo.Output | Select-Object -Last 3) -join ' | ')) }

    # ── 6. 模型目录 ─────────────────────────────────────────────────────────
    if ($ModelDir) {
        $modelsPayloadPath = Join-Path ([System.IO.Path]::GetTempPath()) ("mf-verify-models-" + [guid]::NewGuid().ToString('N').Substring(0, 8) + ".json")
        Write-Utf8NoBom -Path $modelsPayloadPath -Text (@{ modelDir = $ModelDir } | ConvertTo-Json -Compress)
        try {
            $list = Invoke-Captured -Exe $interpreter -Arguments @($workerPath, 'list_models', '--payload', $modelsPayloadPath) -WorkingDirectory $workerDir
        }
        finally { Remove-Item $modelsPayloadPath -Force -ErrorAction SilentlyContinue }
        $catalog = Get-LastJsonEvent -Lines $list.Output -Type 'models'
        if ($catalog -and $catalog.payload) {
            Add-Result '模型目录' 'PASS' ("$($catalog.payload.count) 条模型 @ $($catalog.payload.modelDir)")
        }
        else { Add-Result '模型目录' 'WARN' 'list_models 没返回可解析的事件' }
    }
    else { Add-Result '模型目录' 'WARN' '未指定 -ModelDir' }

    # ── 7. 真任务（可选）────────────────────────────────────────────────────
    if ($SkipJob -or -not $Sample -or -not $ModelDir) {
        Add-Result '真任务' 'WARN' '跳过（需要 -Sample 与 -ModelDir）'
    }
    else {
        $work = $WorkDir
        if (-not $work) { $work = Join-Path ([System.IO.Path]::GetTempPath()) ("mf-verify-job-" + [guid]::NewGuid().ToString('N').Substring(0, 8)) }
        New-Item -ItemType Directory -Path $work -Force | Out-Null
        $outDir = Join-Path $work 'out'
        $payloadPath = Join-Path $work 'payload.json'
        $payload = [ordered]@{
            operation = 'media_image'
            requestId = 'verify_runtime'
            inputs    = @((Resolve-Path $Sample).Path)
            outputDir = $outDir
            modelDir  = $ModelDir
            steps     = @(@{ capability = 'upscale'; bindings = @{ upscaler = 'media:real-esrgan-x2' }; params = @{} })
        }
        Write-Utf8NoBom -Path $payloadPath -Text ($payload | ConvertTo-Json -Depth 8)
        $job = Invoke-Captured -Exe $interpreter -Arguments @($workerPath, 'audio_tools', '--payload', $payloadPath) -WorkingDirectory $workerDir
        $resultEvent = Get-LastJsonEvent -Lines $job.Output -Type 'audio_tool_result'
        $errorEvent = Get-LastJsonEvent -Lines $job.Output -Type 'error'
        if ($resultEvent) {
            $produced = @($resultEvent.payload.outputs)
            if ($produced.Count -gt 0 -and (Test-Path $produced[0].path)) {
                Add-Result '真任务' 'PASS' (Split-Path $produced[0].path -Leaf)
            }
            else { Add-Result '真任务' 'FAIL' '结果事件里没有落地产物' }
        }
        elseif ($errorEvent) { Add-Result '真任务' 'FAIL' ("$($errorEvent.payload.code): $($errorEvent.payload.message)") }
        else { Add-Result '真任务' 'FAIL' ('worker 退出码 ' + $job.ExitCode + '，未产出结果事件') }
    }
}

# ── 汇总 ────────────────────────────────────────────────────────────────────
Write-Host ''
$results | Format-Table -AutoSize | Out-String -Width 200 | Write-Host
$failed = @($results | Where-Object { $_.Result -eq 'FAIL' }).Count
$warned = @($results | Where-Object { $_.Result -eq 'WARN' }).Count
if ($failed -gt 0) {
    Write-Host "$failed 项失败，这个包不能发。" -ForegroundColor Red
    exit 1
}
if ($warned -gt 0) { Write-Host "$warned 项警告（多为开发树缺发布目录，属正常）。" -ForegroundColor Yellow }
else { Write-Host '全部通过。' -ForegroundColor Green }
exit 0


