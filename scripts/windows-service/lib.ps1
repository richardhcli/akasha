# Shared config + helpers for scripts/windows-service/{init,deinit,destroy}.ps1.
#
# This registers the akasha daemon as a REAL Windows Task Scheduler task,
# for the vision/mvp-spec "real-OS residency" leg (autostart + recovery
# after a hard kill) that no CI runner or dev-host pytest run can exercise --
# it needs an actual OS service/task manager on a real machine. Disposable
# by design: a distinct scratch DB, a distinct scheduled-task name
# (AkashaDogfoodResidencyTest), never the user's real config dir or a task
# name that could collide with a genuine future akasha service.
#
# IMPORTANT, empirically verified 2026-07-25 on a real Windows 11 host:
# Task Scheduler's own "restart on failure" (RestartCount/RestartInterval)
# does NOT reliably restart a long-running process that is force-killed
# (taskkill /F) -- confirmed via a live kill test: the task's LastTaskResult
# flipped to a failure code, but no restart happened over 3.5 minutes of
# polling despite RestartInterval=1min/RestartCount=3 being set. This is a
# known real-world Task Scheduler limitation for user-session tasks, not a
# config mistake here. The actual crash-recovery mechanism these scripts
# use instead is a small supervisor loop (New-DaemonWrapperScript below)
# that Task Scheduler launches ONCE at logon; the loop itself relaunches
# the daemon whenever it exits, for any reason. Task Scheduler's job is
# reduced to what it's actually reliable for: autostart at logon.
#
# PRIVILEGE MODEL: registering/unregistering a Scheduled Task requires
# admin rights on this host (a locked-down "Windows 11 Enterprise
# Evaluation" image -- both Register-ScheduledTask and schtasks.exe
# returned Access Denied for a plain, non-admin, own-account AtLogOn task,
# which is stricter than a typical consumer Windows install). These
# scripts do NOT require you to run an elevated shell up front: every
# privileged operation is attempted normally first, and only on a genuine
# Access Denied does it relaunch a minimal single-purpose helper via UAC
# (Start-Process -Verb RunAs) for JUST that one operation. The daemon
# process itself always runs as the normal logged-in user (LogonType
# Interactive, RunLevel Limited) -- elevation is never held, never used to
# run the daemon, and never required for start/stop of an already
# registered task if your account's policy allows that without it.

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$TaskName = "AkashaDogfoodResidencyTest"
$ScratchDir = Join-Path $env:USERPROFILE ".local\share\akasha-dogfood\service-test"
$Port = 7434
$ExePath = Join-Path $RepoRoot ".venv\Scripts\akasha.exe"
$ConfigPath = Join-Path $ScratchDir "config.toml"
$WrapperPath = Join-Path $ScratchDir "run-daemon-supervised.bat"
$DbPath = (Join-Path $ScratchDir "store.db") -replace '\\', '/'

function Assert-UnderDogfoodRoot {
    param([string]$Path)
    $dogfoodRoot = Join-Path $env:USERPROFILE ".local\share\akasha-dogfood"
    $resolved = (Resolve-Path $Path -ErrorAction SilentlyContinue)
    if (-not $resolved) {
        throw "refusing: '$Path' does not exist"
    }
    if (-not $resolved.Path.StartsWith((Resolve-Path $dogfoodRoot).Path)) {
        throw "refusing: '$($resolved.Path)' is not under the scratch root '$dogfoodRoot' -- will not touch it"
    }
}

function Get-ListenerPid {
    param([int]$PortNum)
    $conn = Get-NetTCPConnection -LocalPort $PortNum -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) { return $conn.OwningProcess }
    return $null
}

function Wait-Health {
    param([int]$PortNum, [int]$TimeoutSec = 20)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$PortNum/health" -UseBasicParsing -TimeoutSec 2
            if ($resp.StatusCode -eq 200) { return $true }
        } catch {}
        Start-Sleep -Milliseconds 500
    }
    return $false
}

# The actual crash-recovery mechanism (see module header): a supervisor
# loop, launched once by Task Scheduler at logon, that relaunches the
# daemon every time it exits. Never elevated -- runs at the same privilege
# as the logged-in user, same as running `akasha daemon` by hand would.
function New-DaemonWrapperScript {
    @"
@echo off
:loop
"$ExePath" daemon --config "$ConfigPath"
timeout /t 2 /nobreak >nul
goto loop
"@ | Set-Content -Path $WrapperPath -Encoding Ascii
}

# Runs a single named Scheduled-Task operation normally first; only on a
# genuine Access Denied does it relaunch scripts\windows-service\_elevated-op.ps1
# elevated (a UAC prompt if the current session isn't already elevated) to
# perform JUST that one operation, then returns. Elevation is requested,
# used once, and released -- never held across the rest of the script.
function Invoke-TaskOp {
    param(
        [Parameter(Mandatory=$true)][ValidateSet("Register","Unregister","Start","Stop")] [string]$Op
    )
    try {
        switch ($Op) {
            "Register" {
                $action = New-ScheduledTaskAction -Execute $WrapperPath
                $trigger = New-ScheduledTaskTrigger -AtLogOn
                $settings = New-ScheduledTaskSettingsSet `
                    -ExecutionTimeLimit ([TimeSpan]::Zero) `
                    -DontStopOnIdleEnd -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
                $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
                Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
                Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
                    -Settings $settings -Principal $principal -Force -ErrorAction Stop | Out-Null
            }
            "Unregister" { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop }
            "Start" { Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop }
            "Stop" { Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop }
        }
        return
    } catch {
        $msg = $_.Exception.Message
        if ($msg -notmatch "Access is denied" -and $msg -notmatch "0x80070005") {
            throw
        }
    }

    Write-Host "  -> '$Op' needs elevation on this host; requesting it for this one operation only (UAC prompt may appear)"
    $helper = Join-Path $PSScriptRoot "_elevated-op.ps1"
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $helper, "-Op", $Op, "-TaskName", $TaskName, "-WrapperPath", $WrapperPath)
    $p = Start-Process -FilePath "powershell.exe" -ArgumentList $argList -Verb RunAs -Wait -PassThru
    if ($p.ExitCode -ne 0) {
        throw "elevated '$Op' failed (exit code $($p.ExitCode))"
    }
}
