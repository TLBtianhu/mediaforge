# Windows packaging entry point. Normal packages contain bootstrap Python and
# tools; inference environments and models are separate managed assets.
# Candidate example: -SkipBuild -SkipSign -SkipDownloadCopy -Version 0.0.32
#   -OutputDir release/candidates/0.0.32
param(
    [switch]$SkipBuild,
    [switch]$SkipZip,
    [switch]$SkipSign,
    [switch]$SkipDownloadCopy,
    [switch]$StageOnly,
    [string]$Version = '',
    [string]$OutputDir = '',
    [string]$StageName = '',
    [string]$BootstrapDir = 'python-runtime',
    [ValidateSet('windows-x64-cuda', 'windows-x64-cpu', 'windows-x64-online', 'windows-x64-rocm')]
    [string]$PackageSuffix = 'windows-x64-cuda'
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..").Path
. "$PSScriptRoot\release-signing.ps1"
if (-not $Version) {
    $Version = (Get-Content "$root\package.json" -Raw | ConvertFrom-Json).version
}

function Assert-ReleaseVersion([string]$Value) {
    if ($Value -cnotmatch '\A(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?(?:\+[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?\z') {
        throw "Invalid release version: $Value"
    }
}

function Assert-NoReparsePath([string]$Path) {
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        $entry = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
        if ($entry) {
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "release path refuses reparse point: $cursor"
            }
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
}

function Get-ReleasePaths([string]$Root, [string]$Version, [string]$OutputDir, [string]$StageName, [string]$PackageSuffix) {
    Assert-ReleaseVersion $Version
    $releaseRoot = [IO.Path]::GetFullPath((Join-Path $Root 'release')).TrimEnd('\', '/')
    $output = if (-not $OutputDir) { $releaseRoot } elseif ([IO.Path]::IsPathRooted($OutputDir)) {
        [IO.Path]::GetFullPath($OutputDir)
    } else { [IO.Path]::GetFullPath((Join-Path $Root $OutputDir)) }
    $output = $output.TrimEnd('\', '/')
    if ($output -ne $releaseRoot -and -not $output.StartsWith($releaseRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "OutputDir must be inside the repository release directory: $output"
    }
    Assert-NoReparsePath $output
    if (-not $StageName) { $StageName = "stage-$Version" }
    if ($StageName -cne "stage-$Version" -and $StageName -cne "MediaForge-$Version-$PackageSuffix") {
        throw "Invalid stage directory for this version/package: $StageName"
    }
    [PSCustomObject]@{ Output = $output; Stage = (Join-Path $output $StageName); StageName = $StageName }
}

function Assert-ReleaseOwnedPath([string]$Path, [string]$OutputRoot, [string]$ExpectedName) {
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    $parent = [IO.Path]::GetDirectoryName($full)
    if ($parent -ne [IO.Path]::GetFullPath($OutputRoot).TrimEnd('\', '/') -or [IO.Path]::GetFileName($full) -cne $ExpectedName -or $ExpectedName -in @('', '.', '..')) {
        throw "Refusing release mutation outside its exact output path: $Path"
    }
    Assert-NoReparsePath $full
    if (Test-Path -LiteralPath $full -PathType Container) {
        $pending = New-Object 'Collections.Generic.Stack[string]'
        $pending.Push($full)
        while ($pending.Count) {
            foreach ($entry in Get-ChildItem -LiteralPath ($pending.Pop()) -Force) {
                if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "release cleanup refuses reparse point: $($entry.FullName)" }
                if ($entry.PSIsContainer) { $pending.Push($entry.FullName) }
            }
        }
    }
}

function Remove-ReleaseOwnedPath([string]$Path, [string]$OutputRoot, [string]$ExpectedName) {
    Assert-ReleaseOwnedPath $Path $OutputRoot $ExpectedName
    if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Recurse -Force }
}

function Get-ReleaseBootstrapPath([string]$Root, [string]$BootstrapDir) {
    if ([string]::IsNullOrWhiteSpace($BootstrapDir)) { throw 'BootstrapDir must select an explicit source' }
    $base = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    $source = if ([IO.Path]::IsPathRooted($BootstrapDir)) { [IO.Path]::GetFullPath($BootstrapDir) } else { [IO.Path]::GetFullPath((Join-Path $base $BootstrapDir)) }
    $source = $source.TrimEnd('\', '/')
    $isDefault = $source -ceq (Join-Path $base 'python-runtime')
    $isRelease = [IO.Path]::GetDirectoryName($source) -ceq (Join-Path $base 'release') -and [IO.Path]::GetFileName($source) -cmatch '\Abootstrap-[A-Za-z0-9][A-Za-z0-9._-]*\z'
    if (-not $isDefault -and -not $isRelease) { throw 'BootstrapDir must be workspace python-runtime or exact release/bootstrap-* source' }
    Assert-NoReparsePath $source
    if (-not (Test-Path -LiteralPath $source -PathType Container)) { throw 'Selected bootstrap source does not exist; no fallback is used' }
    $source
}

function Compress-Folder([string]$Source, [string]$Destination, [string]$Label) {
    # 7-Zip 比 Compress-Archive 快一个量级（5 GB 目录：约 3 分钟 vs 10 分钟）。
    $z7 = 'C:\Program Files\7-Zip\7z.exe'
    if (Test-Path $z7) {
        $result = Invoke-ReleaseProcess -FilePath $z7 -Arguments @('a', '-tzip', '-mx=3', '-bso0', '-bsp0', $Destination, '*') -WorkingDirectory $Source -TimeoutMilliseconds 600000
        Write-ReleaseProcessOutput $result
        if ($result.ExitCode -ne 0) { throw "7z compression failed (exit $($result.ExitCode))" }
    } else {
        Compress-Archive -Path "$Source\*" -DestinationPath $Destination -CompressionLevel Optimal
    }
    Write-Host ("  {0}: {1} MB" -f $Label, [Math]::Round((Get-Item $Destination).Length/1MB,1))
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

function Copy-ReleaseTree {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [string[]]$ExcludeRootDirectories = @()
    )
    Assert-NoReparsePath $Destination
    # Filter before recursing/copying: copying runtime-envs and deleting it afterwards
    # temporarily duplicates several GB even though the final bootstrap is small.
    $sourceItem = Get-Item -LiteralPath $Source -Force
    if ($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "staging refuses linked source: $Source"
    }
    if (-not $sourceItem.PSIsContainer) {
        Copy-Item -LiteralPath $Source -Destination $Destination -Force
        return
    }
    $sourcePath = [IO.Path]::GetFullPath($Source).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $destinationPath = [IO.Path]::GetFullPath($Destination).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if ($destinationPath.StartsWith($sourcePath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "staging destination must be outside its source: $Destination"
    }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $cacheDirectories = @('__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.cache', '.git')
    foreach ($entry in Get-ChildItem -LiteralPath $Source -Force) {
        if ($entry.PSIsContainer -and (($ExcludeRootDirectories -contains $entry.Name) -or ($cacheDirectories -contains $entry.Name))) {
            continue
        }
        Copy-ReleaseTree -Source $entry.FullName -Destination (Join-Path $Destination $entry.Name)
    }
}

function Remove-DevOnlyIntegrationScripts([string]$Root) {
    # The PS1 helpers are repository maintenance tools; user bundles and portable
    # zip must never contain them.
    $directory = Join-Path $Root 'integrations/dsh-mediaforge'
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { return }
    foreach ($pattern in @('*.ps1', '*.bak*')) {
        Get-ChildItem -LiteralPath $directory -File -Filter $pattern -ErrorAction SilentlyContinue | Remove-Item -Force
    }
}

function Assert-ReleaseContract([string]$Stage) {
    foreach ($name in @('protocol.json', 'runtime.json', 'execution-quota.json', 'library-schema.json')) {
        $path = Join-Path (Join-Path $Stage 'manifest') $name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "staging is missing worker contract: $path"
        }
    }
}

function Copy-ReleaseUpdate([string]$Stage, [string]$Destination) {
    # The worker and its entire manifest directory must change together.
    Assert-ReleaseContract $Stage
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    foreach ($item in @('MediaForge.exe', 'python', 'bin', 'integrations', 'manifest')) {
        Copy-ReleaseTree -Source (Join-Path $Stage $item) -Destination (Join-Path $Destination $item)
    }
    Remove-DevOnlyIntegrationScripts $Destination
    Assert-ReleaseContract $Destination
}

function Copy-ReleaseResources([string]$Root, [string]$Stage, [string]$BootstrapDir = 'python-runtime') {
    # Copy allowlisted application surfaces only. Managed environments are
    # excluded before recursion, including incoming archives under runtime-envs.
    foreach ($item in @('python', 'python-runtime', 'integrations', 'manifest')) {
        $source = if ($item -eq 'python-runtime') { Get-ReleaseBootstrapPath -Root $Root -BootstrapDir $BootstrapDir } else { Join-Path $Root $item }
        $target = Join-Path $Stage $item
        if (-not (Test-Path -LiteralPath $source -PathType Container)) { throw "Missing package source: $source" }
        $exclude = if ($item -eq 'python-runtime') { @('runtime-envs') } else { @('data', 'models', '.dev-data', '.dev-webview') }
        Copy-ReleaseTree -Source $source -Destination $target -ExcludeRootDirectories $exclude
    }
    Remove-DevOnlyIntegrationScripts $Stage
    Assert-ReleaseContract $Stage
}

function Assert-ReleaseStage([string]$Stage) {
    Assert-ReleaseContract $Stage
    foreach ($relative in @('MediaForge.exe', 'python/worker.py', 'python/core/runtime_audio_probe.py',
            'python/shared/safe_fs.py', 'python/plugin-sdk/pymss-sdk.js', 'python/plugin-sdk/pymss-sdk.css',
            'python/runtime-manifest.json', 'python-runtime/python.exe', 'bin/ffmpeg.exe', 'bin/ffprobe.exe',
            'bin/aria2c.exe', 'bin/vcrt/msvcp140.dll', 'integrations/dsh-mediaforge/cordis.patch.yml')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Stage $relative) -PathType Leaf)) { throw "Stage contract is missing $relative" }
    }
    # Developer-only PS1 helpers must never ship; they are removed during staging.
    $integration = Join-Path $Stage 'integrations/dsh-mediaforge'
    if (Test-Path -LiteralPath $integration -PathType Container) {
        $helpers = @(Get-ChildItem -LiteralPath $integration -File -Filter '*.ps1' -ErrorAction SilentlyContinue)
        if ($helpers.Count) { throw "staging still contains developer-only DSH scripts: $($helpers.Name -join ', ')" }
    }
    foreach ($relative in @('python-runtime/runtime-envs', 'models', 'data', '.dev-data', '.dev-webview', 'mediaforge.portable')) {
        if (Test-Path -LiteralPath (Join-Path $Stage $relative)) { throw "Stage contains mutable user assets: $relative" }
    }
}

$paths = Get-ReleasePaths -Root $root -Version $Version -OutputDir $OutputDir -StageName $StageName -PackageSuffix $PackageSuffix
$stage = $paths.Stage
$stageName = $paths.StageName
$out = $paths.Output
$iscc = if ($env:MEDIAFORGE_ISCC) { $env:MEDIAFORGE_ISCC } else { "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" }

Write-Host "=== MediaForge $Version 打包 ==="
Assert-VersionConsistent $Version
if ($StageOnly -and -not $SkipBuild) { throw '-StageOnly requires -SkipBuild; compile the native EXE first.' }
if (-not $StageOnly -and -not (Test-Path -LiteralPath $iscc -PathType Leaf)) { throw "Inno Setup compiler is missing: $iscc" }
foreach ($relative in @('release/bin/ffmpeg.exe', 'release/bin/ffprobe.exe', 'release/bin/aria2c.exe', 'release/bin/vcrt/msvcp140.dll')) {
    if (-not (Test-Path -LiteralPath (Join-Path $root $relative) -PathType Leaf)) { throw "Missing packaging input: $relative (prepare/fetch it explicitly first)" }
}
$expectedPython = [string](Get-Content -LiteralPath (Join-Path $root 'python/runtime-manifest.json') -Raw | ConvertFrom-Json).python
$bootstrapSource = Get-ReleaseBootstrapPath -Root $root -BootstrapDir $BootstrapDir
Assert-ReleasePython -PythonPath (Join-Path $bootstrapSource 'python.exe') -ExpectedVersion $expectedPython
if (Test-Path -LiteralPath "$stage\python-runtime\python.exe" -PathType Leaf) {
    Assert-ReleasePython -PythonPath "$stage\python-runtime\python.exe" -ExpectedVersion $expectedPython
}

if (-not $SkipBuild) {
    Write-Host "[1/6] 生产构建"
    Invoke-ReleaseNativeBuild -Root $root -PythonPath (Join-Path $bootstrapSource 'python.exe')
} else {
    Write-Host "[1/5] 跳过构建（-SkipBuild）"
}

Write-Host "[2/6] staging"
$sourceExe = Join-Path $root 'src-tauri\target\release\pymss-studio.exe'
# A stale -SkipBuild binary must be rejected before touching an existing stage.
Assert-ReleaseExecutable -Path $sourceExe -ExpectedVersion $Version
if (Test-Path -LiteralPath "$stage\MediaForge.exe" -PathType Leaf) {
    Assert-ReleaseExecutable -Path "$stage\MediaForge.exe" -ExpectedVersion $Version
}
New-Item -ItemType Directory -Force -Path $out | Out-Null
Remove-ReleaseOwnedPath -Path $stage -OutputRoot $out -ExpectedName $stageName
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item -LiteralPath $sourceExe -Destination "$stage\MediaForge.exe" -Force
Assert-ReleaseExecutable -Path "$stage\MediaForge.exe" -ExpectedVersion $Version
Copy-ReleaseResources -Root $root -Stage $stage -BootstrapDir $BootstrapDir
Assert-ReleasePython -PythonPath "$stage\python-runtime\python.exe" -ExpectedVersion $expectedPython
& "$root\scripts\stage-plugin-sdk.ps1" -StagePath $stage
# VC++ 运行库：per-user 安装没有管理员权限，不能依赖 vc_redist；
# 这里把可再发行 DLL 放进 bin\vcrt（随安装包走），并由运行时装好后复制到
# runtime-envs/<assetId>\Scripts。缺这一层时，torch/cv2 的 import 会直接失败。
$vcrtSource = "$root\release\bin\vcrt"
Copy-ReleaseTree -Source "$root\release\bin" -Destination "$stage\bin"
# 引导解释器也一并带上：python.exe 本身自带 vcruntime，但 C++ 扩展需要 msvcp140。
foreach ($dll in Get-ChildItem $vcrtSource -Filter '*.dll') {
    Copy-Item $dll.FullName "$stage\python-runtime\$($dll.Name)" -Force
}
Write-Host "  已把 VC++ 运行库放入 bin\vcrt 与 python-runtime"
@"
MediaForge $Version ($PackageSuffix)
Author: TheSmallHanCat

Includes bootstrap Python and local media tools. The processing runtime and models
are downloaded separately from inside the app. Prepare the runtime under Settings
and download the models you need from the model library.

DeepSeek Harness integration
---------------------------
Bundle: <install-root>\integrations\dsh-mediaforge
Load this folder through Harness's plugin manager if you want MCP integration.
MediaForge's installer does not modify or restart Harness.

Upgrading from an older build
-----------------------------
Older versions injected MCP rows into the DSH profile's cordis.patch.yml.
This version does not, and it does not remove the old block automatically.
If the old block is still present, inside the MediaForge block delete only
its own rows (the BEGIN/END markers, the comment, the insert containing
mediaforge-mcp, and any mediaforge-mcp override). Do not touch other blocks'
inserts. Keep everything else. Install the bundle immediately after;
see README.md in integrations\dsh-mediaforge.
"@ | Set-Content "$stage\README.txt" -Encoding UTF8
Assert-ReleaseStage $stage
Get-Item "$stage\MediaForge.exe" | ForEach-Object { Write-Host ("  exe: {0} MB" -f [Math]::Round($_.Length/1MB,1)) }
if ($StageOnly) { Write-Host "Stage ready: $stage"; return }

if (-not $SkipZip) {
    Write-Host "[3/6] 便携 zip"
    $zip = "$out\MediaForge-$Version-windows-x64-portable.zip"
    Remove-ReleaseOwnedPath -Path $zip -OutputRoot $out -ExpectedName "MediaForge-$Version-windows-x64-portable.zip"
    # 便携版靠程序同目录的标记文件选择 <exe>\data；安装包不能带这个文件，
    # 所以只在压缩期间放进 stage，压缩完立即删掉。
    $portableMarker = Join-Path $stage 'mediaforge.portable'
    Set-Content $portableMarker 'portable' -Encoding ASCII
    try {
        Compress-Folder -Source $stage -Destination $zip -Label "portable"
    } finally {
        Remove-Item $portableMarker -Force -ErrorAction SilentlyContinue
    }
    } else {
    Write-Host "[3/5] 跳过 zip（-SkipZip）"
}


Write-Host "[4/6] 更新包（不含运行时，签名可选）"
$updateRoot = "$out\update-stage-$Version"
Remove-ReleaseOwnedPath -Path $updateRoot -OutputRoot $out -ExpectedName "update-stage-$Version"
New-Item -ItemType Directory -Force -Path $updateRoot | Out-Null
Copy-ReleaseUpdate -Stage $stage -Destination $updateRoot
$updateZip = "$out\MediaForge-$Version-windows-x64-update.zip"
Remove-ReleaseOwnedPath -Path $updateZip -OutputRoot $out -ExpectedName "MediaForge-$Version-windows-x64-update.zip"
foreach ($suffix in @('.sig', '.signature.b64')) {
    Remove-ReleaseOwnedPath -Path "$updateZip$suffix" -OutputRoot $out -ExpectedName "MediaForge-$Version-windows-x64-update.zip$suffix"
}
Compress-Folder -Source $updateRoot -Destination $updateZip -Label "update.zip"
Remove-ReleaseOwnedPath -Path $updateRoot -OutputRoot $out -ExpectedName "update-stage-$Version"
if ($SkipSign) {
    Write-Host '  跳过签名（-SkipSign，隔离验证不读取签名凭据）'
} else {
    $keyPath = [Environment]::GetEnvironmentVariable('TAURI_SIGNING_PRIVATE_KEY_PATH', 'Process')
    if ($keyPath) {
        Assert-ReleaseExecutable -Path "$stage\MediaForge.exe" -ExpectedVersion $Version
        Invoke-ReleaseArchiveSigning -Archive $updateZip -ExpectedVersion $Version -Root $root -KeyPath $keyPath `
            -Password ([Environment]::GetEnvironmentVariable('TAURI_SIGNING_PRIVATE_KEY_PASSWORD', 'Process'))
        Write-Host '  已签名'
    } else {
        Write-Host '  跳过签名：请显式设置 TAURI_SIGNING_PRIVATE_KEY_PATH 后签名'
    }
}

Write-Host "[5/6] 安装包"
# 本机的 Inno 版本比 CI 旧，不认 SetupArchitecture；只对本地副本做剥离，
# 仓库里的 .iss 保持原样。
$localIss = "$out\mediaforge-$Version.local.iss"
Assert-ReleaseOwnedPath -Path $localIss -OutputRoot $out -ExpectedName "mediaforge-$Version.local.iss"
Get-Content "$root\installer\mediaforge.iss" |
    Where-Object { $_ -notmatch '^SetupArchitecture=' } |
    Set-Content $localIss -Encoding UTF8
$env:MEDIAFORGE_VERSION = $Version
$env:MEDIAFORGE_PORTABLE_DIR = $stage
$env:MEDIAFORGE_INSTALLER_OUTPUT = $out
$env:MEDIAFORGE_INSTALLER_ICON = Join-Path $root 'src-tauri\icons\icon.ico'
$env:MEDIAFORGE_PACKAGE_SUFFIX = $PackageSuffix
$setup = "$out\MediaForge-$Version-$PackageSuffix-setup.exe"
Assert-ReleaseOwnedPath -Path $setup -OutputRoot $out -ExpectedName "MediaForge-$Version-$PackageSuffix-setup.exe"
$compilerResult = Invoke-ReleaseProcess -FilePath $iscc -Arguments @($localIss) -WorkingDirectory $root -TimeoutMilliseconds 600000
Write-ReleaseProcessOutput $compilerResult
if ($compilerResult.ExitCode -ne 0) { throw "ISCC failed with exit code $($compilerResult.ExitCode)" }

Write-Host "[6/6] 安装包输出"
if (-not (Test-Path $setup)) { throw "没有找到安装包: $setup" }
if ($SkipDownloadCopy) {
    Write-Host '  跳过本机副本（-SkipDownloadCopy）'
} else {
    $localOut = "$out\local"
    Assert-NoReparsePath $localOut
    New-Item -ItemType Directory -Force -Path $localOut | Out-Null
    Copy-Item $setup (Join-Path $localOut (Split-Path $setup -Leaf)) -Force
    Write-Host "  已复制到 $localOut"
}
Get-Item $setup | ForEach-Object { Write-Host ("  installer: {0} MB" -f [Math]::Round($_.Length/1MB,1)) }
Write-Host "DONE"
