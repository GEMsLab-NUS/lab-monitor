param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Uv = $env:UV_EXE
if (-not $Uv) {
    $Uv = "uv"
}

$StopScript = Join-Path $PSScriptRoot "stop-background.ps1"
$StartScript = Join-Path $PSScriptRoot "start-background.ps1"

if ($DryRun) {
    Write-Host "Previewing invalid roster/head records. No service restart or database backup will be performed."
    & $Uv run lab-monitor --config config.json clean-roster --dry-run --include-low-evidence --min-total-seconds 20
    exit $LASTEXITCODE
}

Write-Host "Stopping background service..."
& powershell -ExecutionPolicy Bypass -File $StopScript

$DatabasePath = Join-Path $Root "data\lab_monitor.sqlite3"
if (Test-Path $DatabasePath) {
    $BackupDir = Join-Path $Root "data\backups"
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    $Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $BackupPath = Join-Path $BackupDir "lab_monitor.before-head-clean.$Timestamp.sqlite3"
    Copy-Item -LiteralPath $DatabasePath -Destination $BackupPath -Force
    foreach ($Suffix in @("-wal", "-shm")) {
        $Sidecar = "$DatabasePath$Suffix"
        if (Test-Path $Sidecar) {
            Copy-Item -LiteralPath $Sidecar -Destination "$BackupPath$Suffix" -Force
        }
    }
    Write-Host "Database backup created: $BackupPath"
} else {
    Write-Host "No database found. Cleanup will run against a new/empty store."
}

Write-Host "Cleaning invalid roster/head records..."
& $Uv run lab-monitor --config config.json clean-roster --include-low-evidence --min-total-seconds 20

Write-Host "Restarting background service..."
& powershell -ExecutionPolicy Bypass -File $StartScript

Write-Host ""
Write-Host "Head data cleanup complete."
Write-Host "Dashboard: http://127.0.0.1:8765/roster"
