$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Uv = $env:UV_EXE
if (-not $Uv) {
    $Uv = "uv"
}
$AppExe = Join-Path $Root ".venv\Scripts\lab-monitor.exe"

Set-Location $Root
& $Uv sync
if (-not (Test-Path $AppExe)) {
    throw "Application entrypoint not found after uv sync: $AppExe"
}

$LogDir = Join-Path $Root "data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$OutLog = Join-Path $LogDir "lab-monitor.out.log"
$ErrLog = Join-Path $LogDir "lab-monitor.err.log"
$PidFile = Join-Path $Root ".lab-monitor.pid"

$Process = Start-Process `
    -FilePath $AppExe `
    -ArgumentList @("--config", "config.json", "run") `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -PassThru

Set-Content -Path $PidFile -Value $Process.Id
Write-Host "Started lab-monitor PID $($Process.Id)"
Write-Host "Dashboard: http://127.0.0.1:8765"
