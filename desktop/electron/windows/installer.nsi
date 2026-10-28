; The Mightling desktop app's Windows installer (DESKTOP_PARITY Phase 3 item 12, 1.7.1): NSIS, per
; user (no administrator, as install.ps1), unsigned like the rest of the Windows preview. It installs
; the packaged app (Forge's out\Mightling-win32-<arch>) under %LOCALAPPDATA%\Programs\Mightling\App,
; beside install.ps1's bin, adds a Start menu entry and the uninstaller Windows lists in Apps.
; Built by windows.yml:
;   makensis /DVERSION=1.7.1 /DARCH=arm64 /DSOURCE=<packaged app folder> /DOUTFILE=<installer.exe> installer.nsi
; VERSION may carry a pre-release part (1.7.0-rc.1); Windows' file version takes four numbers only,
; so it is made from NUMERIC_VERSION (1.7.0), which defaults to VERSION.

!include "MUI2.nsh"
!include "predecessor.nsh"

!ifndef VERSION
  !error "VERSION is required"
!endif
!ifndef ARCH
  !error "ARCH is required"
!endif
!ifndef SOURCE
  !error "SOURCE (the packaged app's folder) is required"
!endif
!ifndef NUMERIC_VERSION
  !define NUMERIC_VERSION "${VERSION}"
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
; Drawn at the display's scaling, not stretched from 96 DPI (blurred text at 150 %).
ManifestDPIAware true
InstallDir "$LOCALAPPDATA\Programs\Mightling\App"
InstallDirRegKey HKCU "${UNINSTALL_KEY}" "InstallLocation"
SetCompressor /SOLID lzma
VIProductVersion "${NUMERIC_VERSION}.0"
VIAddVersionKey "ProductName" "${PRODUCT}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "FileVersion" "${NUMERIC_VERSION}"
VIAddVersionKey "FileDescription" "${PRODUCT} desktop app installer"
VIAddVersionKey "CompanyName" "Dreamference"
VIAddVersionKey "LegalCopyright" "Copyright (C) 2026 Dreamference contributors. AGPL-3.0-or-later."

!define MUI_ICON "${SOURCE}\..\..\resources\icon.ico"
!define MUI_UNICON "${SOURCE}\..\..\resources\icon.ico"
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXE}"
; No folder page: the app always goes in the one per-user place, which the install empties first
; (a chosen folder could be one that holds other things).
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
; The app's data (%LOCALAPPDATA%\dev.dreamference.ragon: the window state, the preferences,
; Chromium's profile) and ling's (%USERPROFILE%\.mightling: threads, settings) are the user's, kept
; for a reinstall; the confirmation page and the log say so, and where they are.
!define MUI_UNCONFIRMPAGE_TEXT_TOP "${PRODUCT} will be removed from your computer. Your data stays: the app's in %LOCALAPPDATA%\dev.dreamference.ragon, your threads and settings in %USERPROFILE%\.mightling. Delete those folders yourself to remove it too."
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

; A running app holds its files (Mightling.exe and its helpers, the bundled ling.exe serving it), and
; Windows refuses to delete a file in use: RMDir /r then leaves part of the old app behind, without an
; error. So the folder is moved aside whole first, which succeeds only once nothing in it is in use, and
; then deleted. The app is asked to close (taskkill without /F closes its windows; it quits when the
; last one closes, stopping its ling.exe); after 10 s whatever still runs from the folder, and only
; from it (never the ling.exe install.ps1 put in ..\bin, which a terminal may be running), is ended;
; after 10 s more the install stops with a message (exit code 2 when silent) instead of mixing two
; versions. Retrying also outlasts a scanner holding a new file for a moment.
; A folder without the app in it is never touched: $0 is "moved" when $INSTDIR is now $INSTDIR.old.
!macro FREE_APP_FOLDER UN
Function ${UN}FreeAppFolder
  StrCpy $0 "absent"
  IfFileExists "$INSTDIR\${EXE}" 0 free_done
  ; The installer's own working folder must not be inside the one it moves.
  SetOutPath "$TEMP"
  ; What an earlier run could not finish deleting.
  RMDir /r "$INSTDIR.old"
  nsExec::Exec 'taskkill /im "${EXE}"'
  Pop $1
  StrCpy $2 0
  free_try:
    ClearErrors
    Rename "$INSTDIR" "$INSTDIR.old"
    IfErrors 0 free_moved
    IntOp $2 $2 + 1
    IntCmp $2 20 free_force
    IntCmp $2 40 free_failed
    Sleep 500
    Goto free_try
  free_force:
    DetailPrint "${PRODUCT} did not close; ending what still runs from $INSTDIR."
    ; By path, through the environment (a user name with a quote in it stays data); WMI gives every
    ; process's path whatever its architecture, which a 32-bit installer's Get-Process would not.
    System::Call 'kernel32::SetEnvironmentVariable(t "MIGHTLING_APP_FOLDER", t "$INSTDIR") i .r1'
    nsExec::Exec '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$$folder = $$env:MIGHTLING_APP_FOLDER.TrimEnd([char]92) + [char]92; Get-CimInstance Win32_Process | Where-Object { $$_.ExecutablePath -and $$_.ExecutablePath.StartsWith($$folder, [StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { Stop-Process -Id $$_.ProcessId -Force -ErrorAction SilentlyContinue }"'
    Pop $1
    Sleep 500
    Goto free_try
  free_failed:
    DetailPrint "Files in $INSTDIR are still in use."
    MessageBox MB_OK|MB_ICONSTOP "${PRODUCT} is still running, or another program is using its files in $INSTDIR. Close it, then run this again." /SD IDOK
    Abort "Files in $INSTDIR are still in use."
  free_moved:
    StrCpy $0 "moved"
  free_done:
FunctionEnd
!macroend
!insertmacro FREE_APP_FOLDER ""
!insertmacro FREE_APP_FOLDER "un."

Section "Install"
  SetShellVarContext current
  ; The app this one replaced (1.9.0), its shortcuts and registrations (predecessor.nsh).
  Call RemovePredecessor
  ; An earlier install is replaced whole (FreeAppFolder above).
  Call FreeAppFolder
  StrCmp $0 "moved" 0 install_files
    RMDir /r "$INSTDIR.old"
  install_files:
  SetOutPath "$INSTDIR"
  File /r "${SOURCE}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall Mightling.exe"
  CreateShortCut "$SMPROGRAMS\${PRODUCT}.lnk" "$INSTDIR\${EXE}"
  CreateShortCut "$DESKTOP\${PRODUCT}.lnk" "$INSTDIR\${EXE}"
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
  Call un.FreeAppFolder
  Delete "$SMPROGRAMS\${PRODUCT}.lnk"
  Delete "$DESKTOP\${PRODUCT}.lnk"
  StrCmp $0 "moved" 0 remove_in_place
    RMDir /r "$INSTDIR.old"
    Goto removed
  remove_in_place:
    RMDir /r "$INSTDIR"
  removed:
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  ; The links the app registered for itself (ling://, mightling://) go with it; ~\.mightling stays.
  DeleteRegKey HKCU "Software\Classes\ling"
  DeleteRegKey HKCU "Software\Classes\mightling"
  DetailPrint "Kept your data: $LOCALAPPDATA\dev.dreamference.ragon (the app) and $PROFILE\.mightling (threads, settings)."
SectionEnd
