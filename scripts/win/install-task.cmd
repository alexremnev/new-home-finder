@echo off
REM Install the scheduled tasks. Run once, from an elevated prompt.
REM
REM   scripts\win\install-task.cmd
REM
REM Six tasks, not one. ingest reads the feed, drain sends what it queued, rollup
REM totals the day into daily_stats so the console reads counts instead of
REM computing them, report checks for silence, and two portal readers fetch the
REM sites that refuse the server's address.
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

REM ── the portal readers that the server cannot run ──────────────────────────
REM
REM Zoopla and OpenRent, read from their own search pages. They live here and
REM not on the server because the server's address is refused: measured from it
REM on 27 September 2026, Zoopla answers 403 under every browser fingerprint and
REM OpenRent answers 405. From this connection both serve the identical requests.
REM
REM So running them here is not a workaround, it is the cheap arrangement: the
REM alternative is a metered residential proxy, and this costs nothing.
REM
REM Rightmove is deliberately NOT here. The server serves it fine and runs it on
REM its own timer; adding it here would mean two machines fetching the same
REM pages, and whichever arrived second would find nothing new and have paid for
REM the privilege.
REM
REM The old "home scrape" task is removed rather than left alone. It ran the
REM sitemap reader, which fetched the whole nationwide sitemap every half hour —
REM about 950MB a day to discover two or three listings — and `openrent_v2`
REM below replaces it by reading one 92KB page per district.
schtasks /Delete /F /TN "home scrape" 2>NUL

REM Every twenty minutes from 07:20 to 22:00, offset from each other so the two
REM sweeps do not start together.
REM
REM Longer in the evening than the server's schedule, which stops the
REM twenty-minute band at 17:00 and goes hourly. That band exists to hold down a
REM traffic bill; here there is no bill, so the cheaper thing is to keep looking.
REM
REM /DU 14:40 /RI 20 is how Task Scheduler expresses a window: start at 07:20,
REM repeat every 20 minutes for fourteen hours and forty minutes.
schtasks /Create /F /RL LIMITED /SC DAILY /ST 07:20 /DU 14:40 /RI 20 ^
  /TN "home zoopla" /TR "%RUN% zoopla"

schtasks /Create /F /RL LIMITED /SC DAILY /ST 07:27 /DU 14:40 /RI 20 ^
  /TN "home openrent" /TR "%RUN% openrent_v2"

schtasks /Create /F /RL LIMITED /SC HOURLY /MO 1 /ST 00:20 ^
  /TN "home report" /TR "cmd /c cd /d \"%PROJECT%\" && .venv\Scripts\python.exe scripts\report.py >> logs\report.log 2>&1"

echo.
echo For a run you want to watch, do not use these tasks — they hide the output
echo in a log. Use instead:
echo.
echo     scripts\win\scrape.cmd              all three, output on screen
echo     scripts\win\scrape.cmd zoopla       just one
echo.
echo Installed. Two settings still have to be ticked by hand in taskschd.msc,
echo for each task, because schtasks cannot set them:
echo.
echo   Conditions -^> clear "Start the task only if the computer is on AC power"
echo   Settings   -^> tick  "Run task as soon as possible after a scheduled start is missed"
echo.
echo Without the first, a laptop on battery runs nothing and the logs stay empty —
echo no error, just silence.
