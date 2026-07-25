# Stop (if running), unregister the scheduled task entirely, and delete
# the scratch DB/config directory. Does not require an elevated shell up
# front; see lib.ps1's header comment.
#
# Safety: refuses unless the resolved scratch directory is a real,
# existing descendant of %USERPROFILE%\.local\share\akasha-dogfood -- this
# makes it structurally impossible for this script to ever touch a real
# %APPDATA%\tm-daemon store.db, in this environment or a real one.
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File destroy.ps1
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib.ps1")

& (Join-Path $PSScriptRoot "deinit.ps1")

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Invoke-TaskOp -Op Unregister
    Write-Host "unregistered task '$TaskName'"
} else {
    Write-Host "task '$TaskName' was not registered"
}

if (Test-Path $ScratchDir) {
    Assert-UnderDogfoodRoot -Path $ScratchDir
    Remove-Item -Recurse -Force $ScratchDir
    Write-Host "destroyed $ScratchDir"
} else {
    Write-Host "'$ScratchDir' does not exist -- nothing to delete"
}
