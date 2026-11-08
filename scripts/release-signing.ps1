# Shared release guards. Importing this file never reads signing credentials.
function Get-ReleasePythonVersion([string]$PythonPath) {
    $result = Invoke-ReleaseProcess -FilePath $PythonPath -Arguments @('-I', '-B', '-c', "import sys; assert sys.prefix == sys.base_prefix, 'Bootstrap must be self-contained'; print('.'.join(map(str, sys.version_info[:2])))") -TimeoutMilliseconds 30000
    if ($result.ExitCode -ne 0) { throw ("Python version probe failed (exit {0}): {1}" -f $result.ExitCode, $result.Stderr.Trim()) }
    $result.Stdout.Trim()
}

function Assert-ReleasePython([string]$PythonPath, [string]$ExpectedVersion) {
    if ($ExpectedVersion -cne '3.12' -or (Get-ReleasePythonVersion $PythonPath) -cne $ExpectedVersion) {
        throw 'Python runtime must match the Python 3.12 release contract'
    }
}

function Get-ReleaseExecutableInfo([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'Release executable is missing' }
    [Diagnostics.FileVersionInfo]::GetVersionInfo([IO.Path]::GetFullPath($Path))
}

function Assert-ReleaseExecutable([string]$Path, [string]$ExpectedVersion) {
    $info = Get-ReleaseExecutableInfo $Path
    if ($info.ProductName -cne 'MediaForge' -or $info.ProductVersion -cne $ExpectedVersion) {
        throw "Release executable identity/version mismatch (expected MediaForge $ExpectedVersion)"
    }
}

function Assert-ReleaseSigningPath([string]$Path) {
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        $item = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
        if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Release signing refuses linked paths'
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
}

function Assert-ReleaseUpdateExecutable([string]$Archive, [string]$ExpectedVersion) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $temporary = Join-Path ([IO.Path]::GetTempPath()) ('mediaforge-signing-check-' + [Guid]::NewGuid().ToString('N'))
    $zip = $null
    try {
        $zip = [IO.Compression.ZipFile]::OpenRead([IO.Path]::GetFullPath($Archive))
        $entries = @($zip.Entries | Where-Object { $_.FullName.Replace('\', '/') -ieq 'MediaForge.exe' })
        if ($entries.Count -ne 1 -or $entries[0].FullName -cne 'MediaForge.exe') {
            throw 'Update archive must contain exactly one root MediaForge.exe'
        }
        [IO.Directory]::CreateDirectory($temporary) | Out-Null
        $executable = Join-Path $temporary 'MediaForge.exe'
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entries[0], $executable)
        Assert-ReleaseExecutable -Path $executable -ExpectedVersion $ExpectedVersion
    } finally {
        if ($zip) { $zip.Dispose() }
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force }
    }
}

function ConvertTo-ReleaseProcessArgument([string]$Value) {
    # Windows argv quoting, including quotes and a trailing backslash. There is
    # no cmd.exe layer; credentials are never passed through this function.
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    '"' + $escaped + '"'
}

function New-ReleaseProcessStartInfo {
    param([string]$FilePath, [string[]]$Arguments = @(), [string]$WorkingDirectory = '', [hashtable]$Environment = @{})
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $FilePath
    $start.Arguments = ($Arguments | ForEach-Object { ConvertTo-ReleaseProcessArgument $_ }) -join ' '
    $start.WorkingDirectory = if ($WorkingDirectory) { $WorkingDirectory } else { (Get-Location).ProviderPath }
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardInput = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $start.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
    foreach ($name in $Environment.Keys) { $start.EnvironmentVariables[$name] = [string]$Environment[$name] }
    $start
}

function Invoke-ReleaseProcess {
    param([string]$FilePath, [string[]]$Arguments = @(), [string]$WorkingDirectory = '',
          [hashtable]$Environment = @{}, [int]$TimeoutMilliseconds = 120000)
    if ($TimeoutMilliseconds -le 0) { throw 'Release process requires a finite positive timeout' }
    $process = New-Object Diagnostics.Process
    $process.StartInfo = New-ReleaseProcessStartInfo -FilePath $FilePath -Arguments $Arguments -WorkingDirectory $WorkingDirectory -Environment $Environment
    try {
        if (-not $process.Start()) { throw 'Release process failed to start' }
        $process.StandardInput.Close()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutMilliseconds)) {
            try { $process.Kill() } catch [InvalidOperationException] { }
            if (-not $process.WaitForExit(10000)) { throw 'Release process could not be reaped after timeout' }
            if (-not $stdout.Wait(5000) -or -not $stderr.Wait(5000)) { throw 'Timed-out release process left output pipes open' }
            # Preserve the bounded process diagnostics in the caller's captured log.
            $out = $stdout.GetAwaiter().GetResult(); $err = $stderr.GetAwaiter().GetResult()
            if ($out) { [Console]::Out.Write($out) }
            if ($err) { [Console]::Error.Write($err) }
            throw 'Release process timed out'
        }
        if (-not $stdout.Wait(5000) -or -not $stderr.Wait(5000)) { throw 'Release process left output pipes open' }
        [PSCustomObject]@{ ExitCode = $process.ExitCode; Stdout = $stdout.GetAwaiter().GetResult(); Stderr = $stderr.GetAwaiter().GetResult() }
    } finally { $process.Dispose() }
}

function Write-ReleaseProcessOutput($Result) {
    if ($Result.Stdout) { [Console]::Out.Write($Result.Stdout) }
    if ($Result.Stderr) { [Console]::Error.Write($Result.Stderr) }
}

function Invoke-ReleaseNativeBuild {
    param([string]$Root, [string]$PythonPath, [string]$Node = '', [int]$TimeoutMilliseconds = 1800000)
    if (-not $Node) { $Node = (Get-Command node -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source }
    $entry = Join-Path $Root 'scripts/tauri.mjs'
    if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) { throw 'Tauri build entry point is missing' }
    # The bootstrap is already required by the packager. Its existing helper
    # creates one hidden console inherited by Node, Cargo and the frontend hook.
    # A job owned by this supervisor makes an outer timeout reclaim only this
    # build tree, even while Cargo or a hook still owns stdout/stderr handles.
    $supervisor = @'
import ctypes,os,subprocess,sys
from pathlib import Path
assert sys.version_info[:2] == (3,12), 'release build requires Python 3.12'
root=Path(sys.argv[1])
sys.path.insert(0,str(root/'python'))
from shared.worker_subprocess import hidden_subprocess_kwargs
if os.name == 'nt':
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_=[('processTime',ctypes.c_longlong),('jobTime',ctypes.c_longlong),('flags',w.DWORD),
                  ('minimum',ctypes.c_size_t),('maximum',ctypes.c_size_t),('active',w.DWORD),
                  ('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
    class IO(ctypes.Structure):
        _fields_=[(name,ctypes.c_ulonglong) for name in ('readOps','writeOps','otherOps','readBytes','writeBytes','otherBytes')]
    class Extended(ctypes.Structure):
        _fields_=[('basic',Basic),('io',IO),('processMemory',ctypes.c_size_t),('jobMemory',ctypes.c_size_t),
                  ('peakProcess',ctypes.c_size_t),('peakJob',ctypes.c_size_t)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.argtypes=(ctypes.c_void_p,w.LPCWSTR);kernel.CreateJobObjectW.restype=w.HANDLE
    kernel.SetInformationJobObject.argtypes=(w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD);kernel.SetInformationJobObject.restype=w.BOOL
    kernel.GetCurrentProcess.argtypes=();kernel.GetCurrentProcess.restype=w.HANDLE
    kernel.AssignProcessToJobObject.argtypes=(w.HANDLE,w.HANDLE);kernel.AssignProcessToJobObject.restype=w.BOOL
    kernel.CloseHandle.argtypes=(w.HANDLE,);kernel.CloseHandle.restype=w.BOOL
    job=kernel.CreateJobObjectW(None,None)
    if not job:raise ctypes.WinError(ctypes.get_last_error())
    limits=Extended();limits.basic.flags=0x2000
    if not kernel.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(job,kernel.GetCurrentProcess()):
        error=ctypes.get_last_error();kernel.CloseHandle(job);raise ctypes.WinError(error)
    # This non-inheritable handle remains owned by the supervisor until exit.
child=subprocess.Popen([sys.argv[2],sys.argv[3],'build','--no-bundle','--ci'],cwd=root,
                       stdin=subprocess.DEVNULL,stdout=sys.stdout,stderr=sys.stderr,
                       **hidden_subprocess_kwargs(inherit_console=True))
raise SystemExit(child.wait())
'@
    $result = Invoke-ReleaseProcess -FilePath $PythonPath -Arguments @('-I', '-B', '-c', $supervisor, $Root, $Node, $entry) `
        -WorkingDirectory $Root -Environment @{ CARGO_NET_OFFLINE = 'true' } -TimeoutMilliseconds $TimeoutMilliseconds
    Write-ReleaseProcessOutput $result
    if ($result.ExitCode -ne 0) { throw "Tauri build failed with exit code $($result.ExitCode)" }
}

function New-ReleaseSignerStartInfo {
    param([string]$Node, [string]$Cli, [string]$Archive, [string]$KeyPath, [string]$Password)
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $Node
    $start.Arguments = (@($Cli, 'signer', 'sign', $Archive) | ForEach-Object { ConvertTo-ReleaseProcessArgument $_ }) -join ' '
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardInput = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($name in @($start.EnvironmentVariables.Keys)) {
        if ($name -match '(?i)KEY|PASSWORD|TOKEN|SECRET' -or $name -match '^(?i:TAURI_|VERSIONS_SIGNING_|SIGNPATH_)') {
            $start.EnvironmentVariables.Remove($name)
        }
    }
    $start.EnvironmentVariables['TAURI_SIGNING_PRIVATE_KEY_PATH'] = $KeyPath
    $start.EnvironmentVariables['TAURI_SIGNING_PRIVATE_KEY_PASSWORD'] = $Password
    $start
}

function Invoke-ReleaseArchiveSigning {
    param([string]$Archive, [string]$ExpectedVersion, [string]$Root,
          [string]$KeyPath, [string]$Password = '', [string]$Node = '', [string]$Cli = '', [int]$TimeoutMilliseconds = 120000)
    foreach ($suffix in @('', '.sig', '.signature.b64')) { Assert-ReleaseSigningPath ($Archive + $suffix) }
    Assert-ReleaseUpdateExecutable -Archive $Archive -ExpectedVersion $ExpectedVersion
    if (-not $KeyPath -or -not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) {
        throw 'An explicit signing key path is required'
    }
    if (-not $Node) { $Node = (Get-Command node -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source }
    if (-not $Cli) { $Cli = Join-Path $Root 'node_modules\@tauri-apps\cli\tauri.js' }
    if (-not (Test-Path -LiteralPath $Cli -PathType Leaf)) { throw 'Release signer CLI is missing' }
    $start = New-ReleaseSignerStartInfo -Node $Node -Cli $Cli -Archive ([IO.Path]::GetFullPath($Archive)) -KeyPath $KeyPath -Password $Password
    foreach ($suffix in @('.sig', '.signature.b64')) {
        $target = [IO.Path]::GetFullPath($Archive + $suffix)
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
    }
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $start
    try {
        try { $null = $process.Start() } catch { throw 'Release signer failed to start' }
        $process.StandardInput.Close()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutMilliseconds)) {
            $process.Kill()
            $process.WaitForExit()
            throw 'Release signer timed out'
        }
        $null = $stdout.GetAwaiter().GetResult()
        $null = $stderr.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0) { throw "Release signer failed (exit $($process.ExitCode))" }
    } finally { $process.Dispose() }
    if (-not (Test-Path -LiteralPath "$Archive.sig" -PathType Leaf)) { throw 'Release signer returned no signature' }
    $signature = [IO.File]::ReadAllText("$Archive.sig").Trim()
    if (-not $signature) { throw 'Release signer returned an empty signature' }
    [IO.File]::WriteAllText("$Archive.signature.b64", $signature, (New-Object Text.UTF8Encoding($false)))
}
