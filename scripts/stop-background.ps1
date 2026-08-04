$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $Root ".lab-monitor.pid"
if (-not (Test-Path $PidFile)) {
    Write-Host "No PID file found."
    exit 0
}

$PidValue = Get-Content $PidFile | Select-Object -First 1
if (-not $PidValue) {
    Remove-Item $PidFile -Force
    Write-Host "Empty PID file removed."
    exit 0
}

$TargetPid = [int]$PidValue
$ChildPids = @()
$Queue = @($TargetPid)
while ($Queue.Count -gt 0) {
    $ParentPid = $Queue[0]
    $Queue = @($Queue | Select-Object -Skip 1)
    $Children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $ParentPid" -ErrorAction SilentlyContinue
    foreach ($Child in $Children) {
        $ChildPids += [int]$Child.ProcessId
        $Queue += [int]$Child.ProcessId
    }
}

foreach ($ChildPid in ($ChildPids | Sort-Object -Descending)) {
    Stop-Process -Id $ChildPid -Force -ErrorAction SilentlyContinue
}

$Process = Get-Process -Id $TargetPid -ErrorAction SilentlyContinue
if ($Process) {
    Stop-Process -Id $Process.Id
    Write-Host "Stopped lab-monitor PID $($Process.Id)"
} else {
    Write-Host "Process $PidValue is not running."
}
Remove-Item $PidFile -Force
