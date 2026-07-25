# Running the daemon as a persistent Windows service, for dogfooding

This documents `scripts/windows-service/{init,deinit,destroy}.ps1`: a way
to run the akasha daemon as a real, autostarting, crash-recovering
background process on Windows, for a human dogfooding the product day to
day (as opposed to `docs/dogfood/README.md`'s scratch-vault runbook, which
covers registering vault content against a manually-started daemon). The
two are complementary — you can run the service scripts against the same
kind of scratch config the vault runbook describes, or against your own.

Read this before running these scripts if you've never used them. It also
covers `scripts/dogfood/{init,deinit,destroy}.sh`, the Bash-side lifecycle
scripts for a disposable dogfood vault + daemon (used for the browser/UI
verification these came from), since the two script sets share the same
safety conventions.

## What these scripts do, in one paragraph each

**`scripts/dogfood/{init,deinit,destroy}.sh`** (Git Bash / MSYS): stand up
a throwaway sync-root vault + daemon under
`$HOME/.local/share/akasha-dogfood/<name>/`, mint a real human bearer
token over HTTP, register the vault, and run an initial rescan —
everything the `docs/dogfood/README.md` runbook does by hand, scripted.
`init.sh <name> [port]` creates it; `deinit.sh <name>` stops the daemon and
leaves the vault/DB on disk; `destroy.sh <name>` stops it and deletes the
scratch tree. `destroy.sh` refuses to run against anything outside
`$HOME/.local/share/akasha-dogfood/` — see "Safety model" below.

**`scripts/windows-service/{init,deinit,destroy}.ps1`** (PowerShell):
register the daemon as a real Windows Task Scheduler task so it survives
logon/logoff and process crashes, for exercising the vision/mvp-spec
"real-OS residency" requirement that no CI runner or pytest run can touch.
`init.ps1` prepares a scratch DB under
`%USERPROFILE%\.local\share\akasha-dogfood\service-test\`, registers the
task, and starts it; `deinit.ps1` stops the running instance but leaves
the task registered; `destroy.ps1` stops it, unregisters the task, and
deletes the scratch tree. Same safety-guard convention as the `.sh`
scripts.

## Why a supervisor wrapper, not Task Scheduler's own "restart on failure"

**Empirically verified 2026-07-25 on a real Windows 11 host: Task
Scheduler's `RestartCount`/`RestartInterval` settings do not reliably
restart a long-running task process that gets force-killed.** A live test
— register a task with `RestartCount=3`/`RestartInterval=1min`, start it,
`taskkill /F` the running daemon process, then poll for up to 3.5 minutes
— showed `LastTaskResult` flip to a failure code but **no restart ever
happened**. This is a known real-world limitation of Task Scheduler for
user-session (`AtLogOn`) tasks, not a configuration mistake in these
scripts.

The fix `init.ps1` actually uses: Task Scheduler's action points at a
tiny generated supervisor script
(`%SCRATCH%\run-daemon-supervised.bat`), not at `akasha.exe` directly:

```bat
:loop
"<akasha.exe>" daemon --config "<config.toml>"
timeout /t 2 /nobreak >nul
goto loop
```

Task Scheduler's only job is starting this loop once at logon. The loop
itself relaunches the daemon every time it exits, for any reason —
verified live: two consecutive `taskkill /F` calls against the daemon
process each produced a genuinely new PID listening on the port again
within ~2 seconds, both times. This is the actual, demonstrated
crash-recovery mechanism; treat any future claim of "restart on failure"
via Task Scheduler's native settings alone as unverified until it's
re-tested the same way.

## Privilege model — you should never need to "run as Administrator"

Registering or unregistering a Scheduled Task requires admin rights **on
some Windows images** — this was discovered because a locked-down
"Windows 11 Enterprise Evaluation" VM denied `Register-ScheduledTask` and
`schtasks.exe /Create` for a plain, non-admin, own-account `AtLogOn` task
(stricter than a typical consumer Windows install, where this usually
needs no elevation at all).

These scripts handle that without requiring you to open an elevated
shell:

- Every Scheduled-Task operation (register/start/stop/unregister) is
  attempted **normally first**.
- Only on a genuine Access Denied does the script relaunch a minimal,
  single-purpose helper (`scripts\windows-service\_elevated-op.ps1`) via
  `Start-Process -Verb RunAs` — a UAC consent prompt, if your session
  isn't already elevated — to perform **just that one operation**.
- Elevation is never held past that single call, and the daemon process
  itself always runs as your normal logged-in user (`LogonType
  Interactive`, `RunLevel Limited`) — elevation is never used to run the
  daemon, only (if needed at all) to register/start/stop/unregister the
  task definition itself.

**Never run these scripts from an already-elevated shell as standard
practice.** Elevation should be requested per-operation, on demand, if
your machine's policy actually requires it — running everything elevated
defeats the least-privilege point of this design and was done only once,
deliberately, to validate the mechanism end-to-end on a host where normal
registration was denied outright.

## Safety model (shared by both script sets)

- Every scratch path lives under a dedicated, disposable root
  (`$HOME/.local/share/akasha-dogfood/` / `%USERPROFILE%\.local\share\akasha-dogfood\`)
  — never your real `~/.config/tm-daemon/` or `%APPDATA%/tm-daemon/`.
- `destroy.sh`/`destroy.ps1` refuse to delete anything that does not
  resolve to a real, existing path under that scratch root — this is
  enforced by a path-prefix check (`require_under_dogfood_root` /
  `Assert-UnderDogfoodRoot`), not just a comment, so it is structurally
  impossible for either script to touch a real database.
- `init` refuses to run if its target scratch directory already exists
  (run `destroy` first) — this avoids silently building on top of a
  partial or stale prior attempt.
- The Scheduled Task name (`AkashaDogfoodResidencyTest`) and every scratch
  DB/config are clearly dogfood-scoped, never reused for a hypothetical
  future real akasha Windows service.

## Path-translation gotcha (Bash-side only)

Git Bash (MSYS) paths like `/c/Users/...` are **not** understood by
native Windows Python/sqlite3 when passed through a heredoc (no
argument-based auto-conversion applies to stdin). `scripts/dogfood/lib.sh`
converts once, at the boundary, via `cygpath -w | tr '\\' '/'`, and every
downstream path (config.toml's `db_path`, the `POST /v1/sync/roots`
`root_path` body, `uv run python` invocations) reuses that single
converted value — never re-derive a path ad hoc mid-script.

## Quick start

```sh
# Vault + daemon (Bash):
bash scripts/dogfood/init.sh my-test-vault 7433
# ... use it (curl, browser, etc.) ...
bash scripts/dogfood/destroy.sh my-test-vault

# Persistent Windows-service daemon (PowerShell), autostart + crash-recovery:
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows-service/init.ps1
# ... reproduce the kill-9 test yourself if you want to see it live: ...
#   $p = (Get-NetTCPConnection -LocalPort 7434 -State Listen).OwningProcess
#   taskkill /F /PID $p
#   # poll: Get-NetTCPConnection -LocalPort 7434 -State Listen -- a new PID
#   # should appear within a few seconds
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows-service/destroy.ps1
```
