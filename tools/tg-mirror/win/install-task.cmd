@echo off
REM Registers the hourly task by handing over to install-task.ps1, which is where
REM the work is: the settings that let the task run while nobody is logged in, and
REM wake the machine from sleep, are reachable from the ScheduledTasks cmdlets and
REM not from schtasks.
REM
REM This wrapper exists so the thing stays double-clickable, and so the execution
REM policy is bypassed for this one file rather than changed for the machine.
REM
REM The script asks for your Windows password — Windows needs it to log the account
REM in unattended — and elevates itself, because storing a task credential is an
REM administrative act.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-task.ps1"

if errorlevel 1 (
  echo.
  echo install-task.ps1 reported a failure. Nothing was registered.
)

echo.
pause
