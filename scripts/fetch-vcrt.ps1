# 收集 VC++ 可再发行运行库，供 app-local 捆绑。
#
# 为什么是 app-local 而不是安装 vc_redist：
#   * per-user 安装没有管理员权限，vc_redist 装不上；
#   * 缺 msvcp140 时，torch/cv2 这类 C++ 扩展会在 import 时直接 "DLL load failed"，
#     用户只看到一句系统级错误。
#
# 这些文件是 Visual C++ Redistributable 的一部分，微软允许随应用以 app-local
# 方式分发。来源固定为构建机的 System32（同一套 VC 运行库），并在 NOTICE 里注明。
param(
    [string]$Destination = (Join-Path $PSScriptRoot '..\release\bin\vcrt')
)

$ErrorActionPreference = 'Stop'

$dlls = @(
    'msvcp140.dll',
    'msvcp140_1.dll',
    'msvcp140_2.dll',
    'msvcp140_atomic_wait.dll',
    'vcruntime140.dll',
    'vcruntime140_1.dll',
    'concrt140.dll',
    'vcomp140.dll'
)

$source = Join-Path $env:SystemRoot 'System32'
New-Item -ItemType Directory -Force -Path $Destination | Out-Null

$copied = 0
foreach ($dll in $dlls) {
    $from = Join-Path $source $dll
    if (-not (Test-Path $from)) {
        Write-Warning "缺少 $dll（构建机未安装对应版本的 VC 运行库？）"
        continue
    }
    Copy-Item $from (Join-Path $Destination $dll) -Force
    $version = (Get-Item $from).VersionInfo.FileVersion
    Write-Host ("  {0,-28} {1}" -f $dll, $version)
    $copied++
}

if ($copied -eq 0) {
    throw "没有复制到任何 VC 运行库，检查构建机是否安装了 Visual C++ 2015-2022 Redistributable"
}

# 记录来源，方便排查"用户机器上的运行库版本和构建机不一致"
$manifest = @{
    source = 'Microsoft Visual C++ Redistributable (app-local deployment)'
    capturedAt = (Get-Date).ToUniversalTime().ToString('o')
    files = $dlls | Where-Object { Test-Path (Join-Path $Destination $_) }
}
$manifest | ConvertTo-Json -Depth 3 | Set-Content (Join-Path $Destination 'SOURCE.json') -Encoding UTF8
Write-Host "  已复制 $copied 个 DLL 到 $Destination"
