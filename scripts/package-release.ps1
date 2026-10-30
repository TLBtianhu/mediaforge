# 唯一的打包入口。任何"临时脚本"都应该被删掉，只用这一个。
#
#   powershell -ExecutionPolicy Bypass -File scripts/package-release.ps1
#   powershell ... -SkipBuild     # 复用已有二进制，只重打包
#
# 步骤与产物：
#   1. pnpm tauri build --no-bundle        → target\release\pymss-studio.exe（生产上下文）
#   2. staging                              → release\stage-<ver>\
#   3. portable zip                         → release\MediaForge-<ver>-windows-x64-portable.zip
#   4. Inno Setup                           → release\MediaForge-<ver>-windows-x64-cuda-setup.exe
#   5. 复制安装包到 Downloads                → 方便本机测试，不依赖网络
#
# 约定（见 docs/install-state.md）：
#   * 安装目录固定为 {autopf}\MediaForge，向导不允许改
#   * 集成包随安装包分发，也在更新包的覆盖范围内
#   * 版本号只来自 package.json
param(
    [switch]$SkipBuild,
    [switch]$SkipZip,
    [string]$Version = ''
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..").Path
if (-not $Version) {
    $Version = (Get-Content "$root\package.json" -Raw | ConvertFrom-Json).version
}
$stage = "$root\release\stage-$Version"
$out = "$root\release"
$iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"

function Compress-Folder([string]$Source, [string]$Destination, [string]$Label) {
    # 7-Zip 比 Compress-Archive 快一个量级（5 GB 目录：约 3 分钟 vs 10 分钟）。
    $z7 = 'C:\Program Files\7-Zip\7z.exe'
    if (Test-Path $z7) {
        Push-Location $Source
        try {
            $ErrorActionPreference = 'Continue'
            & $z7 a -tzip -mx=3 -bso0 -bsp0 $Destination * | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "7z 压缩失败：$LASTEXITCODE" }
        } finally { Pop-Location }
    } else {
        Compress-Archive -Path "$Source\*" -DestinationPath $Destination -CompressionLevel Optimal
    }
    Write-Host ("  {0}: {1} MB" -f $Label, [Math]::Round((Get-Item $Destination).Length/1MB,1))
}

function Get-SigningPassword {
    # 环境变量优先；其次读用户级环境（setx 写 HKCU\Environment，
    # 已在运行的进程继承不到新值，只能直接读注册表）。
    if ($env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD) { return $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD }
    try {
        $value = (Get-ItemProperty 'HKCU:\Environment' -Name 'TAURI_SIGNING_PRIVATE_KEY_PASSWORD' -ErrorAction Stop).TAURI_SIGNING_PRIVATE_KEY_PASSWORD
        if ($value) { return [string]$value }
    } catch { }
    return ''
}

function Assert-VersionConsistent([string]$Expected) {
    $pkg = (Get-Content "$root\package.json" -Raw | ConvertFrom-Json).version
    $conf = (Get-Content "$root\src-tauri\tauri.conf.json" -Raw | ConvertFrom-Json).version
    $cargo = (Select-String -Path "$root\src-tauri\Cargo.toml" -Pattern '^version = "([^"]+)"' | Select-Object -First 1).Matches.Groups[1].Value
    if ($pkg -ne $Expected -or $conf -ne $Expected -or $cargo -ne $Expected) {
        throw "版本不一致：package.json=$pkg tauri.conf.json=$conf Cargo.toml=$cargo（期望 $Expected）"
    }
    Write-Host "  版本一致：$Expected"
}

function Invoke-Native([string]$Label, [scriptblock]$Block) {
    # PowerShell 5.1 会把原生命令写到 stderr 的进度信息当成终止错误，
    # 所以这里只认退出码。
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Block 2>&1 | Select-Object -Last 6 | ForEach-Object { Write-Host ("  " + $_) } }
    finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "$Label failed with exit code $LASTEXITCODE" }
}

Write-Host "=== MediaForge $Version 打包 ==="
Assert-VersionConsistent $Version

if (-not $SkipBuild) {
    Write-Host "[1/6] 生产构建"
    $env:NO_PROXY = 'rsproxy.cn,static.crates.io,index.crates.io'
    Push-Location $root
    try { Invoke-Native 'tauri build' { pnpm tauri build --no-bundle } }
    finally { Pop-Location }
} else {
    Write-Host "[1/5] 跳过构建（-SkipBuild）"
}

Write-Host "[2/6] staging"
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item "$root\src-tauri\target\release\pymss-studio.exe" "$stage\MediaForge.exe" -Force
foreach ($item in @('python', 'python-runtime', 'integrations')) {
    # python-runtime\runtime-envs 不进安装包：它是可替换资产，走独立的运行时包。
    if ($item -eq 'python-runtime' -and (Test-Path (Join-Path $root 'python-runtime\runtime-envs'))) {
        New-Item -ItemType Directory -Force -Path $stage | Out-Null
        Copy-Item (Join-Path $root 'python-runtime') $stage -Recurse -Force -Exclude 'runtime-envs'
        continue
    }
    $source = Join-Path $root $item
    $target = Join-Path $stage $item
    if (-not (Test-Path $source)) { throw "缺少 $item，无法打包" }
    if (Test-Path $target) { Remove-Item $target -Recurse -Force }
    Copy-Item $source $target -Recurse
}
if (-not (Test-Path "$stage\python\safe_fs.py")) { throw "staging 缺少 python\safe_fs.py" }
if (-not (Test-Path "$stage\integrations\dsh-mediaforge\cordis.patch.yml")) { throw "staging 缺少 DSH 集成包" }
if (-not (Test-Path "$root\release\bin")) { throw "缺少 release\bin（ffmpeg/aria2c）" }
if (Test-Path "$stage\bin") { Remove-Item "$stage\bin" -Recurse -Force }
Copy-Item "$root\release\bin" "$stage\bin" -Recurse
@"
MediaForge $Version (Windows x64 CUDA)
Author: TheSmallHanCat

Includes a base Python runtime and a CUDA environment; an NVIDIA driver enables GPU.
Switch or install backends under Settings -> Runtime.

DeepSeek Harness integration
---------------------------
Bundle: <install>\integrations\dsh-mediaforge
DeepSeek Harness integration
---------------------------
Bundle: <install>\integrations\dsh-mediaforge
The installer writes the launcher and the install pointer. Registering the bundle
is one step inside Harness (plugin_manager install_bundle on that folder), then a
restart; verify.ps1 prints the launcher resolution and the MCP handshake result.
Read DESIGN.md for why the bundle carries no absolute path.
  verify.ps1   prints launcher resolution and the MCP handshake result
  DESIGN.md    why the bundle carries no absolute path
"@ | Set-Content "$stage\README.txt" -Encoding UTF8
Get-Item "$stage\MediaForge.exe" | ForEach-Object { Write-Host ("  exe: {0} MB" -f [Math]::Round($_.Length/1MB,1)) }

if (-not $SkipZip) {
    Write-Host "[3/6] 便携 zip"
    $zip = "$out\MediaForge-$Version-windows-x64-portable.zip"
    if (Test-Path $zip) { Remove-Item $zip -Force }
    Compress-Folder -Source $stage -Destination $zip -Label "portable"
    } else {
    Write-Host "[3/5] 跳过 zip（-SkipZip）"
}


Write-Host "# 运行时资产包（独立于版本发布，按 sha256 寻址）
$runtimeAssetId = if ($env:MEDIAFORGE_RUNTIME_ASSET_ID) { $env:MEDIAFORGE_RUNTIME_ASSET_ID } else { 'runtime-cuda-2026.10.1' }
$runtimeSrc = Join-Path $root 'python-runtime\runtime-envs\cuda'
$runtimeOut = Join-Path $root 'release\runtime'
$runtimeZip = Join-Path $runtimeOut "$runtimeAssetId.zip"
$runtimeManifest = Join-Path $runtimeOut "$runtimeAssetId.json"
if (Test-Path $runtimeSrc) {
    New-Item -ItemType Directory -Force -Path $runtimeOut | Out-Null
    if (-not (Test-Path $runtimeZip)) {
        Write-Host "  打包运行时资产（$runtimeAssetId，约 5 GB，几分钟）"
        Compress-Folder -Source $runtimeSrc -Destination $runtimeZip -Label $runtimeAssetId
    } else {
        Write-Host ("  运行时包已存在: {0} MB" -f [Math]::Round((Get-Item $runtimeZip).Length/1MB,1))
    }
    $hash = (Get-FileHash $runtimeZip -Algorithm SHA256).Hash.ToLower()
    $manifest = [ordered]@{
        assetId = $runtimeAssetId
        backend = 'cuda'
        unpackRoot = 'cuda'
        size = (Get-Item $runtimeZip).Length
        sha256 = $hash
        urls = @("https://www.modelscope.cn/datasets/TLBtianhu/MediaForge-Dist/resolve/master/runtime/$runtimeAssetId.zip")
    }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content $runtimeManifest -Encoding UTF8
    Write-Host "  manifest: $runtimeManifest"
    Write-Host "  sha256: $hash"
} else {
    Write-Host "  跳过运行时资产包（源码树里没有 python-runtime\runtime-envs\cuda）"
}

[4/6] 更新包（不含运行时，签名可选）"
$updateRoot = "$out\update-stage"
if (Test-Path $updateRoot) { Remove-Item $updateRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $updateRoot | Out-Null
foreach ($item in @('MediaForge.exe', 'python', 'bin', 'integrations')) {
    Copy-Item (Join-Path $stage $item) $updateRoot -Recurse -Force
}
$updateZip = "$out\MediaForge-$Version-windows-x64-update.zip"
if (Test-Path $updateZip) { Remove-Item $updateZip -Force }
Compress-Folder -Source $updateRoot -Destination $updateZip -Label "update.zip"
Remove-Item $updateRoot -Recurse -Force
$keyPath = Join-Path $env:USERPROFILE '.mediaforge-keys\mediaforge.key'
$signingPassword = Get-SigningPassword
if ($signingPassword -and (Test-Path $keyPath)) {
    $signer = "$root\node_modules\.bin\tauri.cmd"
    Push-Location $root
    try { Invoke-Native 'signer' { & $signer signer sign -f $keyPath -p $signingPassword $updateZip } }
    finally { Pop-Location }
    if (Test-Path "$updateZip.sig") {
        $b64 = (Get-Content "$updateZip.sig" -Raw).Trim()  # .sig 本身就是 base64，清单直接用
        [System.IO.File]::WriteAllText("$updateZip.signature.b64", $b64, (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "  已签名；清单 signature 字段用 $updateZip.sig 的内容"
    }
} else {
    Write-Host "  跳过签名：设置 TAURI_SIGNING_PRIVATE_KEY_PASSWORD 后重跑即可（或单独跑 scripts\sign-update.ps1）"
}

Write-Host "[5/6] 安装包"
# 本机的 Inno 版本比 CI 旧，不认 SetupArchitecture；只对本地副本做剥离，
# 仓库里的 .iss 保持原样。
$localIss = "$out\mediaforge.local.iss"
Get-Content "$root\installer\mediaforge.iss" |
    Where-Object { $_ -notmatch '^SetupArchitecture=' } |
    Set-Content $localIss -Encoding UTF8
$env:MEDIAFORGE_VERSION = $Version
$env:MEDIAFORGE_PORTABLE_DIR = $stage
$env:MEDIAFORGE_INSTALLER_OUTPUT = $out
Invoke-Native 'ISCC' { & $iscc $localIss }

Write-Host "[6/6] 复制到 Downloads"
$setup = "$out\MediaForge-$Version-windows-x64-cuda-setup.exe"
if (-not (Test-Path $setup)) { throw "没有找到安装包: $setup" }
Copy-Item $setup "$env:USERPROFILE\Downloads\$(Split-Path $setup -Leaf)" -Force
Get-Item $setup | ForEach-Object { Write-Host ("  installer: {0} MB -> Downloads" -f [Math]::Round($_.Length/1MB,1)) }
Write-Host "DONE"
