@echo off
REM Runs one worker tick and appends to a log.
REM
REM `tick` rather than `hot`, deliberately: what runs, how often, and in which
REM hours is decided by the `schedules` table, so changing the timetable is an
REM UPDATE and not an edit to this file or to Task Scheduler. If nothing is due
REM the tick costs one cheap query and exits.
REM
REM Task Scheduler discards stdout, so everything is appended to a log and the
REM exit code is preserved for the task's "Last Run Result" column.

setlocal
set PROJECT=D:\projects\new-home-finder
set LOGDIR=%PROJECT%\logs

if not exist "%LOGDIR%" mkdir "%LOGDIR%"
cd /d "%PROJECT%" || exit /b 1

for /f "tokens=1-3 delims=/-. " %%a in ("%DATE%") do set TODAY=%%c-%%b-%%a
set LOG=%LOGDIR%\worker-%TODAY%.log

echo. >> "%LOG%"
echo ==== %DATE% %TIME% tick >> "%LOG%"
call uv run python -m worker tick >> "%LOG%" 2>&1
set CODE=%ERRORLEVEL%
echo ==== exit %CODE% >> "%LOG%"
exit /b %CODE%
