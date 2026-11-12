#ifndef AppVersion
  #error Run packaging/build.ps1 so the application version is supplied.
#endif
[Setup]
AppId={{7A28913C-6C40-4F4A-9A9F-9EB2D9EF3C71}
AppName=Who is disaster
AppVersion={#AppVersion}
AppPublisher=Who is disaster
DefaultDirName={localappdata}\Programs\CodeCompanion
DefaultGroupName=Who is disaster
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
OutputDir=..\dist\installer
OutputBaseFilename=WhoIsDisaster-Setup-{#AppVersion}
SetupIconFile=..\assets\icons\app.ico
LicenseFile=..\LICENSE
UninstallDisplayIcon={app}\CodeCompanion.exe
Compression=lzma2/fast
SolidCompression=yes
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "Windows 로그인할 때 비서 자동 실행"; Flags: checkedonce
Name: "desktopicon"; Description: "바탕 화면 바로가기 만들기"; Flags: unchecked

[Files]
Source: "..\dist\CodeCompanion\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\NOTICE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\PROVENANCE.md"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; Version 0.2.0 accidentally collected incompatible Poppler ICU DLLs.
Type: files; Name: "{app}\_internal\icuuc.dll"
Type: files; Name: "{app}\_internal\icudt78.dll"

[Icons]
Name: "{group}\Who is disaster"; Filename: "{app}\CodeCompanion.exe"
Name: "{autodesktop}\Who is disaster"; Filename: "{app}\CodeCompanion.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "CodeCompanion"; ValueData: """{app}\CodeCompanion.exe"" --startup"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\CodeCompanion.exe"; Description: "비서 실행"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and not WizardIsTaskSelected('autostart') then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'CodeCompanion');
end;
