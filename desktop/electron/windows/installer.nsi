; The Mightling desktop app's Windows installer (DESKTOP_PARITY Phase 3 item 12, 1.7.1): NSIS, per
; user (no administrator, as install.ps1), unsigned like the rest of the Windows preview. It installs
; the packaged app (Forge's out\Mightling-win32-<arch>) under %LOCALAPPDATA%\Programs\Mightling\App,
; beside install.ps1's bin, adds a Start menu entry and the uninstaller Windows lists in Apps.
; Built by windows.yml:
;   makensis /DVERSION=1.7.1 /DARCH=arm64 /DSOURCE=<packaged app folder> /DOUTFILE=<installer.exe> installer.nsi

!include "MUI2.nsh"

!ifndef VERSION
  !error "VERSION is required"
!endif
!ifndef ARCH
  !error "ARCH is required"
!endif
!ifndef SOURCE
  !error "SOURCE (the packaged app's folder) is required"
!endif
!ifndef OUTFILE
  !define OUTFILE "Mightling-Setup-${VERSION}-${ARCH}.exe"
!endif

!define PRODUCT "Mightling"
!define EXE "Mightling.exe"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Mightling"

Name "${PRODUCT}"
OutFile "${OUTFILE}"
Unicode true
RequestExecutionLevel user
InstallDir "$LOCALAPPDATA\Programs\Mightling\App"
InstallDirRegKey HKCU "${UNINSTALL_KEY}" "InstallLocation"
SetCompressor /SOLID lzma
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${PRODUCT}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "FileDescription" "${PRODUCT} desktop app installer"
VIAddVersionKey "CompanyName" "Dreamference"
VIAddVersionKey "LegalCopyright" "Copyright (C) 2026 Dreamference contributors. AGPL-3.0-or-later."

!define MUI_ICON "${SOURCE}\..\..\resources\icon.ico"
!define MUI_UNICON "${SOURCE}\..\..\resources\icon.ico"
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXE}"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "Install"
  SetShellVarContext current
  ; A running app holds its files: ask it to close first (it quits on its own when its window closes).
  nsExec::Exec 'taskkill /im "${EXE}"'
  Sleep 1000
  RMDir /r "$INSTDIR"
  SetOutPath "$INSTDIR"
  File /r "${SOURCE}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall Mightling.exe"
  CreateShortCut "$SMPROGRAMS\${PRODUCT}.lnk" "$INSTDIR\${EXE}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${PRODUCT}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "Dreamference"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${EXE}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall Mightling.exe"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall Mightling.exe" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  nsExec::Exec 'taskkill /im "${EXE}"'
  Sleep 1000
  Delete "$SMPROGRAMS\${PRODUCT}.lnk"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  ; The links the app registered for itself (ling://, mightling://) go with it; ~\.mightling stays.
  DeleteRegKey HKCU "Software\Classes\ling"
  DeleteRegKey HKCU "Software\Classes\mightling"
SectionEnd
