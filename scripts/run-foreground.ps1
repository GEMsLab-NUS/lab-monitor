$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Uv = $env:UV_EXE
if (-not $Uv) {
    $Uv = "uv"
}

& $Uv run lab-monitor --config config.json run
