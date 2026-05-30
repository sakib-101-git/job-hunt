# Registers a Windows Scheduled Task that runs the JobHunt cycle twice a day (9 AM and 9 PM).
# Run once:     powershell -ExecutionPolicy Bypass -File register_task.ps1
# Remove with:  Unregister-ScheduledTask -TaskName JobHunt -Confirm:$false

$ErrorActionPreference = "Stop"

$projectDir = $PSScriptRoot
$python = Join-Path $projectDir ".venv\Scripts\python.exe"
$taskName = "JobHunt"

if (-not (Test-Path $python)) {
    Write-Error "venv python not found at $python - create the venv first."
    exit 1
}

$action = New-ScheduledTaskAction -Execute $python -Argument "-m src.main" -WorkingDirectory $projectDir

# Two daily triggers: 9 AM and 9 PM.
$triggerMorning = New-ScheduledTaskTrigger -Daily -At 9am
$triggerEvening = New-ScheduledTaskTrigger -Daily -At 9pm

# Catch up if the PC was asleep/off; allow running on battery.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

# Remove any prior copy so re-running this script is idempotent.
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggerMorning, $triggerEvening -Settings $settings -Description "Scrape remote entry-level jobs and send Telegram alerts twice daily."

Write-Output "Registered scheduled task: $taskName (runs 9 AM and 9 PM daily)."
