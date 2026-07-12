# Running the daemon at startup on Windows

`akasha daemon` (spec §4.12) is a long-lived foreground process: it loads
config, acquires a single-instance lock (build-plan task T4.9,
`src/akasha/daemon.py`), and serves the local API on
`127.0.0.1:7433` (default `config.bind`/`config.port`, spec §3) until it is
stopped. Windows has no `systemd`/`launchd` equivalent, so this doc covers
the two supported ways to keep it running: **Task Scheduler** (built into
Windows, runs at user logon, no extra install) and **NSSM** (runs as a
proper background Windows service, survives without an interactive logon
session).

Because the daemon has its own single-instance lock
(`tm-daemon.lock` in the config directory — neutral name, no product
branding on disk, build-plan rule 0.6), it is always safe to configure
*both* mechanisms at once, or to run `akasha daemon` manually alongside
one of them: the second launch attempt exits cleanly with a message
instead of starting a competing server.

## Option A — Task Scheduler (logon trigger)

Task Scheduler runs `akasha daemon` as soon as the user signs in. This is
the simplest option and needs no extra download.

### Import via the XML sample

1. Save the XML below as `akasha-daemon-task.xml`.
2. Edit the two placeholders:
   - `%USERNAME%` in the `<UserId>` element — your Windows account name
     (or run `whoami` in a `cmd.exe` prompt to get the exact value).
   - `C:\Path\To\akasha.exe` in `<Command>` — the full path to the
     installed `akasha` entry point (e.g. the `Scripts\akasha.exe` inside
     your Python/`uv` virtual environment, found via `where akasha` in an
     activated environment).
3. Open Task Scheduler (`taskschd.msc`) → **Action** → **Import Task...**
   → select `akasha-daemon-task.xml`.
4. Confirm the task under **Task Scheduler Library**; right-click →
   **Run** to start it immediately without waiting for the next logon.

```xml
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Runs the akasha daemon (local pTMS API on 127.0.0.1:7433) at user logon.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>%USERNAME%</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>%USERNAME%</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>C:\Path\To\akasha.exe</Command>
      <Arguments>daemon</Arguments>
    </Exec>
  </Actions>
</Task>
```

Notes on the XML:

- `<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>` tells
  Task Scheduler itself not to launch a second copy if one is already
  running under its management; the daemon's own single-instance lock is
  the real, always-on guarantee (it also protects against a manually
  launched `akasha daemon`, or a second Task Scheduler entry, or NSSM
  running concurrently).
- `<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>` disables Task
  Scheduler's default time limit (which would otherwise kill a
  long-running daemon after 72 hours).
- `RestartOnFailure` restarts the daemon (up to 3 times, 1 minute apart)
  if it exits unexpectedly; it will **not** loop-restart against a held
  lock, because the daemon's own exit code and message make that failure
  visible in the task's history rather than silently spinning.

### Or: create the task with `schtasks` (no XML import)

```powershell
schtasks /Create /TN "akasha-daemon" /TR "C:\Path\To\akasha.exe daemon" ^
  /SC ONLOGON /RL LIMITED /F
```

## Option B — NSSM (runs as a Windows service)

[NSSM](https://nssm.cc/) ("the Non-Sucking Service Manager") wraps an
ordinary executable as a real Windows service, so it can start at boot
(before any user logs in) and is managed with `services.msc` /
`sc.exe` / `net start` like any other service.

1. Download NSSM from <https://nssm.cc/download> and extract
   `nssm.exe` (pick the `win64` binary for a 64-bit machine) somewhere on
   `PATH`, e.g. `C:\tools\nssm\nssm.exe`.
2. Open an elevated (**Run as administrator**) `cmd.exe` or PowerShell
   prompt and install the service:

   ```powershell
   nssm install akasha-daemon "C:\Path\To\akasha.exe" daemon
   ```

   Running `nssm install akasha-daemon` with no further arguments instead
   opens NSSM's GUI installer, where the same three fields (**Path**,
   **Arguments** = `daemon`, **Startup directory**) can be filled in
   interactively.
3. Configure logging (optional — the daemon already writes its own
   structured JSON log to the config directory, but NSSM can also capture
   stdout/stderr for crash diagnostics):

   ```powershell
   nssm set akasha-daemon AppStdout C:\Path\To\logs\akasha-daemon.out.log
   nssm set akasha-daemon AppStderr C:\Path\To\logs\akasha-daemon.err.log
   nssm set akasha-daemon AppRotateFiles 1
   ```
4. Set the service to start automatically at boot and start it:

   ```powershell
   nssm set akasha-daemon Start SERVICE_AUTO_START
   nssm start akasha-daemon
   ```
5. Manage it afterwards with the standard service commands:

   ```powershell
   nssm status akasha-daemon
   nssm restart akasha-daemon
   nssm stop akasha-daemon
   nssm remove akasha-daemon confirm   # uninstall
   ```

Because a Windows service usually runs under the `LocalSystem` account
(not your interactive user profile), make sure `--config` (or the
service's working directory / environment) points at the intended config
location — the default config directory is resolved per-user
(`%APPDATA%\tm-daemon`, spec §3), which for `LocalSystem` is **not** the
same as your own account's `%APPDATA%`. Passing an explicit path avoids
that mismatch:

```powershell
nssm install akasha-daemon "C:\Path\To\akasha.exe" daemon --config "C:\Users\<you>\AppData\Roaming\tm-daemon\config.toml"
```

## Verifying either setup

After either option is running, confirm the API is reachable and that a
second manual launch is correctly refused:

```powershell
curl http://127.0.0.1:7433/health
akasha daemon
# expected: a one-line "error: another akasha daemon instance is already
# running (lock held at ...\tm-daemon.lock)" message and a non-zero exit
# (4) -- not a traceback, and the already-running service/task is
# unaffected.
```
