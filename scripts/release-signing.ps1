# Shared release guards. Importing this file never reads signing credentials.
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
    if (-not $Node) { $Node = (Get-Command node -CommandType Application -ErrorAction Stop).Source }
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
