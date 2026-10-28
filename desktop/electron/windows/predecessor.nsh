; Removes the app this installer replaces (1.9.0 renamed it: Mightling to Ragon). EXEMPT from
; scripts/rename_ragon.py: it names the old install on purpose. Included by installer.nsi; delete this
; file, and its include and call there, when 2.0 drops the transition.
;
; Done by hand, not through the old uninstaller: that one moves its own folder aside, which cannot
; work from inside it. What goes: the old app (asked to close first), its Start menu and desktop
; shortcuts, its uninstall entry and its link registrations. What stays: the old data folder (the
; new app moves it at its first start, data-folder.ts), the agent's home, and the taskbar pin, which
; the new app repoints at itself at its first start (app-identity.ts).

!define PREDECESSOR_NAME "Mightling"
!define PREDECESSOR_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Mightling"

Function RemovePredecessor
  ReadRegStr $R0 HKCU "${PREDECESSOR_KEY}" "InstallLocation"
  StrCmp $R0 "" 0 have_location
    StrCpy $R0 "$LOCALAPPDATA\Programs\${PREDECESSOR_NAME}\App"
  have_location:
  IfFileExists "$R0\${PREDECESSOR_NAME}.exe" 0 shortcuts
    SetOutPath "$TEMP"
    nsExec::Exec 'taskkill /im "${PREDECESSOR_NAME}.exe"'
    Pop $R1
    Sleep 2000
    RMDir /r "$R0"
    RMDir "$LOCALAPPDATA\Programs\${PREDECESSOR_NAME}"
    DetailPrint "Removed the earlier ${PREDECESSOR_NAME} app from $R0."
  shortcuts:
  Delete "$SMPROGRAMS\${PREDECESSOR_NAME}.lnk"
  Delete "$DESKTOP\${PREDECESSOR_NAME}.lnk"
  DeleteRegKey HKCU "${PREDECESSOR_KEY}"
  DeleteRegKey HKCU "Software\Classes\ling"
  DeleteRegKey HKCU "Software\Classes\mightling"
FunctionEnd
