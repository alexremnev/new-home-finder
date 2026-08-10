@echo off
REM One pass of the mirror, appended to a log.
REM
REM `once` rather than `watch`: Task Scheduler owns the timetable, so an hourly
REM task that connects, forwards what is new, and exits fits it. `watch` would
REM need a process kept alive across sleeps and logouts, which is what a scheduler
REM is for in the first place.
REM
REM Paths are derived from this file's own location (%~dp0 is the win\ folder, so
REM %~dp0.. is tools\tg-mirror). Nothing is hard-coded, so moving or renaming the
REM checkout does not break the task.
REM
REM Task Scheduler discards stdout, so everything is appended to a log and the
REM exit code is preserved for the task's "Last Run Result" column: 0 is a clean
REM pass, 1 means the log says why.

setlocal
REM Resolved to a full path rather than left as "...\win\..", so the log lines and
REM any error message name a directory that can be pasted straight into a prompt.
for %%I in ("%~dp0..") do set MIRROR=%%~fI
set PY=%MIRROR%\.venv\Scripts\python.exe
set LOGDIR=%MIRROR%\logs

if not exist "%PY%" (
  echo Virtualenv missing: "%PY%"
  echo Create it once:
  echo   cd /d "%MIRROR%"
  echo   py -3 -m venv .venv
  echo   .venv\Scripts\pip install -r requirements.txt
  exit /b 1
)

if not exist "%LOGDIR%" mkdir "%LOGDIR%"
cd /d "%MIRROR%" || exit /b 1

REM %DATE% is locale-dependent, so the day, month and year are reordered into a
REM name that sorts chronologically whatever the regional settings are.
for /f "tokens=1-3 delims=/-. " %%a in ("%DATE%") do set TODAY=%%c-%%b-%%a
set LOG=%LOGDIR%\mirror-%TODAY%.log

echo. >> "%LOG%"
echo ==== %DATE% %TIME% once >> "%LOG%"
"%PY%" mirror.py once >> "%LOG%" 2>&1
set CODE=%ERRORLEVEL%
echo ==== exit %CODE% >> "%LOG%"
exit /b %CODE%
