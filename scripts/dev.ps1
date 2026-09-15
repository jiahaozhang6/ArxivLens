[CmdletBinding()]
param(
    [string]$FrontendHost = "0.0.0.0",
    [int]$FrontendPort = 5173
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $ProjectRoot "backend"
$FrontendRoot = Join-Path $ProjectRoot "frontend"
$LogsRoot = Join-Path $ProjectRoot "logs"
$Python = Join-Path $BackendRoot ".venv\Scripts\python.exe"
$Npm = Get-Command npm.cmd -ErrorAction SilentlyContinue

if (-not (Test-Path -LiteralPath $Python) -or -not $Npm) {
    throw "Dependencies are missing. Run .\scripts\setup.ps1 first."
}

New-Item -ItemType Directory -Force -Path $LogsRoot | Out-Null
$ApiProcess = Start-Process -FilePath $Python -ArgumentList @("-m", "app.api") -WorkingDirectory $BackendRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $LogsRoot "api-dev.out.log") -RedirectStandardError (Join-Path $LogsRoot "api-dev.err.log") -PassThru
$WorkerProcess = Start-Process -FilePath $Python -ArgumentList @("-m", "app.worker") -WorkingDirectory $BackendRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $LogsRoot "worker-dev.out.log") -RedirectStandardError (Join-Path $LogsRoot "worker-dev.err.log") -PassThru
try {
    Start-Sleep -Seconds 2
    if ($ApiProcess.HasExited) {
        throw "API failed to start. Check logs\api-dev.err.log."
    }
    if ($WorkerProcess.HasExited) {
        throw "Worker failed to start. Check logs\worker-dev.err.log."
    }

    $DisplayHost = $FrontendHost
    if ($FrontendHost -eq "0.0.0.0") {
        $DisplayHost = Get-NetIPConfiguration |
            Where-Object { $_.NetAdapter.Status -eq "Up" -and $_.IPv4DefaultGateway } |
            ForEach-Object {
                $_.IPv4Address |
                    Where-Object { $_.IPAddress -notlike "169.254.*" } |
                    Select-Object -First 1 -ExpandProperty IPAddress
            } |
            Select-Object -First 1
        if (-not $DisplayHost) {
            $DisplayHost = "127.0.0.1"
        }
    }

    Write-Host "Reader (local): http://127.0.0.1:$FrontendPort/#/"
    Write-Host "Reader (LAN):   http://${DisplayHost}:$FrontendPort/#/"
    Write-Host "Admin (LAN):    http://${DisplayHost}:$FrontendPort/#/admin/daily"
    Write-Host "API (LAN):      http://${DisplayHost}:8000"
    Write-Host "Backend logs are in $LogsRoot. Press Ctrl+C to stop all development processes."
    Push-Location $FrontendRoot
    try {
        & $Npm.Source run dev -- --host $FrontendHost --port $FrontendPort
    }
    finally {
        Pop-Location
    }
}
finally {
    foreach ($Process in @($ApiProcess, $WorkerProcess)) {
        & "$env:SystemRoot\System32\taskkill.exe" /PID $Process.Id /T /F 2>$null | Out-Null
    }
}
