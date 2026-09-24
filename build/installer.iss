; Inno Setup script for pycangui. Built by build.cmd after PyInstaller.
;
; Installs the one-directory PyInstaller build, so Qt and python-can stay as
; separate replaceable DLLs -- what the LGPL asks for -- while the user sees a
; single Start menu entry and an uninstaller.
;
; Download Inno Setup from https://jrsoftware.org/isinfo.php if it is missing.

#define AppName "pycangui"
#define AppPublisher "davhodg"
#define AppURL "https://github.com/davhodg/pycangui"
#define AppExe "pycangui.exe"
; build.cmd passes both, from build\version.py. 0.0.0 marks an installer
; compiled by hand, which has no version to give it.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef FileVersion
  #define FileVersion "0.0.0.0"
#endif

[Setup]
AppId={{7C2F1E90-3D5B-4C7A-9E11-PYCANGUI0001}
AppName={#AppName}
AppVersion={#AppVersion}
; The setup.exe's own version resource. The file version is numbers only,
; and its text is cut at 20 characters, so the full version -- -dev and
; commit included -- goes in the product version, which is not.
VersionInfoVersion={#FileVersion}
VersionInfoTextVersion={#FileVersion}
VersionInfoProductName={#AppName}
VersionInfoProductTextVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
InfoAfterFile=..\THIRD-PARTY-NOTICES.txt
OutputDir=..\dist
OutputBaseFilename=pycangui-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; The installer's own icon, and the one Settings > Apps shows for it. The
; shortcuts need nothing: they take theirs from pycangui.exe.
SetupIconFile=..\pycangui\resources\pycangui.ico
UninstallDisplayIcon={app}\{#AppExe}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Per-user by default so no administrator rights are needed
PrivilegesRequiredOverridesAllowed=dialog commandline

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\pycangui\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Third party notices"; Filename: "{app}\THIRD-PARTY-NOTICES.txt"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[Messages]
; Say plainly that vendor CAN drivers are the user's to install
FinishedLabel=pycangui is installed.%n%nCAN adapter drivers (PCAN, Kvaser, Vector, IXXAT and others) are not included: install the driver from your adapter's vendor and pycangui will find it.%n%nYour hooks, EDS files and settings live in %%APPDATA%%\pycangui and are left alone by upgrades and by uninstalling.
