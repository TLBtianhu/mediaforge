# MediaForge ↔ DSH 集成自检。
#
#   powershell -ExecutionPolicy Bypass -File verify.ps1
#
# 依次检查：启动器存在 → 能找到应用 → bridge 能起 MCP 握手 → 报出工具数量。
# 任何一步失败都会打印它试过的位置，方便直接定位，而不是只看到"没有工具"。
$ErrorActionPreference = 'Stop'

$launcher = Join-Path $env:LOCALAPPDATA 'MediaForge\bin\mediaforge-mcp.cmd'
$homeFile = Join-Path $env:LOCALAPPDATA 'MediaForge\home.txt'

Write-Host '== 1. 启动器 =='
if (-not (Test-Path -LiteralPath $launcher)) {
    Write-Host "  MISSING: $launcher"
    Write-Host '  修：powershell -ExecutionPolicy Bypass -File setup.ps1 -AppRoot "C:\Program Files\MediaForge"'
    exit 1
}
Write-Host "  OK: $launcher"

Write-Host '== 2. 路径解析 =='
if ($env:MEDIAFORGE_HOME) { Write-Host "  MEDIAFORGE_HOME = $env:MEDIAFORGE_HOME" }
if (Test-Path -LiteralPath $homeFile) {
    $pointer = (Get-Content -LiteralPath $homeFile -TotalCount 1).Trim()
    Write-Host "  home.txt = $pointer   (bridge exists: $(Test-Path -LiteralPath (Join-Path $pointer 'python\agent_bridge.py')))"
} else {
    Write-Host '  home.txt 不存在'
}
$reg = Get-ItemProperty -Path 'HKCU:\Software\MediaForge' -Name InstallDir -ErrorAction SilentlyContinue
Write-Host "  registry InstallDir = $(if ($reg) { $reg.InstallDir } else { '(未设置)' })"
Write-Host "  Program Files 兜底 = $(Test-Path -LiteralPath (Join-Path $env:ProgramFiles 'MediaForge\python\agent_bridge.py'))"

Write-Host '== 3. MCP 握手 =='
# 用临时文件喂 stdin：PowerShell 5.1 的管道会给第一行加 BOM，bridge 会拒绝
$requests = @(
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}'
    '{"jsonrpc":"2.0","method":"notifications/initialized"}'
    '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
)
$requestFile = Join-Path $env:TEMP ("mediaforge-verify-" + [guid]::NewGuid().ToString('N') + ".jsonl")
$outputFile = Join-Path $env:TEMP ("mediaforge-verify-" + [guid]::NewGuid().ToString('N') + ".out")
[System.IO.File]::WriteAllLines($requestFile, $requests, (New-Object System.Text.UTF8Encoding($false)))
try {
    $proc = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', $launcher) `
        -RedirectStandardInput $requestFile -RedirectStandardOutput $outputFile `
        -RedirectStandardError ($outputFile + '.err') -NoNewWindow -PassThru -Wait
    $output = @(Get-Content -LiteralPath $outputFile -ErrorAction SilentlyContinue)
    $errors = @(Get-Content -LiteralPath ($outputFile + '.err') -ErrorAction SilentlyContinue)
} finally {
    Remove-Item -LiteralPath $requestFile -Force -ErrorAction SilentlyContinue
}
$json = @($output | Where-Object { $_ -like '{"jsonrpc"*' })
if ($proc.ExitCode -ne 0 -or -not $json) {
    Write-Host "  bridge 退出码 $($proc.ExitCode)；没有 JSON-RPC 响应"
    ($output + $errors) | Select-Object -Last 8 | ForEach-Object { "  | $_" }
    exit 1
}
$initialize = $json | Where-Object { $_ -match '"serverInfo"' } | Select-Object -First 1
$toolsLine = $json | Where-Object { $_ -match '"id"\s*:\s*2' } | Select-Object -First 1
$toolCount = 0
if ($toolsLine) {
    try { $toolCount = @((ConvertFrom-Json -InputObject $toolsLine).result.tools).Count } catch { $toolCount = 0 }
}
if ($initialize) {
    try { Write-Host "  serverInfo.name = $((ConvertFrom-Json -InputObject $initialize).result.serverInfo.name)" } catch { }
}
Write-Host "  响应行数 = $($json.Count)   tools/list 条目 = $toolCount"
if ($initialize -match '"error"') { Write-Host '  initialize 报错：'; Write-Host ("  | " + $initialize.Substring(0, [Math]::Min(200, $initialize.Length))) }
Remove-Item -LiteralPath $outputFile, ($outputFile + '.err') -Force -ErrorAction SilentlyContinue
Write-Host '== 结果 =='
if ($initialize -match '"serverInfo"' -and $toolCount -gt 0) {
    Write-Host "  PASS：bridge 可用，$toolCount 个工具"
} else {
    Write-Host '  FAIL：握手或工具列表不完整'
    exit 1
}
