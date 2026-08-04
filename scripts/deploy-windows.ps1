$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Resolve-Uv {
    $Uv = $env:UV_EXE
    if ($Uv -and (Get-Command $Uv -ErrorAction SilentlyContinue)) {
        return $Uv
    }

    $Existing = Get-Command "uv" -ErrorAction SilentlyContinue
    if ($Existing) {
        return $Existing.Source
    }

    Write-Host "uv not found. Installing uv for the current user..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression

    $CandidateDirs = @(
        (Join-Path $HOME ".local\bin"),
        (Join-Path $HOME ".cargo\bin")
    )
    foreach ($Dir in $CandidateDirs) {
        if ((Test-Path $Dir) -and ($env:Path -notlike "*$Dir*")) {
            $env:Path = "$Dir;$env:Path"
        }
    }

    $Installed = Get-Command "uv" -ErrorAction SilentlyContinue
    if (-not $Installed) {
        throw "uv installation finished, but uv was not found in PATH. Open a new PowerShell window and rerun this script."
    }
    return $Installed.Source
}

$Uv = Resolve-Uv
$env:UV_EXE = $Uv

Write-Host "Syncing Python dependencies..."
& $Uv sync

Write-Host "Creating config.json if needed..."
& $Uv run lab-monitor --config config.json init

$StopScript = Join-Path $PSScriptRoot "stop-background.ps1"
$StartScript = Join-Path $PSScriptRoot "start-background.ps1"

Write-Host "Restarting Lab Monitor background service..."
& powershell -ExecutionPolicy Bypass -File $StopScript
& powershell -ExecutionPolicy Bypass -File $StartScript

Write-Host ""
Write-Host "Lab Monitor is deployed."
Write-Host "Dashboard: http://127.0.0.1:8765"
