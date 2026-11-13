# Headless smoke recompiles the same source/stage with a unique test identity.
# The production installer is read-only hash metadata, never executed.
param(
    [Parameter(Mandatory = $true)][string]$Stage,
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$Python,
    [string]$Compiler = $env:MEDIAFORGE_ISCC,
    [string]$PreviousStage = '',
    [string]$PreviousVersion = '',
    [string]$Installer = '',
    [string]$Report = ''
)
$ErrorActionPreference = 'Stop'
if (-not $Compiler) { throw 'An explicit -Compiler or MEDIAFORGE_ISCC is required' }
foreach ($inputPath in @($Python, $Compiler)) {
    if (-not (Test-Path -LiteralPath $inputPath -PathType Leaf)) { throw "Required executable does not exist: $inputPath" }
}
function Quote-InstallerSmokeArgument([string]$Value) {
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    '"' + $escaped + '"'
}
$arguments = @('-I', '-B', (Join-Path $PSScriptRoot 'installer_smoke.py'), '--stage', $Stage,
    '--version', $Version, '--iscc', $Compiler)
if ($PreviousStage) { $arguments += @('--previous-stage', $PreviousStage) }
if ($PreviousVersion) { $arguments += @('--previous-version', $PreviousVersion) }
if ($Installer) { $arguments += @('--installer', $Installer) }
if ($Report) { $arguments += @('--report', $Report) }
$start = New-Object Diagnostics.ProcessStartInfo
$start.FileName = (Resolve-Path -LiteralPath $Python).Path
$start.WorkingDirectory = (Get-Location).ProviderPath
$start.Arguments = ($arguments | ForEach-Object { Quote-InstallerSmokeArgument $_ }) -join ' '
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$start.StandardOutputEncoding = [Text.Encoding]::UTF8
$start.StandardErrorEncoding = [Text.Encoding]::UTF8
foreach ($name in @($start.EnvironmentVariables.Keys)) {
    if ($name -match '(?i)KEY|PASSWORD|TOKEN|SECRET' -or
        $name -match '^(?i:MEDIAFORGE_|PYMSS_|TAURI_|VERSIONS_SIGNING_|SIGNPATH_|HF_|HUGGING_FACE_|MODELSCOPE_)' -or
        $name -in @('PYTHONHOME', 'PYTHONPATH', 'WEBVIEW2_USER_DATA_FOLDER')) {
        $start.EnvironmentVariables.Remove($name)
    }
}
$start.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
# This environment belongs to the child; the invoking PowerShell environment is unchanged.
$process = New-Object Diagnostics.Process
$process.StartInfo = $start
try {
    if (-not $process.Start()) { throw 'Unable to start isolated installer smoke' }
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $code = $process.ExitCode
    if ($stdout.Result) { [Console]::Out.Write($stdout.Result) }
    if ($stderr.Result) { [Console]::Error.Write($stderr.Result) }
} finally {
    $process.Dispose()
}
exit $code
