# Registers a Windows Scheduled Task that runs litter_robot_v1.py once an hour,
# indefinitely, and keeps running whether or not you are logged on.
#
# Run it via schedule_tonight.bat (double-click). The script will:
#   1. request administrator rights (needed to register an unattended task), and
#   2. prompt for your Windows password (needed so it can run while signed out).
# Credentials for the Whisker account come from the machine-level WHISKER_*
# environment variables (see README), not from this file.
#
# The project location is auto-detected from this script's folder, so it works
# wherever you keep the repo. To target a copy elsewhere, pass -WorkDir, e.g.:
#   schedule_tonight.bat -WorkDir "D:\path\to\Litter-Robot-Dashboard"

param(
    # Project root: the folder that contains code\, live_logs\, and .venv\.
    # Defaults to this script's parent folder (the repo root).
    [string]$WorkDir = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"

# --- Re-launch elevated if needed ---
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Requesting administrator rights..." -ForegroundColor Yellow
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -WorkDir `"$WorkDir`""
    exit
}

$py       = "$WorkDir\.venv\Scripts\python.exe"   # the project's virtual environment
$script   = "$WorkDir\code\litter_robot_v1.py"
$logdir   = "$WorkDir\live_logs"
$taskName = "LitterRobotHourly"

if (-not (Test-Path $py)) {
    Write-Host "WARNING: virtual environment Python not found at:" -ForegroundColor Yellow
    Write-Host "  $py" -ForegroundColor Yellow
    Write-Host "Create the virtual environment and install requirements first:" -ForegroundColor Yellow
    Write-Host "  python -m venv `"$WorkDir\.venv`"" -ForegroundColor Yellow
    Write-Host "  `"$py`" -m pip install -r `"$WorkDir\requirements.txt`"" -ForegroundColor Yellow
}
if (-not (Test-Path $script)) {
    throw "Script not found: $script"
}

# Remove the old one-night task if it's still registered, so we don't leave a duplicate.
Unregister-ScheduledTask -TaskName "LitterRobotOvernight" -Confirm:$false -ErrorAction SilentlyContinue

$action  = New-ScheduledTaskAction -Execute $py -Argument "`"$script`" --log-dir `"$logdir`"" -WorkingDirectory $WorkDir

# Start now, then repeat every hour forever. Omitting -RepetitionDuration means
# "repeat indefinitely" on Windows 10/11.
$start   = (Get-Date)
$trigger = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Hours 1)

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
             -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

# --- Run whether the user is logged on or not (stores your password with the task) ---
Write-Host ""
Write-Host "Enter your Windows password so the task can run while you're signed out." -ForegroundColor Cyan
$cred = Get-Credential -UserName "$env:USERDOMAIN\$env:USERNAME" -Message "Account to run the Litter Robot task (runs whether logged on or not)"
if (-not $cred) { throw "Cancelled - no credentials entered. Task not created." }

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -User $cred.UserName -Password $cred.GetNetworkCredential().Password `
    -Description "Runs litter_robot_v1.py every hour, indefinitely (whether logged on or not)." -Force | Out-Null

Write-Host ""
Write-Host "Scheduled task '$taskName' created." -ForegroundColor Green
Write-Host "Runs every hour, indefinitely, whether or not you are logged on."
Write-Host "First run : $($start.ToString('ddd MMM d, h:mm tt')) (then hourly)"
Write-Host ""
Write-Host "To remove it: double-click uninstall_schedule.bat"
Read-Host "Press Enter to close"
