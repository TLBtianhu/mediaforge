$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = if ($env:MEDIAFORGE_PYTHON) { $env:MEDIAFORGE_PYTHON } else { (Get-Command python -ErrorAction Stop).Source }
& $python (Join-Path $root "scripts\smoke_mcp.py")
