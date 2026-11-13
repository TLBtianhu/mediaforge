# 造一个小型运行时资产 zip，供首装/回滚等冒烟使用。
#
# 微型解释器返回与生产 ABI probe 相同的 JSON，使用动态资产根和 CUDA 后端；
# 生产校验没有 fixture 特判。文件内容只用于事务测试，不是真实 Python 运行时。
param(
    [Parameter(Mandatory = $true)][string]$OutZip,
    [Parameter(Mandatory = $true)][string]$AssetId
)

$ErrorActionPreference = 'Stop'
$work = Join-Path $env:TEMP ('mediaforge-fixture-runtime-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $work | Out-Null
try {
    $source = Join-Path $work 'fake-python.rs'
    @'
fn main() {
    let exe = std::env::current_exe().unwrap();
    let root = exe.parent().unwrap().parent().unwrap().to_string_lossy();
    println!(
        concat!(
            "{{\"python\":\"3.12\",\"platform\":{:?},\"arch\":{:?},",
            "\"prefix\":{:?},\"basePrefix\":{:?},\"imports\":true,\"torchBackend\":\"cuda\"}}"
        ),
        std::env::consts::OS,
        std::env::consts::ARCH,
        root,
        root,
    );
}
'@ | Set-Content -LiteralPath $source -Encoding ASCII
    $fakePython = Join-Path $work 'python.exe'
    & rustc --edition 2021 -C opt-level=1 -o $fakePython $source
    if ($LASTEXITCODE -ne 0) { throw '无法编译小资产解释器（需要 rustc）' }

    $stage = Join-Path $work 'stage'
    $scripts = Join-Path $stage 'Scripts'
    New-Item -ItemType Directory -Force -Path $scripts | Out-Null
    Copy-Item $fakePython (Join-Path $scripts 'python.exe')
    Set-Content -LiteralPath (Join-Path $scripts 'python312.dll') -Value 'tiny matching DLL fixture' -Encoding ASCII
    New-Item -ItemType Directory -Force -Path (Join-Path $stage 'Lib/encodings'), (Join-Path $stage 'Lib/site-packages') | Out-Null
    Set-Content -LiteralPath (Join-Path $stage 'Lib/encodings/__init__.py') -Value '# tiny stdlib fixture' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $stage 'Lib/site-packages/.fixture') -Value 'transaction test only' -Encoding ASCII
    Set-Content (Join-Path $stage 'asset.txt') $AssetId -Encoding ASCII

    $outDir = Split-Path -Parent $OutZip
    if ($outDir) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }
    Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $OutZip -Force
} finally {
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}
