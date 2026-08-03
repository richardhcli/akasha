<#
.SYNOPSIS
    First-time Windows setup for akasha (build-plan T12.4).

.DESCRIPTION
    A one-command bridge to a working daemon + browser session, for a real
    end user -- not a scratch/dogfood script. Unlike scripts/dogfood/*.sh
    and scripts/windows-service/*.ps1 (which deliberately refuse to touch
    anything outside a disposable scratch tree), this script's whole job is
    to set up the REAL default location: %APPDATA%\tm-daemon (spec Sec3).
    Because of that, it asks for confirmation before doing anything unless
    -Yes is passed, and it never requests elevation -- nothing it does needs
    admin rights.

    Steps: uv sync -> `akasha init` (mint the first token, or detect one
    already exists) -> start the daemon if one isn't already answering ->
    verify the token actually authenticates against *this* daemon -> open
    the web UI.

    Autostart-at-logon is deliberately out of scope here; see T12.5 for the
    packaged installer that wires that up using the supervisor-loop pattern
    already validated in scripts/windows-service/lib.ps1 (Task Scheduler's
    own RestartOnFailure was empirically found unreliable -- see
    docs/dogfood/windows-service.md).

.PARAMETER Yes
    Skip the confirmation prompt (for non-interactive/scripted runs).

.PARAMETER IncludeTokenInUrl
    Put the freshly minted bearer token in the opened URL's `?token=`
    query string (T12.3) instead of the clipboard. Off by default: a
    bearer token is a full human write credential, and browser history
    commits the pre-`history.replaceState` URL before the page can strip
    it -- with profile sync on, that's a credential leaving the machine.
    The clipboard is the safer default (Windows clipboard sync is off by
    default, unlike browser history sync).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1 -Yes
#>
param(
    [switch]$Yes,
    [switch]$IncludeTokenInUrl
)

$ErrorActionPreference = 'Continue'
$Repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$BaseUrl = "http://127.0.0.1:7433"
$ConfigDir = Join-Path $env:APPDATA "tm-daemon"
$TokenPattern = '^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$'

function Fail([string]$Message) {
    Write-Host "error: $Message" -ForegroundColor Red
    exit 1
}

Write-Host "akasha setup" -ForegroundColor Cyan
Write-Host "  repo:   $Repo"
Write-Host "  config: $ConfigDir  (default location, spec Sec3 -- never a scratch path)"
Write-Host ""

if (-not $Yes) {
    $resp = Read-Host "This runs 'uv sync' and may create/start a daemon writing to $ConfigDir. Continue? [y/N]"
    if ($resp -notmatch '^[Yy]') {
        Write-Host "Aborted -- nothing was changed."
        exit 0
    }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Fail "uv is not installed. Get it from https://docs.astral.sh/uv/ and re-run this script."
}

Set-Location $Repo

Write-Host "`n[1/6] uv sync..." -ForegroundColor Cyan
uv sync
if ($LASTEXITCODE -ne 0) { Fail "uv sync failed (exit $LASTEXITCODE)." }

# [2/6] `akasha init` runs BEFORE anything starts the daemon or otherwise
# touches the DB, and we branch on ITS exit code (0 = freshly minted, 4 =
# a token already exists) -- never on "does a DB file exist on disk".
# A DB file can exist with zero tokens (e.g. a prior run that crashed
# between DB-create and token-mint); branching on file presence would then
# permanently take the wrong branch on every future run. `init` itself
# already treats "a token exists" as the one safe/idempotent no-op case
# (build-plan T12.1) -- this script relies on that, it does not re-derive it.
Write-Host "`n[2/6] akasha init..." -ForegroundColor Cyan
$initOutput = & uv run akasha init --name "setup" 2>&1
$initExit = $LASTEXITCODE
$freshToken = $null
if ($initExit -eq 0) {
    $tokenLine = $initOutput | Where-Object { $_ -match $TokenPattern } | Select-Object -First 1
    if (-not $tokenLine) {
        Write-Host ($initOutput -join "`n")
        Fail "akasha init exited 0 but no bearer-token-shaped line was found in its output -- see the raw output above."
    }
    $freshToken = $tokenLine.ToString().Trim()
    Write-Host "Minted a new human token." -ForegroundColor Green
} elseif ($initExit -eq 4) {
    Write-Host "A human token already exists on this machine -- skipping mint (this is expected on a re-run)." -ForegroundColor Yellow
} else {
    Write-Host ($initOutput -join "`n")
    Fail "akasha init failed unexpectedly (exit $initExit)."
}

# [3/6] + [4/6] Start the daemon only if nothing is already answering.
Write-Host "`n[3/6] Checking for a running daemon..." -ForegroundColor Cyan
$daemonUp = $false
try {
    $health = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 2
    if ($health.status -eq "ok") { $daemonUp = $true }
} catch {}

if ($daemonUp) {
    Write-Host "Daemon already running at $BaseUrl." -ForegroundColor Green
} else {
    Write-Host "`n[4/6] Starting the daemon (new window)..." -ForegroundColor Cyan
    $proc = Start-Process -FilePath "uv" -ArgumentList @("run", "akasha", "daemon") `
        -WorkingDirectory $Repo -PassThru -WindowStyle Normal
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $deadline) {
        if ($proc.HasExited) {
            Fail "the daemon process exited immediately (exit $($proc.ExitCode)) -- port 7433 may already be held by something else that isn't answering /health the same way, or check its console window for the real error."
        }
        try {
            $health = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 2
            if ($health.status -eq "ok") { $daemonUp = $true; break }
        } catch {}
        Start-Sleep -Milliseconds 500
    }
    if (-not $daemonUp) { Fail "daemon did not answer $BaseUrl/health within 15s." }
    Write-Host "Daemon is up." -ForegroundColor Green
}

# [5/6] A freshly minted token was written against the DB `init` resolved
# from ITS OWN config resolution -- prove it actually authenticates against
# THIS daemon before doing anything with it, in case the running daemon was
# started with a different --config (different db_path) than the default
# one `init` just wrote to.
if ($freshToken) {
    Write-Host "`n[5/6] Verifying the new token against the running daemon..." -ForegroundColor Cyan
    try {
        $null = Invoke-RestMethod -Uri "$BaseUrl/v1/tokens" -Headers @{ Authorization = "Bearer $freshToken" } -TimeoutSec 5
    } catch {
        Fail "the freshly minted token was rejected (401) by the daemon at $BaseUrl -- it is likely serving a different --config/db_path than the default akasha init just wrote to. Check for a non-default daemon already running."
    }
    Write-Host "Token verified." -ForegroundColor Green
}

# [6/6] Open the web UI. Token goes on the clipboard by default (see
# -IncludeTokenInUrl doc comment above for why), never silently into the URL.
Write-Host "`n[6/6] Opening the web UI..." -ForegroundColor Cyan
if ($freshToken -and $IncludeTokenInUrl) {
    Start-Process "$BaseUrl/dashboard?token=$freshToken"
} elseif ($freshToken) {
    Set-Clipboard -Value $freshToken
    Write-Host "Your bearer token is on the clipboard (not the URL / browser history)." -ForegroundColor Yellow
    Write-Host "Paste it into the auth bar at the top of the page that's about to open."
    Start-Process "$BaseUrl/dashboard"
} else {
    Start-Process "$BaseUrl/dashboard"
}

Write-Host "`nDone." -ForegroundColor Cyan
Write-Host "  Config/DB:          $ConfigDir"
Write-Host "  Web UI:             $BaseUrl/"
Write-Host "  Register a vault:   uv run akasha sync add <path-to-your-notes> --token <bearer>"
Write-Host "  Stop the daemon:    close its console window (or Ctrl+C inside it)"
if ($freshToken) {
    Write-Host "  Lost the token?     it cannot be recovered -- start the daemon and use its" -ForegroundColor DarkGray
    Write-Host "                      web UI (once logged in) or 'akasha token create' to mint a" -ForegroundColor DarkGray
    Write-Host "                      new one; 'akasha init' will not mint a second one." -ForegroundColor DarkGray
}
