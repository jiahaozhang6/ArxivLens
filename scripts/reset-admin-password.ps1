[CmdletBinding()]
param(
    [string]$Username = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $ProjectRoot "backend"
$Python = Join-Path $BackendRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Backend environment is missing. Run .\scripts\setup.ps1 first."
}

$Arguments = @("-m", "app.admin_cli", "reset-password")
if ($Username) {
    $Arguments += @("--username", $Username)
}
Push-Location $BackendRoot
try {
    & $Python @Arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
