# Windows packaging (exe, tray, installer)

Build-plan task T12.5. Covers `scripts/windows/build-exe.ps1` (PyInstaller),
`scripts/windows/akasha.iss` (Inno Setup), and
`scripts/windows/run-tray-supervised.bat` (the autostart supervisor loop).
The narrative "why" for each design choice lives in code comments in those
three files — this page is the map, not a duplicate.

## Build the exe

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\build-exe.ps1
```

Runs `uv sync --extra tray --group packaging`, then PyInstaller `--onefile`
targeting `src\akasha\cli\main.py`, bundling `migrations/` and
`src\akasha\ui\{static,templates}` via `--add-data`, with `--collect-all`
for `uvicorn`/`fastapi`/`pystray`/`PIL` (packages that use dynamic imports
PyInstaller's static analysis won't find on its own). Output:
`dist\akasha.exe`. Pass `-OneDir` for an unpacked directory build instead
(faster iteration; slower to distribute).

`akasha.exe` is a two-process PyInstaller bootloader+payload pair at
runtime (`akasha.exe` parent relays the real Python process's exit code) —
seeing two `akasha.exe` PIDs in Task Manager with a parent/child
relationship is expected, not a leak.

## Compile the installer

Requires [Inno Setup](https://jrsoftware.org/isinfo.php) (`ISCC.exe`) on
the host. It is not always under `C:\Program Files*` — winget installs it
user-locally (this repo's own dev host has it at
`%LocalAppData%\Programs\Inno Setup 7\ISCC.exe`); search broadly if the
obvious paths come up empty.

```powershell
& "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe" scripts\windows\akasha.iss
```

Output: `dist-installer\akasha-setup.exe`. It expects `dist\akasha.exe` to
already exist (run `build-exe.ps1` first) and is never elevated
(`PrivilegesRequired=lowest`) — the install, the autostart registration,
and the app itself all run entirely as the logged-in user, matching
`docs/mvp-spec.md` §3's "binds 127.0.0.1 only" no-admin posture.

Silent install for testing: `dist-installer\akasha-setup.exe /VERYSILENT
/SUPPRESSMSGBOXES /TASKS="autostart"`.

## Why not Task Scheduler

Two independent findings on this project's own dev hosts, both already
documented where the fix lives:

1. Task Scheduler's own `RestartOnFailure` does not reliably restart a
   force-killed process — `docs/dogfood/windows-service.md` recorded a
   force-killed task not restarting over 3.5 minutes of polling.
2. `Register-ScheduledTask`/`schtasks.exe` can return Access Denied for a
   plain non-admin `AtLogOn` task on this exact host, and a `-Verb RunAs`
   elevation fallback would hang a `/SILENT` install on a UAC prompt —
   unacceptable for an installer that must never require elevation.

`scripts/windows-service/lib.ps1` proved the general fix for (1): a
supervisor loop outside the OS's own retry logic that unconditionally
relaunches the process on exit. The installer applies the same pattern
without Task Scheduler at all, sidestepping (2) entirely: both the Start
Menu and `{userstartup}` (Startup-folder) shortcuts point at
`run-tray-supervised.bat`, not directly at `akasha.exe tray`. The `.bat`
relaunches `akasha.exe tray` on any exit except code 42, which
`src/akasha/tray.py`'s Quit menu item emits on purpose — see that file's
`quit_app` and the `.bat`'s own header comment for the full contract. The
retry cap (20 consecutive relaunches, then give up and write
`akasha-crashloop.txt` next to the exe) is a flat count, not
time-windowed, as a deliberately simple safety valve against a build that
can't start at all — see the `.bat`'s comments for the tradeoff.

## Verifying a build end-to-end

No automated test drives the compiled installer (it's Windows-GUI-only
and outside `tests/`'s scope) — verify manually:

1. `dist\akasha.exe --help` lists every CLI verb.
2. `dist\akasha.exe init --config <scratch>` mints a token from the frozen
   bundle (exercises the PyInstaller-bundled `migrations/` — see the
   `MIGRATIONS_DIR` note below).
3. `dist\akasha.exe daemon --config <scratch>` serves `/health`, an
   authenticated `GET /v1/tokens`, and `GET /dashboard`.
4. Run the compiled installer `/VERYSILENT`, confirm zero elevation
   prompts, correct `%LocalAppData%\akasha` placement, and correct
   shortcuts (Start Menu + `{userstartup}`, both pointing at the `.bat`).
5. Crash recovery: force-kill `akasha.exe` (`taskkill /IM akasha.exe /F`)
   and confirm a new PID answers `/health` again within a few seconds.
6. Quit semantics: the tray menu's Quit is exit-code-42-gated as described
   above — a live tray-icon click on Quit is the one piece of this flow
   that has proven hard to verify from an automated/remote session (native
   win32 tray popups don't screenshot cleanly through every tooling
   chain); code review of `quit_app` plus a scripted `.bat` control-flow
   test covering the loop's if/goto logic against multiple exit-code
   sequences is the fallback verification method — see
   `docs/agents/task-status.md`'s T12.5 row for the specific run this was
   last done on.

## A packaging-only bug worth knowing about

`kernel/store.py`'s `MIGRATIONS_DIR` used to be
`Path(__file__).resolve().parents[3] / "migrations"` — correct for every
non-frozen entry point (tests, `uv run akasha`), but repo-root-relative
paths don't exist inside a PyInstaller bundle. It now branches on
`getattr(sys, "frozen", False)` and resolves from `sys._MEIPASS` (the
bundle's extraction directory) when frozen, otherwise the original path —
so this bug class (frozen-vs-source path resolution) is worth checking for
in any other module that computes a path relative to `__file__` before
assuming it'll "just work" in the packaged build.
