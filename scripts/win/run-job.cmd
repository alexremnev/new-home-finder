@echo off
REM Runs one worker job and appends to a log. Usage: run-job.cmd ingest | drain
REM
REM Exists because the obvious form — putting the whole command in the task's
REM /TR with `>> logs\name.log` — fails before the worker starts if `logs` does
REM not exist, and `logs` is gitignored so on any fresh checkout it does not.
REM cmd then returns 1, Task Scheduler records a failure, and the log that would
REM have explained it is the thing that could not be created.
REM
REM The venv's python is called directly rather than through `uv`: Task Scheduler
REM runs with a different PATH from an interactive shell, and `uv` usually lives in
REM %USERPROFILE%\.local\bin, which is not on it. Nothing here depends on PATH.

setlocal
if "%~1"=="" (
  echo Usage: run-job.cmd ingest ^| drain
  exit /b 2
)
set JOB=%~1

REM Resolved from this file's own location, so moving the checkout does not break
REM the task: %~dp0 is scripts\win\, two levels up is the project root.
for %%I in ("%~dp0..\..") do set PROJECT=%%~fI
set PY=%PROJECT%\.venv\Scripts\python.exe
set LOGDIR=%PROJECT%\logs

if not exist "%PY%" (
  echo Virtualenv missing: "%PY%"  -- run: uv sync --extra ingest --extra dev
  exit /b 1
)
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
cd /d "%PROJECT%" || exit /b 1

REM %DATE% is locale-dependent, so the parts are reordered into a name that sorts
REM chronologically whatever the regional settings are.
for /f "tokens=1-3 delims=/-. " %%a in ("%DATE%") do set TODAY=%%c-%%b-%%a
set LOG=%LOGDIR%\%JOB%-%TODAY%.log

echo ==== START %DATE% %TIME% %JOB% >> "%LOG%"
"%PY%" -m worker %JOB% --trigger schedule >> "%LOG%" 2>&1
set CODE=%ERRORLEVEL%
echo ==== END   %DATE% %TIME% exit %CODE% >> "%LOG%"
exit /b %CODE%
