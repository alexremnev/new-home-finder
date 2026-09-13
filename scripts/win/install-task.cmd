@echo off
REM Install the scheduled tasks. Run once, from an elevated prompt.
REM
REM   scripts\win\install-task.cmd
REM
REM Four tasks, not one. ingest reads the feed, drain sends what it queued, rollup
REM totals the day into daily_stats so the console reads counts instead of computing
REM them, and report checks for silence.
REM
REM The offsets matter: drain at +2 so a batch goes out in the cycle it was queued
REM in rather than waiting for the next, and rollup at +3 so it counts a delivery
REM that has already happened rather than one about to.

setlocal
for %%I in ("%~dp0..\..") do set PROJECT=%%~fI
set RUN="%PROJECT%\scripts\win\run-job.cmd"

schtasks /Create /F /RL LIMITED /SC MINUTE /MO 5 /ST 00:00 ^
  /TN "home ingest" /TR "%RUN% ingest"

schtasks /Create /F /RL LIMITED /SC MINUTE /MO 5 /ST 00:02 ^
  /TN "home drain" /TR "%RUN% drain"

schtasks /Create /F /RL LIMITED /SC MINUTE /MO 5 /ST 00:03 ^
  /TN "home rollup" /TR "%RUN% rollup"

schtasks /Create /F /RL LIMITED /SC HOURLY /MO 1 /ST 00:20 ^
  /TN "home report" /TR "cmd /c cd /d \"%PROJECT%\" && .venv\Scripts\python.exe scripts\report.py >> logs\report.log 2>&1"

echo.
echo Installed. Two settings still have to be ticked by hand in taskschd.msc,
echo for each task, because schtasks cannot set them:
echo.
echo   Conditions -^> clear "Start the task only if the computer is on AC power"
echo   Settings   -^> tick  "Run task as soon as possible after a scheduled start is missed"
echo.
echo Without the first, a laptop on battery runs nothing and the logs stay empty —
echo no error, just silence.
