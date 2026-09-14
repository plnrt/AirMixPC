#define AppName "AirMix PC"
#define AppVersion "1.1.0"
#define AppPublisher "AirMix PC contributors"
#define AppExeName "AirMixPC.exe"

[Setup]
AppId={{D60702CA-AB37-4CC1-98B0-92239E00E9D8}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/Kylepossible/UxPlayEnhanced
AppSupportURL=https://github.com/Kylepossible/UxPlayEnhanced
AppUpdatesURL=https://github.com/Kylepossible/UxPlayEnhanced
DefaultDirName={autopf}\AirMixPC
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
InfoBeforeFile=..\installer\WELCOME.txt
InfoAfterFile=..\installer\FINISH.txt
OutputDir=..\dist\installer
OutputBaseFilename=AirMixPC-Setup-{#AppVersion}
SetupIconFile=..\assets\UxPlayEnhanced.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
UsedUserAreasWarning=no
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription=AirPlay audio receiver and Windows audio mixer
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}

[Languages]
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startup"; Description: "Запускати AirMix PC при вході у Windows"; GroupDescription: "Додаткові параметри:"; Flags: checkedonce
Name: "desktopicon"; Description: "Створити ярлик на робочому столі"; GroupDescription: "Додаткові параметри:"; Flags: unchecked

[Files]
Source: "..\dist\AirMixPC\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.ps1,*.cmd,*.bat,*.py,*.pyw"
Source: "configure-system.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion

[Dirs]
Name: "{localappdata}\AirMixPC"

[Icons]
Name: "{autoprograms}\AirMix PC"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autoprograms}\Repair AirMix PC"; Filename: "{app}\installer\AirMixPC-Setup-{#AppVersion}.exe"; Parameters: "/SILENT /NORESTART"; WorkingDir: "{app}\installer"
Name: "{autodesktop}\AirMix PC"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{userstartup}\AirMix PC"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: startup

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\installer\configure-system.ps1"" -Mode Install -InstallDir ""{app}"" -DataDir ""{localappdata}\AirMixPC"""; Flags: runhidden waituntilterminated; StatusMsg: "Налаштування приватної мережі та брандмауера..."
Filename: "{app}\{#AppExeName}"; Description: "Запустити AirMix PC"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\installer\configure-system.ps1"" -Mode Uninstall -InstallDir ""{app}"" -DataDir ""{localappdata}\AirMixPC"""; Flags: runhidden waituntilterminated; RunOnceId: "AirMixPCSystemCleanup"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{cmd}'), '/c taskkill /F /IM AirMixPC.exe /T >nul 2>nul & taskkill /F /IM uxplay.exe /T >nul 2>nul', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    CopyFile(ExpandConstant('{srcexe}'),
      ExpandConstant('{app}\installer\AirMixPC-Setup-{#AppVersion}.exe'), False);
end;
