# Register + start a real Windows Task Scheduler task running the akasha
# daemon under a supervisor wrapper, for the vision/mvp-spec real-OS
# residency leg. Disposable scratch DB + a dedicated task name -- see
# lib.ps1's header comment. Does NOT require an elevated shell: elevation
# (via UAC) is requested only if/when a specific Scheduled-Task operation
# is actually denied, and only for that one operation.
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File init.ps1
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib.ps1")

if (Test-Path $ScratchDir) {
    throw "refusing: '$ScratchDir' already exists -- run destroy.ps1 first"
}
New-Item -ItemType Directory -Path $ScratchDir -Force | Out-Null

@"
port = $Port
bind = "127.0.0.1"
db_path = "$DbPath"
"@ | Set-Content -Path $ConfigPath -Encoding Ascii

Write-Host "== bootstrapping DB (migrations only, no auth needed for /health) =="
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
& $PythonExe -c "from akasha.kernel import store; conn = store.connect(r'$DbPath'.replace('/', '\\'), check_same_thread=True); store.run_migrations(conn); conn.close(); print('migrated')"
if ($LASTEXITCODE -ne 0) { throw "DB bootstrap failed" }

Write-Host "== writing supervisor wrapper (real crash recovery -- see lib.ps1 header) =="
New-DaemonWrapperScript

Write-Host "== registering scheduled task '$TaskName' (idempotent; elevates only if denied) =="
Invoke-TaskOp -Op Register

Write-Host "== starting task =="
Invoke-TaskOp -Op Start
Start-Sleep -Seconds 2

if (-not (Wait-Health -PortNum $Port -TimeoutSec 20)) {
    throw "daemon did not come up on port $Port within timeout"
}
$daemonPid = Get-ListenerPid -PortNum $Port
Write-Host "daemon up, port $Port, pid $daemonPid, task '$TaskName' registered (AtLogOn trigger -> supervisor wrapper -> daemon)"
