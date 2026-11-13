# MediaForge DSH 集成的迁移工具（不再向 cordis.patch.yml 注入配置）。
#
# 旧安装器会在此维护一段 BEGIN/END 区块，整块替换。问题是 DSH 设置页把新行追加到
# 文件末尾，于是设置会落进区块内部，下次替换就把用户配置删掉。现在改为 bundle 安装：
#
#   plugin_manager install_bundle  target=link:<安装目录>\integrations\dsh-mediaforge
#
# 本脚本只保留两个职责：
#   1. 报告遗留的注入区块（以及区块里不属于 MediaForge 的行）；
#   2. 用 -Profile <name> -RemoveLegacyBlock 安全移除该区块，保留区块内的外来行。
#
# 不再写 cordis.patch.yml（除非显式 -RemoveLegacyBlock -Profile），也不会创建/覆盖用户设置。
# 写入只针对显式指定的一个 profile；读取/报告可以扫描全部 profile。
param(
    [string]$DshHome = '',
    [string]$Profile = '',
    [switch]$RemoveLegacyBlock,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$BEGIN = '# === mediaforge MCP BEGIN ==='
$END   = '# === mediaforge MCP END ==='

if (-not $DshHome) { $DshHome = Join-Path $env:USERPROFILE '.dsh' }

$profilesDir = Join-Path $DshHome 'profiles'
if (-not (Test-Path -LiteralPath $profilesDir)) {
    Write-Host "未找到 DSH profile 目录：$profilesDir（跳过，不影响应用本身）"
    exit 0
}

if ($RemoveLegacyBlock -and -not $Profile) {
    throw "指定 -Profile <name> 后再使用 -RemoveLegacyBlock；本工具不再遍历所有 profile 写入。"
}
if ($Profile) {
    if ($Profile -in @('.', '..') -or $Profile -match '[\\/]') { throw "invalid profile name: $Profile" }
    $target = Join-Path $profilesDir $Profile
    if (-not (Test-Path -LiteralPath $target -PathType Container)) { throw "profile not found: $target" }
    $profiles = @(Get-Item -LiteralPath $target)
} else {
    # 只读报告允许扫描全部 profile；写入路径已要求显式 -Profile。
    $profiles = @(Get-ChildItem -LiteralPath $profilesDir -Directory -ErrorAction SilentlyContinue)
}

function Get-PreservedRows {
    param([string[]]$BlockLines)
    # 保留区块内的顶层 `- id:` 组（外来配置），丢掉 MediaForge 自己的 insert 与禁用覆盖。
    $rows = New-Object System.Collections.Generic.List[string]
    $current = $null
    foreach ($line in $BlockLines) {
        if ($line -match '^- id:\s*(\S+)') {
            if ($current -and $current.Id -ne 'mediaforge-mcp') { $rows.AddRange([string[]]$current.Lines) }
            $current = @{ Id = $Matches[1]; Lines = @($line) }
            continue
        }
        if ($current) { $current.Lines += $line }
    }
    if ($current -and $current.Id -ne 'mediaforge-mcp') { $rows.AddRange([string[]]$current.Lines) }
    $filtered = @($rows | Where-Object { $_.Trim() -ne $END -and $_.Trim() -ne $BEGIN })
    while ($filtered.Count -and [string]::IsNullOrWhiteSpace($filtered[$filtered.Count - 1])) {
        $filtered = @($filtered[0..($filtered.Count - 2)])
    }
    return $filtered
}

$changed = @()
foreach ($profile in $profiles) {
    $patch = Join-Path $profile.FullName 'cordis.patch.yml'
    if (-not (Test-Path -LiteralPath $patch)) { continue }
    $original = Get-Content -LiteralPath $patch -Raw
    $normalized = $original -replace "`r`n", "`n"
    $match = [regex]::Match($normalized, "(?s)" + [regex]::Escape($BEGIN) + ".*?" + [regex]::Escape($END))
    if (-not $match.Success) { continue }

    $startLine = ($normalized.Substring(0, $match.Index) -split "`n").Count
    $endLine = $startLine + (($match.Value -split "`n").Count - 1)
    $preserved = Get-PreservedRows -BlockLines ($match.Value -split "`n")

    Write-Host "[$($profile.Name)] 发现遗留注入区块（第 $startLine-$endLine 行）"
    if ($preserved.Count) {
        Write-Host "  区块内还有 $($preserved.Count) 行外来配置，将原样保留。"
    }
    if (-not $RemoveLegacyBlock) {
        Write-Host "  本脚本不再写入。推荐迁移："
        Write-Host "    plugin_manager install_bundle target=link:$PSScriptRoot"
        Write-Host "  或只移除旧区块：register.ps1 -Profile $($profile.Name) -RemoveLegacyBlock"
        continue
    }
    if ($DryRun) {
        Write-Host "  DryRun：将移除区块并保留上面列出的外来行。"
        continue
    }

    $before = $normalized.Substring(0, $match.Index).TrimEnd()
    $after = $normalized.Substring($match.Index + $match.Length).TrimStart()
    $parts = @()
    if ($before) { $parts += $before }
    if ($preserved.Count) { $parts += ($preserved -join "`n") }
    if ($after) { $parts += $after.TrimEnd() }
    $next = ($parts -join "`n`n") + "`n"

    Copy-Item -LiteralPath $patch -Destination "$patch.bak-mediaforge-$(Get-Date -Format 'yyMMddHHmmss')" -Force
    [System.IO.File]::WriteAllText($patch, $next, (New-Object System.Text.UTF8Encoding($false)))
    $changed += $profile.Name

    # 只保留最近 5 份备份，避免 profile 目录越堆越多。
    $backups = @(Get-ChildItem -LiteralPath $profile.FullName -Filter 'cordis.patch.yml.bak-mediaforge-*' | Sort-Object LastWriteTime -Descending)
    if ($backups.Count -gt 5) { $backups | Select-Object -Skip 5 | Remove-Item -Force }
}

if ($changed.Count) {
    Write-Host "已移除遗留区块的 profile：$($changed -join ', ')"
    Write-Host "请用插件管理器安装 bundle（若尚未安装），并重启/重连 DSH。"
} else {
    Write-Host "没有需要移除的遗留区块。bundle 安装：plugin_manager install_bundle target=link:$PSScriptRoot"
}
