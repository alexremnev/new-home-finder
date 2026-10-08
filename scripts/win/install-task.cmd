@echo off
REM Install the scheduled tasks. Run once, from an elevated prompt.
REM
REM   scripts\win\install-task.cmd
REM
REM Seven tasks, not one. ingest reads the feed, drain sends what it queued,
REM rollup totals the day into daily_stats so the console reads counts instead of
REM computing them, report checks for silence, and three portal readers fetch
REM the sites directly — two of which refuse the server's address outright.
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

REM ── the three portal readers ───────────────────────────────────────────────
REM
REM Every twenty minutes, round the clock, offset seven minutes from each other
REM so the three sweeps never start in the same minute.
REM
REM /SC MINUTE /MO 20 is a plain repeat with no daily window, so this runs
REM overnight too. The server's timers stop between 22:00 and 07:20 because that
REM band exists to hold down a traffic bill; here there is no bill, and a
REM listing posted at 23:00 is worth finding at 23:00.
REM
REM Zoopla and OpenRent especially belong here rather than on the server.
REM Measured from the server on 27 September 2026: Zoopla answers 403 under
REM every browser fingerprint and OpenRent answers 405, while this connection is
REM served normally. So running them here is not a workaround, it is the cheap
REM arrangement — the alternative is a metered residential proxy.
REM
REM Rightmove is served on the server as well, so it now runs in both places.
REM That is not harmful — whichever sweep arrives second finds nothing new — but
REM it is wasted work, and if this machine is reliably awake the server's copy
REM can be stopped with:
REM
REM     systemctl disable --now london-home-finder-rightmove.timer
REM
REM The old "home scrape" task is deleted rather than left alone. It ran the
REM sitemap reader, which fetched the whole nationwide sitemap every half hour —
REM about 950MB a day to discover two or three listings — and the `openrent`
REM job does the same work by reading one 92KB page per district.
schtasks /Delete /F /TN "home scrape" 2>NUL

schtasks /Create /F /RL LIMITED /SC MINUTE /MO 20 /ST 00:00 ^
  /TN "home rightmove" /TR "%RUN% rightmove"

schtasks /Create /F /RL LIMITED /SC MINUTE /MO 20 /ST 00:07 ^
  /TN "home zoopla" /TR "%RUN% zoopla"

schtasks /Create /F /RL LIMITED /SC MINUTE /MO 20 /ST 00:14 ^
  /TN "home openrent" /TR "%RUN% openrent"

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
