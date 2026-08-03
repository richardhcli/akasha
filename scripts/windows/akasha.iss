; akasha Windows installer (build-plan T12.5).
;
; Design reviewed twice (Opus advisor consults, both incorporated):
;  1. Never elevated. `PrivilegesRequired=lowest` throughout, and Task
;     Scheduler is deliberately NOT used for autostart -- lib.ps1's own
;     header (scripts\windows-service\lib.ps1) already documents
;     Register-ScheduledTask/schtasks.exe returning Access Denied for a
;     plain non-admin AtLogOn task on this exact host, and a `-Verb RunAs`
;     fallback would hang a `/SILENT` install on a UAC prompt. A
;     `{userstartup}` Startup-folder shortcut needs no elevation and runs
;     in the correct (actually-logged-in) user context by construction.
;  2. Crash recovery still needs something outside the OS's own retry
;     logic (same empirical finding as above): the Startup-folder shortcut
;     and the Start Menu shortcut both point at
;     scripts\windows\run-tray-supervised.bat, not directly at
;     `akasha.exe tray` -- see that file's own header comment for the
;     supervisor-loop + exit-code-42-means-quit contract with
;     src\akasha\tray.py.
;
; Rebrand invariant (build-plan rule 0.6): the product name may appear in
; installer-facing UI strings (this file), but never in what it writes to
; disk under the app's own control -- %APPDATA%\tm-daemon (spec Sec3) is
; untouched by this installer; it only places akasha.exe, the supervisor
; .bat, and shortcuts.

#define MyAppName "akasha"
#define MyAppVersion "0.1.0"
#define MyAppExeName "akasha.exe"
#define MyWrapperName "run-tray-supervised.bat"

[Setup]
AppId={{9F1D6C8E-6C1B-4B7B-9F3B-A1DA5A000001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=akasha
DefaultDirName={localappdata}\akasha
DefaultGroupName=akasha
DisableProgramGroupPage=yes
OutputDir=..\..\dist-installer
OutputBaseFilename=akasha-setup
Compression=lzma2
SolidCompression=yes
; Never require admin -- see header comment. The daemon itself never needs
; elevation either (spec Sec3: binds 127.0.0.1 only, runs as the logged-in
; user).
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyWrapperName}"; DestDir: "{app}"; Flags: ignoreversion

[Tasks]
Name: "autostart"; Description: "Start akasha automatically when you sign in (recommended -- recovers from a crash within a few seconds; a plain Startup-folder shortcut without the bundled supervisor script would not)"; GroupDescription: "Startup:"; Flags: checkedonce

[Icons]
Name: "{group}\akasha"; Filename: "{app}\{#MyWrapperName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Flags: runminimized; Comment: "Run akasha with a system-tray icon (crash-recovering)"
Name: "{group}\Uninstall akasha"; Filename: "{uninstallexe}"
Name: "{userstartup}\akasha"; Filename: "{app}\{#MyWrapperName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Flags: runminimized; Tasks: autostart

[Run]
Filename: "{app}\{#MyWrapperName}"; WorkingDir: "{app}"; Description: "Launch akasha now"; Flags: postinstall nowait skipifsilent shellexec runminimized

[UninstallDelete]
; Runtime-generated files the [Files]/[Icons] sections above don't know
; about (akasha.exe/the .bat/shortcuts are removed automatically).
Type: files; Name: "{app}\akasha-crashloop.txt"

[Code]
// Stop any already-running supervised instance (both the loop itself, by
// its distinguishing console title -- see run-tray-supervised.bat's
// `title akasha-supervisor-loop` -- and the exe it's relaunching) before
// an upgrade-in-place install or an uninstall, so the exe/bat files are
// never locked mid-operation and a killed exe is never silently
// respawned a moment later by a still-running loop. Neither taskkill call
// needs elevation (they only ever target this same user's own processes).
procedure KillSupervisedAkasha;
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/FI "WINDOWTITLE eq akasha-supervisor-loop*" /F', '',
    SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/IM akasha.exe /F', '',
    SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    KillSupervisedAkasha;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    KillSupervisedAkasha;
end;
