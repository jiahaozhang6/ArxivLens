[CmdletBinding()]
param(
    [string]$Output = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $ProjectRoot "backend"
$Python = Join-Path $BackendRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Backend environment is missing. Run .\scripts\setup.ps1 first."
}

Push-Location $BackendRoot
try {
    if ($Output) {
        & $Python -m app.maintenance backup --output $Output
    }
    else {
        & $Python -m app.maintenance backup
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Database backup failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
