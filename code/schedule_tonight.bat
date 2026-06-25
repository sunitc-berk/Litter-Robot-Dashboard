@echo off
REM Double-click this to register the task that runs the monitor every hour.
REM Optional: pass -WorkDir "X:\path\to\repo" to target a copy in another folder.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0schedule_litter_robot.ps1" %*
echo.
pause
