@echo off
REM Double-click this to remove the hourly scheduled task.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Unregister-ScheduledTask -TaskName 'LitterRobotHourly' -Confirm:$false -ErrorAction SilentlyContinue; Unregister-ScheduledTask -TaskName 'LitterRobotOvernight' -Confirm:$false -ErrorAction SilentlyContinue; Write-Host 'Removed Litter Robot scheduled task(s).'"
echo.
pause
