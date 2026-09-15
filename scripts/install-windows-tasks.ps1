[CmdletBinding()]
param(
    [switch]$NoStart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "This script is only for Windows."
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot "backend\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Backend environment is missing. Run .\scripts\setup.ps1 first."
}

$PowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
$CurrentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$Principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

$ApiScript = Join-Path $PSScriptRoot "start-api.ps1"
$WorkerScript = Join-Path $PSScriptRoot "start-worker.ps1"
$ApiLog = Join-Path $ProjectRoot "logs\api.log"
$WorkerLog = Join-Path $ProjectRoot "logs\worker.log"
$ApiArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ApiScript`" -LogPath `"$ApiLog`""
$WorkerArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$WorkerScript`" -LogPath `"$WorkerLog`""
$ApiAction = New-ScheduledTaskAction -Execute $PowerShell -Argument $ApiArgs -WorkingDirectory $ProjectRoot
$WorkerAction = New-ScheduledTaskAction -Execute $PowerShell -Argument $WorkerArgs -WorkingDirectory $ProjectRoot
$ApiTrigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
$WorkerTrigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
$WorkerTrigger.Delay = "PT15S"

Register-ScheduledTask -TaskName "ArxivLens-API" -Action $ApiAction -Trigger $ApiTrigger -Settings $Settings -Principal $Principal -Description "ArxivLens FastAPI service" -Force | Out-Null
Register-ScheduledTask -TaskName "ArxivLens-Worker" -Action $WorkerAction -Trigger $WorkerTrigger -Settings $Settings -Principal $Principal -Description "ArxivLens daily arXiv scheduler" -Force | Out-Null
if (-not $NoStart) {
    Start-ScheduledTask -TaskName "ArxivLens-API"
    Start-Sleep -Seconds 3
    Start-ScheduledTask -TaskName "ArxivLens-Worker"
}

if ($NoStart) {
    Write-Host "Installed ArxivLens-API and ArxivLens-Worker for $CurrentUser; they will start at the next logon."
}
else {
    Write-Host "Installed and started ArxivLens-API and ArxivLens-Worker for $CurrentUser."
}
Write-Host "Logs: $ApiLog and $WorkerLog"
