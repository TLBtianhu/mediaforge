# 给更新包签名。密码来源（先到先得）：
#   1. 环境变量 TAURI_SIGNING_PRIVATE_KEY_PASSWORD
#   2. 用户级环境变量（HKCU\Environment，setx 写的那份）
#   3. 以上都没有时才交互式询问
#
#   powershell -ExecutionPolicy Bypass -File scripts/sign-update.ps1
#   powershell ... -Version 0.0.14
param(
    [string]$Version = ''
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..").Path
if (-not $Version) { $Version = (Get-Content "$root\package.json" -Raw | ConvertFrom-Json).version }

$zip = "$root\release\MediaForge-$Version-windows-x64-update.zip"
$key = Join-Path $env:USERPROFILE '.mediaforge-keys\mediaforge.key'
$signer = "$root\node_modules\.bin\tauri.cmd"

if (-not (Test-Path $zip)) { throw "没有找到更新包：$zip（先跑 package-release.ps1）" }
if (-not (Test-Path $key)) { throw "没有找到私钥：$key" }
if (-not (Test-Path $signer)) { throw "没有找到 tauri CLI：$signer" }

Write-Host "签名对象：$zip"
Write-Host ("大小：{0} MB" -f [Math]::Round((Get-Item $zip).Length/1MB,1))

# 密码：环境变量 → 注册表 → 交互
$password = ''
if ($env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD) {
    $password = $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD
    Write-Host '密码来源：进程环境变量'
} else {
    try {
        $stored = (Get-ItemProperty 'HKCU:\Environment' -Name 'TAURI_SIGNING_PRIVATE_KEY_PASSWORD' -ErrorAction Stop).TAURI_SIGNING_PRIVATE_KEY_PASSWORD
        if ($stored) { $password = [string]$stored; Write-Host '密码来源：HKCU\Environment（setx 写入）' }
    } catch { }
}
if (-not $password) {
    $secure = Read-Host -AsSecureString '请输入私钥密码'
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $password = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    Write-Host '密码来源：交互输入'
}

$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = $password
Push-Location $root
try {
    & $signer signer sign -f $key -p $password $zip
} finally {
    Pop-Location
    Remove-Item Env:\TAURI_SIGNING_PRIVATE_KEY_PASSWORD -ErrorAction SilentlyContinue
}

if (-not (Test-Path "$zip.sig")) { throw '签名失败：没有生成 .sig 文件' }
$b64 = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes("$zip.sig"))
$out = "$zip.signature.b64"
[System.IO.File]::WriteAllText($out, $b64, (New-Object System.Text.UTF8Encoding($false)))
Write-Host ''
Write-Host "签名文件：$zip.sig"
Write-Host "清单用 base64：$out"
Write-Host ('前 60 字符：' + $b64.Substring(0, [Math]::Min(60, $b64.Length)) + '...')
