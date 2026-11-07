[CmdletBinding()]
param(
    [ValidateSet('cuda', 'default', 'rocm', 'mps', 'mlx')]
    [string]$Variant = 'default',
    [string]$Python = 'python',
    [string]$RuntimeDir = 'python-runtime',
    [switch]$Minimal
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'release-signing.ps1')
if (-not $Minimal) { throw 'prepare-python-runtime.ps1 only prepares -Minimal bootstrap; build inference assets separately' }
$manifest = Get-Content -LiteralPath (Join-Path $root 'python/runtime-manifest.json') -Raw | ConvertFrom-Json
$pythonExe = (Get-Command $Python -CommandType Application -ErrorAction Stop).Source
# Probe before resolving any destructive operation or creating output directories.
Assert-ReleasePython -PythonPath $pythonExe -ExpectedVersion ([string]$manifest.python)
$pythonHome = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($pythonExe))
$runtime = if ([IO.Path]::IsPathRooted($RuntimeDir)) { [IO.Path]::GetFullPath($RuntimeDir) } else { [IO.Path]::GetFullPath((Join-Path $root $RuntimeDir)) }
$runtime = $runtime.TrimEnd('\', '/')
$release = Join-Path $root 'release'
$isDefault = $runtime -ceq (Join-Path $root 'python-runtime')
$isFreshRelease = [IO.Path]::GetDirectoryName($runtime) -ceq $release -and [IO.Path]::GetFileName($runtime) -cmatch '\Abootstrap-[A-Za-z0-9][A-Za-z0-9._-]*\z'
if (-not $isDefault -and -not $isFreshRelease) {
    throw 'Bootstrap destination must be python-runtime or an exact release/bootstrap-* directory'
}
if ($pythonHome -eq $runtime -or $pythonHome.StartsWith($runtime + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Bootstrap destination must not contain its source interpreter'
}
Assert-ReleaseSigningPath $runtime
if (Test-Path -LiteralPath $runtime) {
    throw 'Bootstrap preparation requires a fresh destination; existing contents are never removed'
}
$site = Join-Path $pythonHome 'Lib/site-packages'
$sourceScripts = Join-Path $pythonHome 'Scripts'
$excluded = @('__pycache__', '.cache', $site, $sourceScripts) + @('Doc', 'docs', 'include', 'libs', 'share', 'tcl', 'Lib/idlelib', 'Lib/tkinter', 'Lib/test') | ForEach-Object {
    if ([IO.Path]::IsPathRooted($_) -or $_ -in @('__pycache__', '.cache')) { $_ } else { Join-Path $pythonHome $_ }
}
robocopy $pythonHome $runtime /E /NP /NFL /NDL /NJH /XD $excluded /XF *.pyc EXTERNALLY-MANAGED | Out-Host
if ($LASTEXITCODE -gt 7) { throw "Bootstrap copy failed (exit $LASTEXITCODE)" }
$global:LASTEXITCODE = 0
$runtimePython = Join-Path $runtime 'python.exe'
Assert-ReleasePython -PythonPath $runtimePython -ExpectedVersion ([string]$manifest.python)
function Invoke-BootstrapCommand([object[]]$Arguments) {
    & $runtimePython @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Bootstrap command failed (exit $LASTEXITCODE)" }
}
Invoke-BootstrapCommand -Arguments @('-m', 'ensurepip', '--upgrade')
Invoke-BootstrapCommand -Arguments @('-m', 'pip', 'install', '--no-cache-dir', '--upgrade', 'pip', 'setuptools', 'wheel')
$requirements = @($manifest.bootstrap.PSObject.Properties | ForEach-Object { [string]$_.Value })
if ($requirements.Count) { Invoke-BootstrapCommand -Arguments (@('-m', 'pip', 'install', '--no-cache-dir') + $requirements) }
Invoke-BootstrapCommand -Arguments @('-I', '-B', '-c', 'import requests,socks,ssl,venv,xmlrpc.client; from pip._vendor.packaging.requirements import Requirement; assert Requirement("pymss>=2.1").name=="pymss"')
Invoke-BootstrapCommand -Arguments @('-m', 'pip', 'install', '--help')
Write-Host 'Prepared Python 3.12 minimal bootstrap without inference environments'
