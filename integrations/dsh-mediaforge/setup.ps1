# 把 MediaForge 的 MCP 启动器写到固定位置，并登记安装路径指针。
#
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -AppRoot "C:\Path\To\MediaForge"
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -AppRoot "D:\portable\MediaForge"
#
# 为什么要有这一步：DSH 插件包（cordis.patch.yml）里不能写机器相关的绝对路径，
# 否则发出去谁都用不了。启动器固定放在 %USERPROFILE%\.mediaforge\bin\，
# 和其它由程序生成的状态放在同一个数据根里（卸载不会删掉它，重装后仍然可用）。
# 启动器自己按 MEDIAFORGE_HOME → 注册表 → %LOCALAPPDATA%\Programs → Program Files
# 的顺序找应用。
#
# 安装包会在安装结束时调用 register.ps1 做同样的事；这个脚本覆盖便携版、开发树，
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

# 1) 启动器：数据根下的固定位置（模板里没有绝对路径，直接复制即可）
$binDir = Join-Path $env:USERPROFILE '.mediaforge\bin'
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$launcher = Join-Path $binDir 'mediaforge-mcp.cmd'
Copy-Item -LiteralPath $template -Destination $launcher -Force

# 2) 安装路径指针：用户级注册表，不需要管理员
New-Item -Path 'HKCU:\Software\MediaForge' -Force | Out-Null
New-ItemProperty -Path 'HKCU:\Software\MediaForge' -Name 'InstallDir' -Value $root -PropertyType String -Force | Out-Null

# 3) 一个人类可读的指针文件，排障时不用去翻注册表
[System.IO.File]::WriteAllText(
    (Join-Path $env:USERPROFILE '.mediaforge\home.txt'),
    $root,
    (New-Object System.Text.UTF8Encoding($false))
)

Write-Host "launcher : $launcher"
Write-Host "registry : HKCU\Software\MediaForge\InstallDir = $root"
Write-Host "home.txt : $env:USERPROFILE\.mediaforge\home.txt"
Write-Host ""
Write-Host "next: install this bundle in DSH (plugin_manager install_bundle -> $PSScriptRoot)"
Write-Host "check: powershell -ExecutionPolicy Bypass -File verify.ps1"
