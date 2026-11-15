; Instant Share's installer (NSIS), made by build.py:
;   makensis /DVERSION=1.3.3 /DPROGRAM_DIR=<the folder PyInstaller made> /DOUTFILE=<...Setup.exe> build\installer.nsi
;
; The program is a folder: Instant Share.exe and its resources folder (Python, the libraries, the icons). Until 1.3.0
; it was one self-unpacking .exe; 1.3.1 called the folder _internal (PyInstaller's name), removed when updating.
;
; Installs for everyone in "C:\Program Files\SCAFFApps\Instant Share", like every SCAFFApps app, with shortcuts in the
; Start menu's SCAFFApps folder and on the desktop, and an entry in Windows' list of installed programs. The history,
; settings and keys stay in %LOCALAPPDATA%\InstantShare and are not touched, by installing or by uninstalling.
; It does not copy itself into the program's folder: with that, Microsoft Defender's machine-learning check took the
; installer for a trojan (Bearfoos.B!ml, 16 Nov 2026); "Get the app" sends people to the Releases page instead.
;   /S      no pages (the SCAFFApps Suite and Instant Share's own updates use this)
;   /run    open Instant Share when done (as the user, not as administrator)

Unicode true
ManifestDPIAware true
RequestExecutionLevel admin
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "StrFunc.nsh"
!include "x64.nsh"
${UnStrStr}

!define APP "Instant Share"
!define EXE "Instant Share.exe"
!define UNINSTALLER "Uninstall Instant Share.exe"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\com.scaffapps.instantshare"
!define RUN_KEY "Software\Microsoft\Windows\CurrentVersion\Run"

Name "${APP}"
OutFile "${OUTFILE}"
InstallDir "$PROGRAMFILES64\SCAFFApps\${APP}"
BrandingText "SCAFFApps"
Icon "${__FILEDIR__}\icon.ico"
UninstallIcon "${__FILEDIR__}\icon.ico"

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APP}"
VIAddVersionKey "CompanyName" "SCAFFApps"
VIAddVersionKey "FileDescription" "${APP} installer"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "Copyright © 2026 SCAFFApps"

!define MUI_ICON "${__FILEDIR__}\icon.ico"
!define MUI_UNICON "${__FILEDIR__}\icon.ico"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Open ${APP}"
!define MUI_FINISHPAGE_RUN_FUNCTION OpenAsUser
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

; Opened through Explorer, so it runs as the signed-in user, not with the installer's administrator rights.
Function OpenAsUser
  Exec '"$WINDIR\explorer.exe" "$INSTDIR\${EXE}"'
FunctionEnd

; Instant Share running (this one, or an older single-file copy): it is closed, so its files can be replaced.
; The old single files are Instant-Share-1.x.x-portable.exe and Text-Share-1.x.x-portable.exe: "Instant-Share-1*",
; never "Instant-Share-*", which would also close this installer (Instant-Share-Setup-...).
!macro CloseInstantShare
  nsExec::Exec 'taskkill /IM "${EXE}" /T'
  Sleep 1500
  nsExec::Exec 'taskkill /F /IM "${EXE}" /T'
  nsExec::Exec 'taskkill /F /IM "Instant-Share-1*" /T'
  nsExec::Exec 'taskkill /F /IM "Text-Share-1*" /T'
  Sleep 800
!macroend

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "${APP} needs 64-bit Windows 10 or 11." /SD IDOK
    Abort
  ${EndIf}
  SetRegView 64
  ; an earlier install in a folder of the user's choosing: the update goes there
  ReadRegStr $0 HKLM "${UNINSTALL_KEY}" "InstallLocation"
  ${If} $0 != ""
  ${AndIf} ${FileExists} "$0\${EXE}"
    StrCpy $INSTDIR $0
  ${EndIf}
FunctionEnd

Section "Install"
  SetShellVarContext all
  !insertmacro CloseInstantShare
  SetOutPath "$INSTDIR"
  RMDir /r "$INSTDIR\resources"                      ; the version before's libraries: nothing of it left behind
  RMDir /r "$INSTDIR\_internal"                      ; (1.3.1's name for that folder)
  File /r "${PROGRAM_DIR}\*"
  Delete "$INSTDIR\Instant-Share-Setup-*.exe"         ; a copy an early 1.3.0 test build left there
  WriteUninstaller "$INSTDIR\${UNINSTALLER}"

  CreateDirectory "$SMPROGRAMS\SCAFFApps"
  CreateShortCut "$SMPROGRAMS\SCAFFApps\${APP}.lnk" "$INSTDIR\${EXE}" "" "$INSTDIR\${EXE}" 0
  CreateShortCut "$DESKTOP\${APP}.lnk" "$INSTDIR\${EXE}" "" "$INSTDIR\${EXE}" 0

  WriteRegStr HKLM "${UNINSTALL_KEY}" "DisplayName" "${APP} ${VERSION}"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "Publisher" "SCAFFApps"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${EXE}"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\${UNINSTALLER}"'
  WriteRegStr HKLM "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\${UNINSTALLER}" /S'
  WriteRegStr HKLM "${UNINSTALL_KEY}" "URLInfoAbout" "https://www.scaffapps.com"
  WriteRegDWORD HKLM "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINSTALL_KEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  WriteRegDWORD HKLM "${UNINSTALL_KEY}" "EstimatedSize" $0

  ${GetParameters} $1
  ${GetOptions} $1 "/run" $2
  ${IfNot} ${Errors}
    Call OpenAsUser
  ${EndIf}
SectionEnd

Section "Uninstall"
  SetShellVarContext all
  SetRegView 64
  nsExec::Exec 'taskkill /IM "${EXE}" /T'
  Sleep 1500
  nsExec::Exec 'taskkill /F /IM "${EXE}" /T'
  Sleep 800
  RMDir /r "$INSTDIR\resources"
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\${EXE}"
  Delete "$INSTDIR\Instant-Share-Setup-*.exe"
  Delete "$INSTDIR\${UNINSTALLER}"
  RMDir "$INSTDIR"
  RMDir "$INSTDIR\.."
  Delete "$SMPROGRAMS\SCAFFApps\${APP}.lnk"
  RMDir "$SMPROGRAMS\SCAFFApps"
  Delete "$DESKTOP\${APP}.lnk"
  DeleteRegKey HKLM "${UNINSTALL_KEY}"
  ; start with Windows, if it pointed here (the signed-in user's own setting)
  ReadRegStr $0 HKCU "${RUN_KEY}" "${APP}"
  ${UnStrStr} $1 $0 "$INSTDIR\${EXE}"
  ${If} $1 != ""
    DeleteRegValue HKCU "${RUN_KEY}" "${APP}"
  ${EndIf}
SectionEnd
