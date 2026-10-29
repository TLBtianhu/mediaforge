# 生成 MediaForge 的 MCP 启动器到固定位置，供 DSH 插件包引用。
#
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -AppRoot "C:\Program Files\MediaForge"
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -AppRoot "D:\portable\MediaForge"
#
# 为什么要有这一步：插件包（cordis.patch.yml）里不能写机器相关的绝对路径，
# 否则发出去谁都用不了。启动器放在 `%LOCALAPPDATA%\MediaForge\bin\`，由 cmd 展开。
param(
    [Parameter(Mandatory = $true)][string]$AppRoot
)

$ErrorActionPreference = 'Stop'

$root = (Resolve-Path -LiteralPath $AppRoot).Path
$bridge = Join-Path $root 'python\agent_bridge.py'
if (-not (Test-Path -LiteralPath $bridge)) {
    throw "app root looks wrong: $bridge not found"
}

$template = Join-Path $PSScriptRoot 'launcher.template.cmd'
if (-not (Test-Path -LiteralPath $template)) {
    throw "launcher.template.cmd is missing next to this script"
}

$targetDir = Join-Path $env:LOCALAPPDATA 'MediaForge\bin'
New-Item -ItemType Directory -Force -Path $targetDir | Out-Null

$content = Get-Content -LiteralPath $template -Raw
$content = $content.Replace('__MEDIAFORGE_ROOT__', $root)
$target = Join-Path $targetDir 'mediaforge-mcp.cmd'
# 用无 BOM 的 UTF-8 写：cmd 遇到 BOM 会把第一行当命令执行
[System.IO.File]::WriteAllText($target, $content, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "launcher written: $target"
Write-Host "app root        : $root"
Write-Host ""
Write-Host "next: install this bundle in DSH (plugin_manager install_bundle -> $PSScriptRoot)"
