; NSIS installer for pycangui.  Built by build.cmd after PyInstaller, as an
; alternative to installer.iss -- use whichever compiler you have.
;
; Installs the one-directory PyInstaller build, so Qt and python-can stay as
; separate replaceable DLLs, which is what the LGPL asks for, while the user
; sees one Start menu entry and an uninstaller.
;
;   makensis /DAppVersion=0.0.1 build\installer.nsi

!ifndef AppVersion
  !define AppVersion "0.0.1"
!endif
!define AppName "pycangui"
!define AppPublisher "davhodg"
!define AppURL "https://github.com/davhodg/pycangui"
!define AppExe "pycangui.exe"
!define RegKey "Software\Microsoft\Windows\CurrentVersion\Uninstall\${AppName}"

Unicode true
Name "${AppName} ${AppVersion}"
OutFile "..\dist\${AppName}-${AppVersion}-setup.exe"
InstallDir "$LOCALAPPDATA\Programs\${AppName}"
InstallDirRegKey HKCU "Software\${AppName}" "InstallDir"
RequestExecutionLevel user      ; per user: no administrator rights needed
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show

!include "MUI2.nsh"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\${AppExe}"
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\THIRD-PARTY-NOTICES.txt"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "Show the third party notices"
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED

!insertmacro MUI_PAGE_LICENSE "..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

VIProductVersion "${AppVersion}.0"
VIAddVersionKey "ProductName" "${AppName}"
VIAddVersionKey "CompanyName" "${AppPublisher}"
VIAddVersionKey "LegalCopyright" "Copyright 2026 ${AppPublisher}. Apache-2.0."
VIAddVersionKey "FileDescription" "${AppName} CAN bus tool"
VIAddVersionKey "FileVersion" "${AppVersion}"

Section "pycangui" SecMain
  SectionIn RO
  SetOutPath "$INSTDIR"
  File /r "..\dist\pycangui\*.*"

  CreateDirectory "$SMPROGRAMS\${AppName}"
  CreateShortCut "$SMPROGRAMS\${AppName}\${AppName}.lnk" "$INSTDIR\${AppExe}"
  CreateShortCut "$SMPROGRAMS\${AppName}\Third party notices.lnk" \
      "$INSTDIR\THIRD-PARTY-NOTICES.txt"
  CreateShortCut "$SMPROGRAMS\${AppName}\Uninstall ${AppName}.lnk" "$INSTDIR\uninstall.exe"

  WriteRegStr HKCU "Software\${AppName}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${RegKey}" "DisplayName" "${AppName}"
  WriteRegStr HKCU "${RegKey}" "DisplayVersion" "${AppVersion}"
  WriteRegStr HKCU "${RegKey}" "Publisher" "${AppPublisher}"
  WriteRegStr HKCU "${RegKey}" "URLInfoAbout" "${AppURL}"
  WriteRegStr HKCU "${RegKey}" "DisplayIcon" "$INSTDIR\${AppExe}"
  WriteRegStr HKCU "${RegKey}" "UninstallString" "$INSTDIR\uninstall.exe"
  WriteRegDWORD HKCU "${RegKey}" "NoModify" 1
  WriteRegDWORD HKCU "${RegKey}" "NoRepair" 1
  WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

Section "Desktop shortcut" SecDesktop
  CreateShortCut "$DESKTOP\${AppName}.lnk" "$INSTDIR\${AppExe}"
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecMain} "The application and everything it needs."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "Put a shortcut on the desktop."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Function .onInit
  ; A desktop shortcut is opt in, as in the Inno Setup script
  SectionSetFlags ${SecDesktop} 0
FunctionEnd

Function .onInstSuccess
  DetailPrint "CAN adapter drivers (PCAN, Kvaser, Vector, IXXAT and others) are not"
  DetailPrint "included: install your adapter vendor's driver and pycangui will find it."
  DetailPrint "Hooks, EDS files and settings live in %APPDATA%\pycangui."
FunctionEnd

Section "Uninstall"
  ; Deliberately leaves %APPDATA%\pycangui alone: the user's hooks, back ends,
  ; EDS files and settings are theirs, not ours.
  Delete "$DESKTOP\${AppName}.lnk"
  RMDir /r "$SMPROGRAMS\${AppName}"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "${RegKey}"
  DeleteRegKey HKCU "Software\${AppName}"
SectionEnd
