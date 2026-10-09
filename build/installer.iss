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
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} installer
VersionInfoProductTextVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
; The third-party notices on the page after the licence, before anything is
; installed: what comes with pycangui is worth reading before it is on the
; machine, not on the way to Finish.
InfoBeforeFile=..\THIRD-PARTY-NOTICES.txt
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
; Setup asks who it is for: everybody, which needs administrator rights and
; is what it suggests, or only the person running it, which needs none.
PrivilegesRequiredOverridesAllowed=dialog commandline
; For the fileassoc task: Explorer is told to look again.
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "fileassoc"; Description: "Open .dcf and .eds files with pycangui"; GroupDescription: "File types:"

; pycangui is offered in Open with, rather than made the default: another
; CANopen tool somebody already uses for these keeps them until they choose
; otherwise, which Windows asks the first time one is opened. HKA is HKCU for
; a per-user install and HKLM for an all-users one. A file double-clicked
; while pycangui is open goes to that one (pycangui/ui/handoff.py).
[Registry]
Root: HKA; Subkey: "Software\Classes\.dcf\OpenWithProgids"; ValueType: string; ValueName: "pycangui.dcf"; ValueData: ""; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\pycangui.dcf"; ValueType: string; ValueName: ""; ValueData: "CANopen device configuration file"; Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\pycangui.dcf\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\pycangui.dcf\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.eds\OpenWithProgids"; ValueType: string; ValueName: "pycangui.eds"; ValueData: ""; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\pycangui.eds"; ValueType: string; ValueName: ""; ValueData: "CANopen electronic data sheet"; Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\pycangui.eds\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\pycangui.eds\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".dcf"; ValueData: ""; Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".eds"; ValueData: ""; Tasks: fileassoc

[Files]
Source: "..\dist\pycangui\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; An upgrade would otherwise leave the last version's shortcuts beside the
; new ones, named for a version that is no longer installed. The desktop
; pattern needs a digit-dot after the name, so a shortcut of somebody's own
; that happens to start with "pycangui " is left alone.
[InstallDelete]
Type: files; Name: "{group}\{#AppName} *.lnk"
Type: files; Name: "{autodesktop}\{#AppName} ?.*.lnk"

; Named with the version: an installed pycangui stays the version it is
; until the next installer, unlike a source folder's entry, which starts
; whatever was last pulled and is called plain pycangui.
[Icons]
Name: "{group}\{#AppName} {#AppVersion}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Third party notices"; Filename: "{app}\THIRD-PARTY-NOTICES.txt"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName} {#AppVersion}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[Messages]
; Say plainly that vendor CAN drivers are the user's to install
FinishedLabel=pycangui is installed.%n%nCAN adapter drivers (PCAN, Kvaser, Vector, IXXAT and others) are not included: install the driver from your adapter's vendor and pycangui will find it.%n%nYour hooks, EDS files and settings live in %%APPDATA%%\pycangui and are left alone by upgrades and by uninstalling.
