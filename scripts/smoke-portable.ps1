# 便携版边界冒烟：解压到一个任意目录，确认它用 <exe>\data，且不碰安装版的数据根。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts/smoke-portable.ps1 `
#       -Zip release/MediaForge-0.0.27-windows-x64-portable.zip
#
# 旧版便携 zip 没有 mediaforge.portable 标记；脚本会先把标记补进 zip 的副本，
# 用来验证"标记 → 便携数据根"这条链路，而不依赖某个已发布的包。
param(
    [Parameter(Mandatory = $true)][string]$Zip,
    [int]$LaunchSeconds = 30
)

$ErrorActionPreference = 'Stop'
$failures = New-Object System.Collections.Generic.List[string]
function Check([string]$Name, [bool]$Ok, [string]$Detail = '') {
    if ($Ok) { Write-Host ("  [ok]   {0} {1}" -f $Name, $Detail) }
    else { Write-Host ("  [FAIL] {0} {1}" -f $Name, $Detail); $script:failures.Add("$Name $Detail") }
}

$source = (Resolve-Path $Zip).Path
$work = Join-Path $env:TEMP ('mediaforge-portable-smoke-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $work | Out-Null
$zip = Join-Path $work 'portable.zip'
Copy-Item $source $zip
$sevenZip = 'C:\Program Files\7-Zip\7z.exe'
if (-not (Test-Path $sevenZip)) { throw "需要 7-Zip：$sevenZip" }

Write-Host "便携版冒烟"
Write-Host "  source: $source"
Write-Host "  work:   $work"

# 1) 归档里没有标记就补一个（发布产物已经有；旧 zip 用来复现修复前的场景）
$listing = & $sevenZip l $zip 2>&1 | Out-String
if ($listing -notmatch 'mediaforge\.portable') {
    if ($zip -notmatch '\.zip$') { throw "归档缺少 mediaforge.portable，且不是 zip，无法补标记" }
    $markerDir = Join-Path $work 'marker'
    New-Item -ItemType Directory -Force -Path $markerDir | Out-Null
    Set-Content (Join-Path $markerDir 'mediaforge.portable') 'portable' -Encoding ASCII
    Push-Location $markerDir
    try {
        & $sevenZip a -tzip -bso0 -bsp0 $zip 'mediaforge.portable' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "添加标记失败：$LASTEXITCODE" }
    } finally { Pop-Location }
} else {
    Write-Host '  [ok]   归档已带标记'
}

# 2) 解压
$extract = Join-Path $work 'portable'
& $sevenZip x -o"$extract" -y $zip | Out-Null
if ($LASTEXITCODE -ne 0) { throw "解压失败：$LASTEXITCODE" }
# 发布用的 7z 里有一个顶层目录；测试用的 zip 可能是平铺的，两种都要能跑。
$exeItem = Get-ChildItem -Path $extract -Recurse -Filter 'MediaForge.exe' -File | Select-Object -First 1
Check '解压后有主程序' ($null -ne $exeItem)
if (-not $exeItem) { exit 1 }
$app = $exeItem.Directory.FullName
$exe = $exeItem.FullName
Check '标记随包' (Test-Path (Join-Path $app 'mediaforge.portable'))

# 3) 启动，确认数据落在程序旁的 data\，而不是用户的 .mediaforge。
# CI 首次启动可能超过 8 秒：在明确上限内等待条件，而不是用固定 sleep 猜测启动完成。
$installedSettings = Join-Path $env:USERPROFILE '.mediaforge\settings'
$installedBefore = if (Test-Path $installedSettings) { (Get-Item $installedSettings).LastWriteTimeUtc } else { $null }
$stdout = Join-Path $work 'portable.stdout.log'
$stderr = Join-Path $work 'portable.stderr.log'
$previousWebView = [Environment]::GetEnvironmentVariable('WEBVIEW2_USER_DATA_FOLDER')
$webviewRoot = Join-Path $work 'webview2'
$env:WEBVIEW2_USER_DATA_FOLDER = $webviewRoot
# 先用真实进程直接验证数据根，避免把 GUI/WebView2 启动速度误当成数据根错误。
$rootStdout = Join-Path $work 'portable-root.stdout.log'
$rootStderr = Join-Path $work 'portable-root.stderr.log'
$rootProcess = Start-Process -FilePath $exe -WorkingDirectory $app -ArgumentList '--smoke-print-data-root' -Wait -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $rootStdout -RedirectStandardError $rootStderr
$rootOutput = if (Test-Path $rootStdout) { Get-Content $rootStdout -Raw } else { '' }
$rootErrors = if (Test-Path $rootStderr) { Get-Content $rootStderr -Raw } else { '' }
$rootLine = @($rootOutput -split '\r?\n' | Where-Object { $_ -like 'SMOKE_DATA_ROOT=*' }) | Select-Object -Last 1
$actualRoot = if ($rootLine) { $rootLine.Substring('SMOKE_DATA_ROOT='.Length).Trim().TrimEnd('\') } else { '' }
$expectedRoot = (Join-Path $app 'data').TrimEnd('\')
if ($rootOutput) { Write-Host $rootOutput.TrimEnd() }
if ($rootErrors) { Write-Host $rootErrors.TrimEnd() }
Check '真实进程数据根初始化' ($rootProcess.ExitCode -eq 0) "exit=$($rootProcess.ExitCode)"
Check '便携形态被识别' ($rootOutput -match '(?m)^SMOKE_DEVELOPMENT=false\s*$')
Check '数据根在程序旁 data\' ($actualRoot -and $actualRoot.Equals($expectedRoot, [StringComparison]::OrdinalIgnoreCase)) $actualRoot
if ($failures.Count -gt 0) { exit 1 }

$process = Start-Process -FilePath $exe -WorkingDirectory $app -PassThru `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr
Start-Sleep -Seconds ([Math]::Min(3, $LaunchSeconds))
Check '应用启动后保持运行' (-not $process.HasExited)
if ($failures.Count -gt 0) {
    foreach ($log in @($stdout, $stderr)) {
        if (-not (Test-Path $log)) { continue }
        $content = Get-Content $log -Raw
        if ($null -ne $content -and $content.Trim()) { Write-Host "  $(Split-Path -Leaf $log):`n$($content.Trim())" }
    }
}
if (Test-Path $installedSettings) {
    $installedAfter = (Get-Item $installedSettings).LastWriteTimeUtc
    Check '安装版数据根未被写入' ($installedBefore -eq $installedAfter)
} else {
    Write-Host '  [skip] 本机没有安装版数据根，跳过"未被写入"检查'
}
if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
# WebView2 子进程可能在主进程退出后仍持有 profile 文件；只处理这次临时 profile。
$webviewChildren = @(Get-CimInstance Win32_Process -Filter "Name = 'msedgewebview2.exe'" | Where-Object {
    $_.CommandLine -and $_.CommandLine.Contains($webviewRoot)
})
foreach ($child in $webviewChildren) { Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue }
$deadline = (Get-Date).AddSeconds(10)
do {
    Start-Sleep -Milliseconds 500
    $webviewChildren = @(Get-CimInstance Win32_Process -Filter "Name = 'msedgewebview2.exe'" | Where-Object {
        $_.CommandLine -and $_.CommandLine.Contains($webviewRoot)
    })
} while ($webviewChildren.Count -gt 0 -and (Get-Date) -lt $deadline)
if ($webviewChildren.Count -gt 0) { throw 'WebView2 child processes did not exit within 10 seconds' }
if ($null -eq $previousWebView) { Remove-Item Env:WEBVIEW2_USER_DATA_FOLDER -ErrorAction SilentlyContinue }
else { Set-Item Env:WEBVIEW2_USER_DATA_FOLDER $previousWebView }

Write-Host ''
if ($failures.Count -gt 0) {
    Write-Host ("FAILED: {0} 项；现场保留在 {1}" -f $failures.Count, $work)
    $failures | ForEach-Object { Write-Host ("  - {0}" -f $_) }
    exit 1
}
. (Join-Path $PSScriptRoot 'temp-work-guard.ps1')
$resolvedWork = Assert-TempWorkDir -Work $work -Prefix 'mediaforge-portable-smoke-'
Remove-Item -LiteralPath $resolvedWork -Recurse -Force
Write-Host 'PASS: 便携版使用程序旁数据根'
exit 0
