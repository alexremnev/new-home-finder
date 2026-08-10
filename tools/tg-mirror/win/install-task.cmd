@echo off
REM Registers the hourly task. Run once, from a normal prompt — no elevation
REM needed, because /RL LIMITED runs it as you, and reading your own Telegram
REM messages needs nothing more than that.
REM
REM /SC HOURLY /MO 1 with /ST 00:07 puts it seven minutes past the hour rather
REM than on the hour, where every other scheduled thing on the machine already is.
REM
REM Two settings this cannot reach, both worth having on a laptop, so the last
REM step below opens the GUI for them:
REM
REM   "Run task as soon as possible after a scheduled start is missed" — without
REM   it, hours spent asleep are simply skipped. Nothing is lost from Telegram
REM   (history is real history, and the cursor is in state.json), but the copies
REM   arrive only at the next waking hour.
REM
REM   "Hidden" — without it a console window flashes every hour.

setlocal
set TASK=TG mirror

schtasks /Create ^
  /TN "%TASK%" ^
  /TR "\"%~dp0run-mirror.cmd\"" ^
  /SC HOURLY /MO 1 ^
  /ST 00:07 ^
  /RL LIMITED ^
  /F

if errorlevel 1 (
  echo.
  echo Could not register the task. Check the name is not already taken by
  echo something you did not create: schtasks /Query /TN "%TASK%"
  exit /b 1
)

echo.
echo Registered. Run it now to check it works end to end:
echo   schtasks /Run /TN "%TASK%"
echo   type "%~dp0..\logs\mirror-*.log"
echo.
echo Other useful commands:
echo   schtasks /Query  /TN "%TASK%" /V /FO LIST
echo   schtasks /Change /TN "%TASK%" /DISABLE
echo   schtasks /Delete /TN "%TASK%" /F
echo.
echo Now tick two boxes the command line cannot set. Opening Task Scheduler:
echo   Task Scheduler Library -^> "%TASK%" -^> Properties
echo     Settings tab -^> "Run task as soon as possible after a scheduled start is missed"
echo     General  tab -^> "Hidden"
echo.
pause
REM `start` rather than a bare taskschd.msc: .MSC is in PATHEXT, so the bare form
REM works but blocks this window until the console is closed.
start "" taskschd.msc
