$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Require-Command {
    param([string]$Name)
    $Command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $Command) {
        throw "$Name is required but was not found in PATH."
    }
    return $Command.Source
}

function Get-GitValue {
    param([string[]]$Arguments)
    $Output = & git @Arguments 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    return ($Output | Select-Object -First 1)
}

if (-not (Test-Path (Join-Path $Root ".git"))) {
    throw "This script must run from a cloned lab-monitor repository. Missing .git directory at $Root."
}

Require-Command "git" | Out-Null

$Branch = Get-GitValue @("branch", "--show-current")
if (-not $Branch) {
    throw "Cannot determine the current git branch."
}

$Upstream = Get-GitValue @("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
if (-not $Upstream) {
    $Upstream = "origin/$Branch"
}

Write-Host "Checking for updates on $Upstream..."
& git fetch origin
if ($LASTEXITCODE -ne 0) {
    throw "Failed to fetch updates from origin."
}

$LocalSha = Get-GitValue @("rev-parse", "HEAD")
$RemoteSha = Get-GitValue @("rev-parse", $Upstream)
if (-not $LocalSha -or -not $RemoteSha) {
    throw "Cannot compare local branch with $Upstream."
}

if ($LocalSha -eq $RemoteSha) {
    Write-Host "Already up to date. No service restart, database backup, or deployment step was needed."
    exit 0
}

$MergeBase = Get-GitValue @("merge-base", "HEAD", $Upstream)
if ($MergeBase -eq $RemoteSha) {
    Write-Host "Local branch is ahead of $Upstream. No remote update is needed."
    exit 0
}
if ($MergeBase -ne $LocalSha) {
    throw "Local branch and $Upstream have diverged. Resolve git history manually before running this updater."
}

$TrackedChanges = & git status --porcelain --untracked-files=no
if ($TrackedChanges) {
    Write-Host "Tracked local changes detected:"
    $TrackedChanges | ForEach-Object { Write-Host $_ }
    throw "Commit, stash, or discard tracked local changes before updating. Ignored runtime data is safe and does not block updates."
}

$ShortLocal = $LocalSha.Substring(0, 7)
$ShortRemote = $RemoteSha.Substring(0, 7)
Write-Host "Update available: $ShortLocal -> $ShortRemote"

$StopScript = Join-Path $PSScriptRoot "stop-background.ps1"
$DeployScript = Join-Path $PSScriptRoot "deploy-windows.ps1"

Write-Host "Stopping background service..."
& powershell -ExecutionPolicy Bypass -File $StopScript

$DatabasePath = Join-Path $Root "data\lab_monitor.sqlite3"
if (Test-Path $DatabasePath) {
    $BackupDir = Join-Path $Root "data\backups"
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    $Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $BackupPath = Join-Path $BackupDir "lab_monitor.$Timestamp.sqlite3"
    Copy-Item -LiteralPath $DatabasePath -Destination $BackupPath -Force
    foreach ($Suffix in @("-wal", "-shm")) {
        $Sidecar = "$DatabasePath$Suffix"
        if (Test-Path $Sidecar) {
            Copy-Item -LiteralPath $Sidecar -Destination "$BackupPath$Suffix" -Force
        }
    }
    Write-Host "Database backup created: $BackupPath"
} else {
    Write-Host "No existing database found at $DatabasePath. Skipping database backup."
}

Write-Host "Pulling latest code..."
& git pull --ff-only
if ($LASTEXITCODE -ne 0) {
    throw "git pull failed. The service is stopped; rerun deploy after resolving the git issue."
}

Write-Host "Redeploying..."
& powershell -ExecutionPolicy Bypass -File $DeployScript

Write-Host ""
Write-Host "Update complete."
Write-Host "Dashboard: http://127.0.0.1:8765"
