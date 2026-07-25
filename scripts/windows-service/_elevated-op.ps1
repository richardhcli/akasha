# Minimal, single-purpose elevated helper. Never invoke this directly --
# lib.ps1's Invoke-TaskOp launches it (via UAC) only when the equivalent
# non-elevated Scheduled-Task cmdlet call was denied, and only to perform
# the exact one operation requested. It does nothing else: it does not
# start the daemon itself, does not touch the scratch vault/DB, and holds
# elevation only for the lifetime of this one CIM call.
param(
    [Parameter(Mandatory=$true)][ValidateSet("Register","Unregister","Start","Stop")] [string]$Op,
    [Parameter(Mandatory=$true)][string]$TaskName,
    [string]$WrapperPath
)
$ErrorActionPreference = "Stop"
try {
    switch ($Op) {
        "Register" {
            if (-not $WrapperPath) { throw "Register requires -WrapperPath" }
            $action = New-ScheduledTaskAction -Execute $WrapperPath
            $trigger = New-ScheduledTaskTrigger -AtLogOn
            $settings = New-ScheduledTaskSettingsSet `
                -ExecutionTimeLimit ([TimeSpan]::Zero) `
                -DontStopOnIdleEnd -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
            $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
            Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
                -Settings $settings -Principal $principal -Force | Out-Null
        }
        "Unregister" { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue }
        "Start" { Start-ScheduledTask -TaskName $TaskName }
        "Stop" { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
    }
    exit 0
} catch {
    Write-Error $_
    exit 1
}
