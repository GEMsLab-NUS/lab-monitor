$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Uv = $env:UV_EXE
if (-not $Uv) {
    $Uv = "uv"
}
$AppExe = Join-Path $Root ".venv\Scripts\lab-monitor.exe"

Set-Location $Root
$PidFile = Join-Path $Root ".lab-monitor.pid"

if (Test-Path $PidFile) {
    $ExistingPid = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($ExistingPid) {
        $ExistingProcess = Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue
        if ($ExistingProcess) {
            Write-Host "Lab Monitor is already running with PID $ExistingPid"
            Write-Host "Dashboard: http://127.0.0.1:8765"
            exit 0
        }
    }
}

& $Uv sync
if (-not (Test-Path $AppExe)) {
    throw "Application entrypoint not found after uv sync: $AppExe"
}

$LogDir = Join-Path $Root "data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$OutLog = Join-Path $LogDir "lab-monitor.out.log"
$ErrLog = Join-Path $LogDir "lab-monitor.err.log"

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
