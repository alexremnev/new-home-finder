@echo off
REM Run the portal scrapers here, now, and show what happened.
REM
REM   scrape.cmd                       the three this machine runs, in order
REM   scrape.cmd zoopla                just that one
REM   scrape.cmd spareroom             a server reader, by hand
REM   scrape.cmd rightmove --dry-run   flags are passed straight through
REM   scrape.cmd --dry-run             the default three, flags only
REM
REM ── why this machine is a good place to run them ────────────────────────────
REM
REM The three portals do not agree about the server. Measured from it on
REM 27 September 2026: Rightmove serves it, Zoopla answers 403, OpenRent answers
REM 405. From a home connection all three serve the identical requests, so what
REM the other two object to is the address, not the request.
REM
REM That makes this machine the cheapest place to read Zoopla and OpenRent: it is
REM not blocked, so nothing has to go through a metered residential proxy.
REM
REM Discovery happens wherever this runs; delivery still happens on the server,
REM because both share one database and `drain` runs there. This machine being
REM asleep delays those listings, it does not stop the alerts.
REM
REM ── why this is not run-job.cmd ────────────────────────────────────────────
REM
REM run-job.cmd is written for Task Scheduler: it swallows the output into a log
REM and sends a Telegram alert on failure, throttled to one an hour. Both are
REM wrong for a run you are watching — you want to see the output, and a failed
REM experiment should not spend the hour's alert and so hide a real failure an
REM hour later.
REM
REM So this one prints everything, alerts nobody, and still keeps a log.

setlocal EnableDelayedExpansion

for %%I in ("%~dp0..\..") do set PROJECT=%%~fI
set PY=%PROJECT%\.venv\Scripts\python.exe
set LOGDIR=%PROJECT%\logs
REM Trailing space included on purpose — see the match below.
REM
REM Every job name `python -m worker` accepts as a portal reader, not only the
REM ones this machine runs on a schedule: this list is the typo guard below, and
REM rejecting a name that really exists is worse than the typo it is catching.
REM `zoopla_london` and `spareroom` are both server jobs and both perfectly
REM runnable here by hand.
set KNOWN=rightmove zoopla zoopla_london spareroom openrent portals 

REM ── what to run ────────────────────────────────────────────────────────────
REM
REM Named here rather than deferred to the `portals` job, so that one reader
REM failing does not stop the next: `portals` is a single run with a single exit
REM code, and a Zoopla outage would take Rightmove's listings down with it.
REM
REM The three this machine exists to run — the ones the server's address is
REM refused by, plus Rightmove. `spareroom` is deliberately not among them: the
REM server reads it directly and a second schedule here would only duplicate
REM the traffic. It is still accepted as an argument, for a run by hand.
set JOBS=rightmove zoopla openrent
set PASSTHRU=

REM `shift` is deliberately not used inside a parenthesised block: the %1..%9
REM there are expanded when the block is parsed, so shifting inside one moves
REM nothing and the arguments come out wrong. Labels and goto instead.
if "%~1"=="" goto :args
set FIRST=%~1
if "!FIRST:~0,1!"=="-" goto :args
set JOBS=%FIRST%
shift

:args
if "%~1"=="" goto :checked
set PASSTHRU=!PASSTHRU! %~1
shift
goto :args

:checked
REM A typo should say so here rather than reach argparse, which answers a
REM misspelled job with its whole usage message and an exit code that looks
REM exactly like a portal refusing us.
for %%J in (%JOBS%) do (
  REM The trailing space on both sides makes this an exact word match. Without
  REM it "right" is a substring of "rightmove" and a typo sails through here to
  REM fail in argparse instead.
  echo %KNOWN% | "%WINDIR%\System32\findstr.exe" /I /C:"%%J " >NUL
  if errorlevel 1 (
    echo "%%J" is not a portal reader.
    echo Try one of: %KNOWN%
    exit /b 2
  )
)

REM ── the environment ────────────────────────────────────────────────────────

if not exist "%PY%" (
  echo Virtualenv missing: "%PY%"
  echo Run this first:
  echo     uv sync --extra ingest --extra scrape --extra dev
  exit /b 1
)

REM The transport. Rightmove answers a plain client 403 and the same request 200
REM once the TLS handshake looks like a browser's, so without curl_cffi the
REM readers cannot even import — and that arrives as a bare ImportError several
REM frames deep, which is a poor way to find out an extra was missed.
"%PY%" -c "import curl_cffi" 2>NUL
if errorlevel 1 (
  echo curl_cffi is not installed in this virtualenv, and the portal readers
  echo cannot import without it. Run:
  echo     uv sync --extra ingest --extra scrape --extra dev
  exit /b 1
)

if not exist "%LOGDIR%" mkdir "%LOGDIR%"
cd /d "%PROJECT%" || exit /b 1

REM %DATE% is locale-dependent, so the parts are reordered into a name that
REM sorts chronologically whatever the regional settings are.
for /f "tokens=1-3 delims=/-. " %%a in ("%DATE%") do set TODAY=%%c-%%b-%%a
set LOG=%LOGDIR%\scrape-%TODAY%.log

REM ── the runs ───────────────────────────────────────────────────────────────

set FAILED=
set RAN=0

echo.
for %%J in (%JOBS%) do (
  echo ================================================================
  echo  %%J    %DATE% %TIME:~0,8%
  echo ================================================================

  REM Output goes to a file, then to both the screen and the day's log.
  REM
  REM The obvious `... 2^>^&1 ^| find /v ""` shows it live but destroys the exit
  REM code: in a pipeline ERRORLEVEL is the *last* command's, so find's nought
  REM would mean every run looked successful. Waiting for the file is worth a
  REM correct answer about whether it worked.
  set OUT=%TEMP%\home-scrape-%%J.out
  "%PY%" -m worker %%J --trigger manual !PASSTHRU! > "!OUT!" 2>&1
  set CODE=!ERRORLEVEL!

  type "!OUT!"
  echo ==== START %DATE% %TIME% %%J !PASSTHRU! >> "%LOG%"
  type "!OUT!" >> "%LOG%"
  echo ==== END   %DATE% %TIME% %%J exit !CODE! >> "%LOG%"

  set /a RAN+=1
  if not "!CODE!"=="0" set FAILED=!FAILED! %%J
  echo.
)

echo ================================================================
if "!FAILED!"=="" (
  echo  All !RAN! finished without error.
) else (
  echo  Finished with failures:!FAILED!
)
echo  Log: %LOG%
echo ================================================================

REM Non-zero only when something actually failed, so this can be chained.
if not "!FAILED!"=="" exit /b 1
exit /b 0
