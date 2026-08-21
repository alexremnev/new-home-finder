@echo off
REM Runs one worker job, logs it, and shouts if it failed.
REM
REM   run-job.cmd ingest | drain
REM
REM ── why this file exists at all ─────────────────────────────────────────────
REM
REM The obvious form — the whole command in the task's /TR with `>> logs\name.log`
REM — fails before the worker starts if `logs` does not exist, and `logs` is
REM gitignored so on any fresh checkout it does not. cmd then returns 1, Task
REM Scheduler records a failure, and the log that would have explained it is the
REM thing that could not be created.
REM
REM The venv's python is called directly rather than through `uv`: Task Scheduler
REM runs with a different PATH from an interactive shell, and `uv` usually lives in
REM %USERPROFILE%\.local\bin, which is not on it. Nothing here depends on PATH.
REM
REM ── why the alert lives here and not in scripts/report.py ───────────────────
REM
REM report.py finds problems by reading the database. So when the database is what
REM is wrong, it is silent for the same reason everything else is — and an outage
REM that lasted a day showed up nowhere except an exit code nobody was watching.
REM
REM An alert about the store cannot live in the store. This one is sent with curl
REM straight to Telegram, reads its two settings out of .env by hand, and needs
REM neither Python nor a working venv — so it still fires when those are the fault.

setlocal EnableDelayedExpansion
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

if not exist "%LOGDIR%" mkdir "%LOGDIR%"
cd /d "%PROJECT%" || exit /b 1

REM %DATE% is locale-dependent, so the parts are reordered into a name that sorts
REM chronologically whatever the regional settings are.
for /f "tokens=1-3 delims=/-. " %%a in ("%DATE%") do set TODAY=%%c-%%b-%%a
set LOG=%LOGDIR%\%JOB%-%TODAY%.log

REM This run's output goes to its own file first, then is appended to the day's
REM log. Two reasons: the failing lines can be read back without scanning a log
REM that may already hold a thousand of them, and the alert can quote the actual
REM error rather than "something went wrong".
set OUT=%TEMP%\home-%JOB%-run.out

if not exist "%PY%" (
  echo Virtualenv missing: "%PY%"  -- run: uv sync --extra ingest --extra dev> "%OUT%"
  set CODE=1
) else (
  "%PY%" -m worker %JOB% --trigger schedule > "%OUT%" 2>&1
  set CODE=!ERRORLEVEL!
)

echo ==== START %DATE% %TIME% %JOB% >> "%LOG%"
type "%OUT%" >> "%LOG%"
echo ==== END   %DATE% %TIME% exit !CODE! >> "%LOG%"

if "!CODE!"=="0" goto :done

REM ── the alert ──────────────────────────────────────────────────────────────

REM Read the two settings out of .env. `tokens=1,*` so a value containing "=" —
REM which a connection string usually does — survives intact. A commented line's
REM "key" starts with # and matches neither name, so comments need no handling.
if exist "%PROJECT%\.env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%PROJECT%\.env") do (
    if /i "%%a"=="TELEGRAM_TOKEN"    set "TG_TOKEN=%%b"
    if /i "%%a"=="TELEGRAM_OPS_CHAT" set "OPS_CHAT=%%b"
  )
)

if not defined TG_TOKEN goto :done
if not defined OPS_CHAT goto :done

REM One alert per job per hour, and no more. The task runs every five minutes, so
REM an outage lasting an afternoon would otherwise send fifty identical messages —
REM which is how an alert channel becomes one nobody reads. The flag file's name
REM carries the hour, so the throttle needs no date arithmetic and no cleanup that
REM matters: a stale flag from yesterday's 14:00 simply never matches again.
set HOUR=%TIME:~0,2%
set HOUR=%HOUR: =0%
set FLAG=%TEMP%\home-alert-%JOB%-%TODAY%-%HOUR%.flag
if exist "%FLAG%" goto :done
echo sent> "%FLAG%"

REM The message is built as a FILE and sent from that file, not interpolated into
REM the command line. Two reasons, and both bite:
REM
REM   * A traceback line is untrusted text as far as cmd is concerned. One ">" or
REM     "|" in it and the shell redirects instead of sending.
REM   * "%%0A" inside --data-urlencode arrives literally, because curl encodes the
REM     percent sign too. Newlines have to come from real newlines in a file.
REM
REM curl's "name@file" form reads the value from the file and encodes the whole
REM thing, so quotes, colons and line breaks all survive untouched.
set MSG=%TEMP%\home-alert-%JOB%.txt
> "%MSG%" echo Worker failed: %JOB% ^(exit !CODE!^) on %COMPUTERNAME% at %TIME:~0,5%
>> "%MSG%" echo.

REM The lines worth reading, chosen by findstr rather than by cmd, so no untrusted
REM text ever reaches a command line. For a Python traceback the last of these is
REM the exception and its message: the one line that says what happened.
findstr /I /C:"Error" /C:"Traceback" /C:"failed" /C:"SUMMARY" "%OUT%" >> "%MSG%" 2>nul

>> "%MSG%" echo.
>> "%MSG%" echo Log: %LOG%
>> "%MSG%" echo Further alerts for this job are held for the rest of the hour.

REM Telegram refuses a message over 4096 characters, and it refuses it quietly as
REM far as this script is concerned — curl is silent and the alert simply never
REM arrives. That is the worst outcome available: the one run that produced pages of
REM errors is the one that says nothing.
REM
REM So the size is checked and an oversized message is replaced by a short one that
REM points at the log. Bounded by bytes rather than by picking lines, because
REM picking lines means putting untrusted text back on a command line.
for %%A in ("%MSG%") do set MSGSIZE=%%~zA
if !MSGSIZE! GTR 3500 (
  > "%MSG%" echo Worker failed: %JOB% ^(exit !CODE!^) on %COMPUTERNAME% at %TIME:~0,5%
  >> "%MSG%" echo.
  >> "%MSG%" echo Output too long to quote — !MSGSIZE! bytes of it.
  >> "%MSG%" echo Log: %LOG%
  >> "%MSG%" echo Further alerts for this job are held for the rest of the hour.
)

curl -s -m 20 -o NUL -X POST "https://api.telegram.org/bot%TG_TOKEN%/sendMessage" ^
  --data-urlencode "chat_id=%OPS_CHAT%" ^
  --data-urlencode "text@%MSG%" ^
  --data-urlencode "disable_web_page_preview=true"

:done
exit /b !CODE!
