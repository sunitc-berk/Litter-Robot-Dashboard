@echo off
REM Double-click this to register the task that runs the monitor every hour.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0schedule_litter_robot.ps1"
echo.
pause
