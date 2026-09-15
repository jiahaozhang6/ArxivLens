[CmdletBinding()]
param(
    [string]$LogPath = ""
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
    if ($LogPath) {
        if (-not [IO.Path]::IsPathRooted($LogPath)) {
            $LogPath = Join-Path $ProjectRoot $LogPath
        }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogPath) | Out-Null
        & $Python -m app.api *>&1 | Tee-Object -FilePath $LogPath -Append
    }
    else {
        & $Python -m app.api
    }
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
