param(
  [switch]$Dev,
  [int]$Port = 1421,
  [string]$Python = "",
  [switch]$NoAuth
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = if ($Python) { $Python } elseif ($env:MEDIAFORGE_PYTHON) { $env:MEDIAFORGE_PYTHON } else { (Get-Command python -ErrorAction Stop).Source }
$dataRoot = if ($Dev) { Join-Path $root ".dev-data" } else { Join-Path $env:USERPROFILE ".mediaforge" }
$bridgeArgs = @(
  (Join-Path $root "python\agent_bridge.py"),
  "--port", "$Port",
  "--data-root", $dataRoot,
  "--pair-file", (Join-Path $dataRoot "mcp-bridge.json")
)
if ($NoAuth) { $bridgeArgs += "--no-auth" }

Write-Host "MediaForge MCP bridge"
Write-Host "  python: $py"
Write-Host "  data:   $dataRoot"
Write-Host "  url:    http://127.0.0.1:$Port/mcp"
Write-Host "  token:  $dataRoot\mcp-bridge.json"
& $py @bridgeArgs
