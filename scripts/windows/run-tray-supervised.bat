@echo off
rem Supervisor loop for `akasha.exe tray` (build-plan T12.5). Bundled as an
rem Inno Setup [Files] asset (scripts\windows\akasha.iss) -- both the
rem installer's Startup-folder autostart shortcut and its Start Menu
rem shortcut point HERE, not directly at akasha.exe, so a manual launch
rem gets the same crash recovery as autostart.
rem
rem Why a loop at all: Task Scheduler's own RestartOnFailure was empirically
rem found unreliable on this project's own dev host (a force-killed task
rem did not restart over 3.5 min of polling -- see
rem docs\dogfood\windows-service.md). scripts\windows-service\lib.ps1 proved
rem the fix: something outside the OS's own retry logic that unconditionally
rem relaunches the process on every exit. This is that pattern, adapted for
rem a Startup-folder shortcut instead of a Task-Scheduler task -- dropped
rem Task Scheduler entirely for T12.5's installer because lib.ps1's own
rem header already documents Register-ScheduledTask/schtasks.exe returning
rem Access Denied for a plain non-admin AtLogOn task on this exact host, and
rem an installer must never require an elevation prompt to complete a
rem /SILENT run.
rem
rem Exit-code contract with src/akasha/tray.py's Quit handler: 42 means
rem "the user asked to quit" -- stop looping, don't treat it as a crash.
rem Any other exit code (a genuine crash, or 4 from a same-machine
rem AlreadyRunningError conflict -- e.g. this script launched twice) is
rem retried, up to a fixed cap below so a persistently broken build can't
rem spin forever.
rem
rem Known simplification, disclosed rather than silently hidden: the retry
rem cap is a flat count of consecutive relaunches, not a time-windowed one
rem (batch's %TIME% is locale-formatted and awkward to parse robustly
rem across locales/midnight rollover -- not worth the fragility for a
rem non-safety-critical safety valve). A daemon that runs stably for weeks
rem and then crashes occasionally will still be retried every time in
rem practice (each restart of THIS SCRIPT, e.g. at the next logon, resets
rem the counter to 0) -- the cap only bites a tight, immediate, repeated
rem failure (e.g. a broken build that can't start at all).

title akasha-supervisor-loop
set "DIR=%~dp0"
set FAILCOUNT=0
set MAXFAILS=20

:loop
"%DIR%akasha.exe" tray
set "RC=%ERRORLEVEL%"
if %RC% EQU 42 goto done

set /a FAILCOUNT+=1
if %FAILCOUNT% GEQ %MAXFAILS% goto giveup

timeout /t 2 /nobreak >nul
goto loop

:giveup
echo akasha stopped auto-restarting after %MAXFAILS% consecutive relaunches (last exit code %RC%). Close this window and check %DIR%daemon.log or the config directory's daemon.log for the real error, then run "%DIR%akasha.exe" daemon from a console to see it directly. > "%DIR%akasha-crashloop.txt"
goto done

:done
