$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$script = Join-Path $root "run_tracker.cmd"

$action = New-ScheduledTaskAction -Execute $script -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 06:30
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName "ProjectTracker" -Action $action -Trigger $trigger -Settings $settings -Description "Local project deadline tracker" -Force | Out-Null

Write-Output "Registered scheduled task 'ProjectTracker' for the current user: weekdays 06:30, runs $script"
Write-Output 'Remove it with: Unregister-ScheduledTask -TaskName "ProjectTracker" -Confirm:$false'
