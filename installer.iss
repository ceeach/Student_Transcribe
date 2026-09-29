; Script generated for Inno Setup Compiler
; NotionTranscribe Windows Installer

#define MyAppName "NotionTranscribe"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "NotionTranscribe Team"
#define MyAppExeName "NotionTranscribe.exe"

[Setup]
AppId={{D37E6F4A-483B-49A1-979F-0A86532B41B7}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
UninstallDisplayIcon={app}\assets\icon.ico
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=output
OutputBaseFilename=NotionTranscribe-Setup-v1.0
SetupIconFile=assets\icon.ico
SolidCompression=yes
Compression=lzma2/max
PrivilegesRequiredOverridesAllowed=dialog commandline
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\NotionTranscribe\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
