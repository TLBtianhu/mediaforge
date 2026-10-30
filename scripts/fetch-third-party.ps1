<#
拉取并校验 CI / 打包所需的第三方二进制。

仓库不跟踪 ffmpeg / ffprobe / aria2c（在 .gitignore 里），公开仓库需要能自己拿到它们。
这个脚本按 .github/third-party.json 下载、解压、逐个校验 sha256 与大小，
校验失败直接抛错——宁可构建失败，也不要悄悄用了一个不同版本的 ffmpeg。

用法：
    pwsh scripts/fetch-third-party.ps1                   # 下载到 release/bin
    pwsh scripts/fetch-third-party.ps1 -VerifyOnly       # 只校验已有的文件
    pwsh scripts/fetch-third-party.ps1 -Destination X:\bin
#>
[CmdletBinding()]
param(
    [string]$Destination,
    [string]$ManifestPath,
    [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'

# 注意：不要把 $PSScriptRoot 写进 param 默认值——PowerShell 5.1 在用 -File 调用时
# 求值 param 默认值阶段它还不可用，会得到空路径。
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $Destination) { $Destination = Join-Path $repoRoot 'release\bin' }
if (-not $ManifestPath) { $ManifestPath = Join-Path $repoRoot '.github\third-party.json' }

function Get-Sha256([string]$Path) {
    (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-File([string]$Path, [long]$Size, [string]$Sha256, [string]$Label) {
    if (-not (Test-Path $Path)) {
        throw "$Label 不存在：$Path"
    }
    $actual = (Get-Item $Path).Length
    if ($actual -ne $Size) {
        throw "$Label 大小不符：期望 $Size，实际 $actual（$Path）"
    }
    $hash = Get-Sha256 $Path
    if ($hash -ne $Sha256) {
        throw "$Label 哈希不符：期望 $Sha256，实际 $hash（$Path）"
    }
    Write-Host "  OK  $Label  $Size bytes"
}

if (-not (Test-Path $ManifestPath)) { throw "找不到清单：$ManifestPath" }
$manifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json

New-Item -ItemType Directory -Force -Path $Destination | Out-Null

foreach ($name in $manifest.tools.PSObject.Properties.Name) {
    $tool = $manifest.tools.$name
    $needed = @()
    foreach ($file in $tool.files.PSObject.Properties.Name) {
        $target = Join-Path $Destination $file
        $spec = $tool.files.$file
        $ok = $false
        if (Test-Path $target) {
            try {
                Assert-File -Path $target -Size $spec.size -Sha256 $spec.sha256 -Label "$name/$file"
                $ok = $true
            } catch {
                Write-Host "  需要重新获取：$name/$file（$($_.Exception.Message)）"
            }
        }
        if (-not $ok) { $needed += $file }
    }

    if ($needed.Count -eq 0) { continue }
    if ($VerifyOnly) { throw "$name 缺失或校验失败：$($needed -join ', ')" }

    $temp = Join-Path ([System.IO.Path]::GetTempPath()) ("mf-thirdparty-" + [guid]::NewGuid().ToString('n'))
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    try {
        $archive = Join-Path $temp "download.zip"
        Write-Host "  下载 $name $($tool.version)：$($tool.url)"
        # 用系统 curl：Windows 10+ 自带，且不会因为 PowerShell 的进度条拖慢下载
        & curl.exe -sSL --fail --retry 3 --max-time 1800 -o $archive $tool.url
        if ($LASTEXITCODE -ne 0) { throw "下载失败（curl 退出码 $LASTEXITCODE）：$($tool.url)" }
        $size = (Get-Item $archive).Length
        if ($tool.archiveSize -and $size -ne $tool.archiveSize) {
            Write-Host "  提示：压缩包大小 $size 与清单记录 $($tool.archiveSize) 不同（上游可能重新打包），继续按解压后的文件校验"
        }

        Expand-Archive -Path $archive -DestinationPath $temp -Force
        foreach ($file in $needed) {
            $spec = $tool.files.$file
            $source = Join-Path $temp ($tool.extractPrefix + $file)
            if (-not (Test-Path $source)) {
                # 有些包的目录名带平台后缀变动，退化成按文件名搜索
                $found = Get-ChildItem -Path $temp -Recurse -Filter $file -File |
                    Where-Object { $_.FullName -notmatch '\\bin\\x64\\|\\bin\\x86\\' } |
                    Select-Object -First 1
                if (-not $found) { throw "压缩包里找不到 $file" }
                $source = $found.FullName
            }
            Copy-Item $source (Join-Path $Destination $file) -Force
            Assert-File -Path (Join-Path $Destination $file) -Size $spec.size -Sha256 $spec.sha256 -Label "$name/$file"
        }
    } finally {
        Remove-Item $temp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "第三方二进制就绪：$Destination"
