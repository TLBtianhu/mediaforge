# 把 MediaForge 的 MCP 启动器写到固定位置，并登记安装路径指针。
#
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -AppRoot "C:\Program Files\MediaForge"
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -AppRoot "D:\portable\MediaForge"
#
# 为什么要有这一步：DSH 插件包（cordis.patch.yml）里不能写机器相关的绝对路径，
# 否则发出去谁都用不了。启动器放在 %LOCALAPPDATA%\MediaForge\bin\，由 cmd 展开；
# 它自己按 MEDIAFORGE_HOME → home.txt → 注册表 → Program Files 的顺序找应用。
#
# 安装包会在安装结束时自动做同样三件事；这个脚本覆盖便携版、开发树，
# 以及"安装包没写指针"的情况。
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

$stateDir = Join-Path $env:LOCALAPPDATA 'MediaForge'
$binDir = Join-Path $stateDir 'bin'
New-Item -ItemType Directory -Force -Path $binDir | Out-Null

# 1) 启动器（模板里没有绝对路径，直接复制即可）
$launcher = Join-Path $binDir 'mediaforge-mcp.cmd'
Copy-Item -LiteralPath $template -Destination $launcher -Force

# 2) 指针文件：一行路径，启动器优先读它
$homeFile = Join-Path $stateDir 'home.txt'
[System.IO.File]::WriteAllText($homeFile, $root + "`r`n", (New-Object System.Text.UTF8Encoding($false)))

# 3) 注册表兜底（用户级，不需要管理员）
New-Item -Path 'HKCU:\Software\MediaForge' -Force | Out-Null
New-ItemProperty -Path 'HKCU:\Software\MediaForge' -Name 'InstallDir' -Value $root -PropertyType String -Force | Out-Null

Write-Host "launcher : $launcher"
Write-Host "home.txt : $homeFile  -> $root"
Write-Host "registry : HKCU\Software\MediaForge\InstallDir = $root"
Write-Host ""
Write-Host "next: install this bundle in DSH (plugin_manager install_bundle -> $PSScriptRoot)"
Write-Host "check: powershell -ExecutionPolicy Bypass -File verify.ps1"
