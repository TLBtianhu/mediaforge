# 造一个小型运行时资产 zip，供首装/回滚等冒烟使用。
#
# 资产里的 python.exe 是现场编译的微型可执行文件（退出码 0），
# 用来证明"解包后的解释器能运行"，避免把 3.6 GB 真运行时或用户现有环境带进测试。
param(
    [Parameter(Mandatory = $true)][string]$OutZip,
    [Parameter(Mandatory = $true)][string]$AssetId
)

$ErrorActionPreference = 'Stop'
$work = Join-Path $env:TEMP ('mediaforge-fixture-runtime-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $work | Out-Null
try {
    $source = Join-Path $work 'fake-python.rs'
    Set-Content $source 'fn main() { std::process::exit(0); }' -Encoding ASCII
    $fakePython = Join-Path $work 'python.exe'
    & rustc --edition 2021 -C opt-level=1 -o $fakePython $source
    if ($LASTEXITCODE -ne 0) { throw '无法编译小资产解释器（需要 rustc）' }

    $stage = Join-Path $work 'stage'
    $scripts = Join-Path $stage 'Scripts'
    New-Item -ItemType Directory -Force -Path $scripts | Out-Null
    Copy-Item $fakePython (Join-Path $scripts 'python.exe')
    Set-Content (Join-Path $stage 'asset.txt') $AssetId -Encoding ASCII

    $outDir = Split-Path -Parent $OutZip
    if ($outDir) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }
    Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $OutZip -Force
} finally {
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}
