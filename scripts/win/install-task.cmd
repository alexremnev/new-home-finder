@echo off
REM Registers the scheduled task. Run once, from an elevated prompt.
REM
REM Every 10 minutes, all day. The window and the interval live in `schedules`
REM (quiet_hours 19:00-10:00, interval_seconds 3600), so a frequent tick that
REM usually does nothing is what makes those settings changeable without touching
REM Windows. A once-a-day task could not honour an hourly schedule at all.

schtasks /Create ^
  /TN "LondonRentAlerts worker" ^
  /TR "\"%~dp0run-worker.cmd\"" ^
  /SC MINUTE /MO 10 ^
  /ST 00:00 ^
  /RL LIMITED ^
  /F

echo.
echo Registered. Useful commands:
echo   schtasks /Run    /TN "LondonRentAlerts worker"
echo   schtasks /Query  /TN "LondonRentAlerts worker" /V /FO LIST
echo   schtasks /Change /TN "LondonRentAlerts worker" /DISABLE
echo   schtasks /Delete /TN "LondonRentAlerts worker" /F
