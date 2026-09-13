@echo off
REM Install the scheduled tasks. Run once, from an elevated prompt.
REM
REM   scripts\win\install-task.cmd
REM
REM Three tasks, not one: ingest reads the feed, drain sends what it queued, and
REM report checks for silence. Separate because their failure modes are, and drain
REM is offset by two minutes so a batch goes out in the same cycle it was queued in
REM rather than waiting for the next.

setlocal
for %%I in ("%~dp0..\..") do set PROJECT=%%~fI
set RUN="%PROJECT%\scripts\win\run-job.cmd"

schtasks /Create /F /RL LIMITED /SC MINUTE /MO 5 /ST 00:00 ^
  /TN "home ingest" /TR "%RUN% ingest"

schtasks /Create /F /RL LIMITED /SC MINUTE /MO 5 /ST 00:02 ^
  /TN "home drain" /TR "%RUN% drain"

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
