[CmdletBinding()]
param(
    [switch]$SkipFrontend
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $ProjectRoot "backend"
$FrontendRoot = Join-Path $ProjectRoot "frontend"
$EnvPath = Join-Path $ProjectRoot ".env"
$Python = Join-Path $BackendRoot ".venv\Scripts\python.exe"

function Assert-LastExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

$Uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $Uv) {
    throw "uv is required. Install it from https://docs.astral.sh/uv/ and rerun this script."
}

if (-not $SkipFrontend) {
    $Node = Get-Command node.exe -ErrorAction SilentlyContinue
    $Npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $Node -or -not $Npm) {
        throw "Node.js 20 or newer is required to build the frontend."
    }
    $NodeVersion = (& $Node.Source --version).TrimStart("v")
    if ([int]($NodeVersion.Split(".")[0]) -lt 20) {
        throw "Node.js 20 or newer is required; found $NodeVersion."
    }
}

New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "logs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "backups") | Out-Null

if (-not (Test-Path -LiteralPath $EnvPath)) {
    $Template = [IO.File]::ReadAllText((Join-Path $ProjectRoot ".env.example"))
    $SecretBytes = New-Object byte[] 32
    $Generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $Generator.GetBytes($SecretBytes)
    }
    finally {
        $Generator.Dispose()
    }
    $Secret = -join ($SecretBytes | ForEach-Object { $_.ToString("x2") })
    $Content = $Template.Replace("replace-with-a-long-random-string", $Secret)
    [IO.File]::WriteAllText($EnvPath, $Content, (New-Object Text.UTF8Encoding($false)))
    Write-Host "Created .env with a random SECRET_KEY. Keep this file stable and private."
}

Push-Location $BackendRoot
try {
    & $Uv.Source sync --frozen --group dev
    Assert-LastExitCode "Backend dependency installation"
    & $Python -m alembic upgrade head
    Assert-LastExitCode "Database migration"
}
finally {
    Pop-Location
}

if (-not $SkipFrontend) {
    Push-Location $FrontendRoot
    try {
        & $Npm.Source ci
        Assert-LastExitCode "Frontend dependency installation"
        & $Npm.Source run build
        Assert-LastExitCode "Frontend production build"
    }
    finally {
        Pop-Location
    }
}

Write-Host "Setup complete. Start the API with .\scripts\start-api.ps1 and the worker with .\scripts\start-worker.ps1."
Write-Host "Reader: http://127.0.0.1:8000/#/"
Write-Host "Admin:  http://127.0.0.1:8000/#/admin/daily"
