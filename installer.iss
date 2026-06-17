; Inno Setup — установщик Re.form CRM.
; Собирает dist\ReformCRM (вывод PyInstaller) в один setup.exe.
; Версия передаётся из CI:  ISCC.exe /DMyAppVersion=1.2.3 installer.iss

#define MyAppName "Re.form CRM"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppExe "ReformCRM.exe"
#define MyAppPublisher "Re.form Cosmetology"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\ReformCRM
DefaultGroupName=Re.form CRM
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer_out
OutputBaseFilename=ReformCRM-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
CloseApplications=yes

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Files]
Source: "dist\ReformCRM\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Re.form CRM"; Filename: "{app}\{#MyAppExe}"
Name: "{userdesktop}\Re.form CRM"; Filename: "{app}\{#MyAppExe}"

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Запустить Re.form CRM"; Flags: nowait postinstall skipifsilent
