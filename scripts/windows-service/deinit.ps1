# Stop the running akasha daemon (task + its supervisor wrapper). Leaves
# the task REGISTERED (init.ps1's Start step, or Invoke-TaskOp -Op Start,
# can bring it back up) and leaves the scratch DB/config on disk -- use
# destroy.ps1 to remove those. Does not require an elevated shell up
# front; see lib.ps1's header comment.
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File deinit.ps1
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib.ps1")

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "task '$TaskName' is not registered -- nothing to stop"
    exit 0
}

Invoke-TaskOp -Op Stop

# The supervisor wrapper's `goto loop` means Stop-ScheduledTask killing the
# current akasha.exe child is not enough on its own if the .bat's own
# cmd.exe process survives -- confirm the port is actually free, and stop
# any surviving listener/wrapper process directly if not.
$deadline = (Get-Date).AddSeconds(10)
while ((Get-Date) -lt $deadline) {
    if (-not (Get-ListenerPid -PortNum $Port)) { break }
    Start-Sleep -Milliseconds 500
}
$remainingPid = Get-ListenerPid -PortNum $Port
if ($remainingPid) {
    Write-Host "port $Port still has a listener (pid $remainingPid) after Stop -- force-killing"
    Stop-Process -Id $remainingPid -Force -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*$WrapperPath*" } |
    ForEach-Object {
        Write-Host "stopping surviving supervisor wrapper process (pid $($_.ProcessId))"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

Write-Host "stopped (task '$TaskName' remains registered)"
