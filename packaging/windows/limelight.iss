; Inno Setup script for the Limelight desktop app.
;
; Expects the PyInstaller one-directory bundle at dist\limelight\ (see
; packaging\limelight.spec) and writes the installer to dist\installer\.
;
; Build with:
;     pyinstaller packaging\limelight.spec --noconfirm
;     iscc packaging\windows\limelight.iss
;
; Pass /DAppVersion=x.y.z to override the version.

#ifndef AppVersion
  #define AppVersion "0.0.1"
#endif

#define AppName "Limelight"
#define AppPublisher "Mike Hull"
#define AppExeName "limelight.exe"

[Setup]
AppId={{8B5F2C41-6E3A-4D57-9A1B-2F0C7E48D913}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\..\dist\installer
OutputBaseFilename=limelight-{#AppVersion}-windows-x64
SetupIconFile=limelight.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The bundle is 64-bit only.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Per-user installs need no elevation; lowest keeps that possible.
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "associate"; Description: "Open .limelight and .ll packages with {#AppName}"; GroupDescription: "File associations:"

[Files]
Source: "..\..\dist\limelight\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Registry]
; A package is normally a folder, but archives use the same extensions.
Root: HKA; Subkey: "Software\Classes\.limelight"; ValueType: string; ValueName: ""; ValueData: "Limelight.Package"; Flags: uninsdeletevalue; Tasks: associate
Root: HKA; Subkey: "Software\Classes\.ll"; ValueType: string; ValueName: ""; ValueData: "Limelight.Package"; Flags: uninsdeletevalue; Tasks: associate
Root: HKA; Subkey: "Software\Classes\Limelight.Package"; ValueType: string; ValueName: ""; ValueData: "Limelight Package"; Flags: uninsdeletekey; Tasks: associate
Root: HKA; Subkey: "Software\Classes\Limelight.Package\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExeName},0"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\Limelight.Package\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExeName}"" ""%1"""; Tasks: associate

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
