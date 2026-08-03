<#
.SYNOPSIS
    Build a standalone akasha.exe (build-plan T12.5, vision.md Sec7.9's
    "packaged single executable (PyInstaller/Nuitka) as a later polish
    step").

.DESCRIPTION
    Packages the SAME entry point the `akasha` console script already uses
    (`src/akasha/cli/main.py`'s typer `app`) -- every existing CLI verb
    (new/get/set/rm/search/review/token/export/daemon/init/sync/tray) works
    identically from the exe, nothing here adds a second code path.

    Requires the `packaging` dependency group (PyInstaller) and, for the
    `tray` command to work from the built exe, the `tray` extra
    (pystray/Pillow) -- both optional, installed via:
        uv sync --extra tray --group packaging

    Bundles two things PyInstaller's default analysis would otherwise miss
    because they are data files, not Python imports:
      - migrations/*.sql (kernel/store.py's MIGRATIONS_DIR; see the
        sys.frozen branch added there for T12.5 -- docs/spec-questions.md).
      - src/akasha/ui/{static,templates} (the daemon-served web UI).
    `--collect-all` on uvicorn/fastapi/pystray/Pillow is intentionally
    generous (larger bundle, but a working exe on the first try) --
    uvicorn/pystray both do platform-dependent dynamic imports that
    PyInstaller's static import analysis alone can miss, and vision.md
    Sec7.9 already accepts "known Windows costs (AV false positives, bundle
    size)" for this stopgap deliberately, ahead of the eventual Rust
    static-binary migration (Sec7.12) that removes the need for PyInstaller
    entirely.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\windows\build-exe.ps1
#>
param(
    [switch]$OneDir
)

$ErrorActionPreference = 'Continue'
$Repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Repo

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "error: uv is not installed. Get it from https://docs.astral.sh/uv/" -ForegroundColor Red
    exit 1
}

Write-Host "Syncing packaging + tray dependencies..." -ForegroundColor Cyan
uv sync --extra tray --group packaging
if ($LASTEXITCODE -ne 0) { Write-Host "uv sync failed" -ForegroundColor Red; exit 1 }

$ModeArg = if ($OneDir) { "--onedir" } else { "--onefile" }

Write-Host "Running PyInstaller ($ModeArg)..." -ForegroundColor Cyan
uv run pyinstaller `
    --noconfirm `
    --clean `
    --name akasha `
    $ModeArg `
    --console `
    --add-data "migrations;migrations" `
    --add-data "src\akasha\ui;akasha\ui" `
    --collect-all uvicorn `
    --collect-all fastapi `
    --collect-all pystray `
    --collect-all PIL `
    --paths src `
    src\akasha\cli\main.py

if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller build failed (exit $LASTEXITCODE)." -ForegroundColor Red
    exit 1
}

$ExePath = if ($OneDir) { "dist\akasha\akasha.exe" } else { "dist\akasha.exe" }
if (Test-Path $ExePath) {
    Write-Host "`nBuilt: $Repo\$ExePath" -ForegroundColor Green
} else {
    Write-Host "error: build reported success but $ExePath was not found." -ForegroundColor Red
    exit 1
}
