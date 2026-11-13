# Run the linked-bundle regression suite with the repository's isolated test runner.
$ErrorActionPreference = 'Stop'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
& node (Join-Path $root 'scripts/test-python.mjs') tests.python.test_dsh_integration_layout
exit $LASTEXITCODE
