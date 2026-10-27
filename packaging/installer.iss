; Inno Setup script: wraps dist\RaceEngineer\ into RaceEngineer-Setup-<version>.exe.
; Built by .github/workflows/release.yml:  iscc /DAppVersion=0.1.0 packaging\installer.iss
;
; Installs for the current user only (no admin prompt) into %LOCALAPPDATA%\Programs\RaceEngineer.
; Settings and data live separately in %LOCALAPPDATA%\RaceEngineer and survive updates/uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6C0D3E8A-2F4B-4C1E-9B7A-5E2D1F0A9C11}
AppName=Race Engineer
AppVersion={#AppVersion}
AppPublisher=Race Engineer
DefaultDirName={localappdata}\Programs\RaceEngineer
DefaultGroupName=Race Engineer
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=RaceEngineer-Setup-{#AppVersion}
Compression=lzma2/fast
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
UninstallDisplayName=Race Engineer

[Files]
Source: "..\dist\RaceEngineer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\docs\telemetry-guide.md"; DestDir: "{app}"; DestName: "Telemetry guide.txt"

[Icons]
Name: "{group}\Race Engineer"; Filename: "{app}\RaceEngineer.exe"
Name: "{group}\Race Engineer setup"; Filename: "{app}\RaceEngineer.exe"; Parameters: "--setup"
Name: "{group}\Telemetry guide"; Filename: "{app}\Telemetry guide.txt"
Name: "{group}\Uninstall Race Engineer"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Race Engineer"; Filename: "{app}\RaceEngineer.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Run]
Filename: "{app}\RaceEngineer.exe"; Description: "Start Race Engineer now (first-time setup)"; Flags: nowait postinstall skipifsilent
