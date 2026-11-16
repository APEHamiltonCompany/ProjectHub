$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    $found = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if (-not $found) { throw "Run setup.cmd first: no .venv\Scripts\pythonw.exe and no pythonw.exe on PATH." }
    $pythonw = $found.Source
}
$user = "$env:USERDOMAIN\$env:USERNAME"

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "-m tracker.hub" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName "ProjectHub" -Action $action -Trigger $trigger -Settings $settings -Description "Local project hub web app" -Force | Out-Null

Write-Output "Registered scheduled task 'ProjectHub' for ${user}: at logon, runs $pythonw -m tracker.hub in $root"
Write-Output 'Remove it with: Unregister-ScheduledTask -TaskName "ProjectHub" -Confirm:$false'
