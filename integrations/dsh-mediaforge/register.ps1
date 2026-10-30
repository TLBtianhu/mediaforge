# 把 MediaForge 接入 DeepSeek Harness（幂等、可重复执行）。
#
#   powershell -ExecutionPolicy Bypass -File register.ps1
#   powershell ... -DshHome "D:\dsh-home" -DryRun        # 测试用
#
# 做法与仓库里已有的 QQ bridge 集成同构：在 profile 的 cordis.patch.yml 里维护
# 一段带 BEGIN/END 标记的区块，每次运行**整块替换**，不碰用户其它配置。
#
# 为什么不走 plugin_manager install_bundle：
#   那条路需要 pnpm 物化依赖，安装器里没有可靠的 pnpm；而 DSH 也没有可调用的
#   dsh CLI（PATH 里没有）。写一行 mcp-client 配置是最小、可审计、可回滚的方式。
param(
    [string]$DshHome = '',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$BEGIN = '# === mediaforge MCP BEGIN ==='
$END   = '# === mediaforge MCP END ==='
$NOTE  = '# 由 MediaForge 安装器维护；这段区块会被整体替换，请勿手工编辑内部条目。'

if (-not $DshHome) { $DshHome = Join-Path $env:USERPROFILE '.dsh' }

$launcher = Join-Path $env:USERPROFILE '.mediaforge\bin\mediaforge-mcp.cmd'

# 安装器只会调这个脚本，不会调 setup.ps1；启动器必须在这里补出来，
# 否则写进配置的是一个不存在的 .cmd，DSH 侧调用会直接失败。
$binDir = Split-Path -Parent $launcher
if (-not (Test-Path -LiteralPath $binDir)) {
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null
}
if (-not (Test-Path -LiteralPath $launcher)) {
    $template = Join-Path $PSScriptRoot 'launcher.template.cmd'
    if (Test-Path -LiteralPath $template) {
        Copy-Item -LiteralPath $template -Destination $launcher -Force
        Write-Host "launcher : $launcher (created)"
    } else {
        Write-Warning "launcher.template.cmd 缺失，无法生成启动器：$launcher"
    }
}
if (-not $DryRun -and -not (Test-Path -LiteralPath $launcher)) {
    Write-Host "尚未生成启动器：$launcher"
    Write-Host '请先运行 setup.ps1 -AppRoot "<安装目录>"，或让安装器完成安装。'
    exit 1
}

$profilesDir = Join-Path $DshHome 'profiles'
if (-not (Test-Path -LiteralPath $profilesDir)) {
    Write-Host "未找到 DSH profile 目录：$profilesDir（跳过，不影响应用本身）"
    exit 0
}

function Get-Block {
    param([string]$LauncherPath)
    $lines = @(
        $BEGIN
        $NOTE
        '- insert:'
        '    - id: mediaforge-mcp'
        "      name: '@deepseek-ai/dsh-mcp-client'"
        '      config:'
        '        serverName: mediaforge'
        '        transport: stdio'
        '        command: cmd'
        '        args:'
        '          - /c'
        "          - '%USERPROFILE%\.mediaforge\bin\mediaforge-mcp.cmd'"
        '        failOnStartupError: false'
        '        toolCallTimeoutMs: 120000'
        $END
    )
    return ($lines -join "`r`n")
}

$block = Get-Block -LauncherPath $launcher
$updated = @()
$skipped = @()

foreach ($profile in Get-ChildItem -LiteralPath $profilesDir -Directory -ErrorAction SilentlyContinue) {
    $patch = Join-Path $profile.FullName 'cordis.patch.yml'
    if (-not (Test-Path -LiteralPath $patch)) {
        $skipped += "$($profile.Name)（无 cordis.patch.yml）"
        continue
    }
    $original = Get-Content -LiteralPath $patch -Raw
    $normalized = $original -replace "`r`n", "`n"

    # 已经存在我们的区块：整块替换；否则追加到文件末尾。
    $pattern = "(?s)" + [regex]::Escape($BEGIN) + ".*?" + [regex]::Escape($END)
    if ($normalized -match $pattern) {
        $next = [regex]::Replace($normalized, $pattern, [System.Text.RegularExpressions.MatchEvaluator]{ param($m) $block -replace "`r`n", "`n" })
    } else {
        $next = $normalized.TrimEnd() + "`n`n" + ($block -replace "`r`n", "`n") + "`n"
    }
    if ($next -eq $normalized) {
        $skipped += "$($profile.Name)（已是最新）"
        continue
    }

    if ($DryRun) {
        Write-Host "--- 将写入 $patch ---"
        Write-Host (($block -split "`r`n" | Select-Object -First 6) -join "`n")
        Write-Host '...'
        continue
    }

    Copy-Item -LiteralPath $patch -Destination "$patch.bak-mediaforge-$(Get-Date -Format 'yyMMddHHmmss')" -Force
    [System.IO.File]::WriteAllText($patch, $next, (New-Object System.Text.UTF8Encoding($false)))
    $updated += $profile.Name
}

if ($DryRun) {
    Write-Host "（DryRun 结束，未写文件）"
    exit 0
}

Write-Host "已更新的 profile：$(if ($updated) { $updated -join ', ' } else { '无' })"
if ($skipped) { Write-Host "跳过：$($skipped -join '; ')" }
if ($updated) { Write-Host '重启 DeepSeek Harness 后生效（工具名形如 mcp__mediaforge__*）。' }
