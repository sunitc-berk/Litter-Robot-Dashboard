@echo off
REM Double-click to dump the scheduled-task status to schedule_diag.txt
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='SilentlyContinue';" ^
  "$out='%~dp0schedule_diag.txt';" ^
  "$t=Get-ScheduledTask -TaskName 'LitterRobotHourly';" ^
  "if(-not $t){ 'TASK NOT FOUND - it was never created (the .bat may not have been run).' | Out-File $out; Start-Process notepad $out; exit }" ^
  "$i=Get-ScheduledTaskInfo -TaskName 'LitterRobotHourly';" ^
  "$lines=@();" ^
  "$lines+='Task State        : '+$t.State;" ^
  "$lines+='Last Run Time     : '+$i.LastRunTime;" ^
  "$lines+='Last Result(code) : '+('0x{0:X}' -f $i.LastTaskResult);" ^
  "$lines+='Next Run Time     : '+$i.NextRunTime;" ^
  "$lines+='Missed Runs       : '+$i.NumberOfMissedRuns;" ^
  "$lines+='';" ^
  "$lines+='--- Triggers ---';" ^
  "$lines+=($t.Triggers | Format-List * | Out-String);" ^
  "$lines+='--- Settings (wake/battery/idle) ---';" ^
  "$lines+=($t.Settings | Select-Object WakeToRun,DisallowStartIfOnBatteries,StopIfGoingOnBatteries,StartWhenAvailable,Enabled,ExecutionTimeLimit | Format-List * | Out-String);" ^
  "$lines+='--- Principal (run-as / logon) ---';" ^
  "$lines+=($t.Principal | Format-List * | Out-String);" ^
  "$lines+='--- Last wake source ---';" ^
  "$lines+=(powercfg /lastwake | Out-String);" ^
  "$lines | Out-File $out -Encoding utf8;" ^
  "Start-Process notepad $out"
