!define WANDAO_LEGACY_UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\1b0cbfbe-638f-5e34-9149-e794da0edaeb"
!define WANDAO_LEGACY_INSTALL_DIR "$LOCALAPPDATA\Programs\wandao"
!define WANDAO_LEGACY_MAIN_EXE "${WANDAO_LEGACY_INSTALL_DIR}\Wandao.exe"
!define WANDAO_LEGACY_UNINSTALLER "${WANDAO_LEGACY_INSTALL_DIR}\Uninstall Wandao.exe"

; Tauri enables MUI_HEADERIMAGE when the configured branded bitmap exists.
; Keep it on the conventional right side so installer text remains left-aligned.
!define MUI_HEADERIMAGE_RIGHT

; The 1.3.x Electron installer and the 1.4.x Tauri installer use different
; install directories and uninstall keys. Remove only a verified legacy shell
; before Tauri writes any files. User data under $APPDATA\wandao is never
; touched by this migration hook.
!macro NSIS_HOOK_PREINSTALL
  ${GetOptions} $CMDLINE "/SKIPLEGACYUNINSTALL" $R0
  ${IfNot} ${Errors}
    DetailPrint "Skipping legacy Wandao uninstall by explicit command-line request."
    Goto wandao_legacy_done
  ${EndIf}

  ClearErrors
  ReadRegStr $R0 HKCU "${WANDAO_LEGACY_UNINSTALL_KEY}" "DisplayName"
  IfErrors wandao_legacy_not_registered
  StrCmp $R0 "" wandao_legacy_untrusted

  ReadRegStr $R1 HKCU "${WANDAO_LEGACY_UNINSTALL_KEY}" "DisplayVersion"
  StrCmp $R1 "" wandao_legacy_untrusted

  ; Accept only 1.3.x and require one of the two historical display-name
  ; shapes: "Wandao <version>" (1.3.0-1.3.8) or the later localized name,
  ; which has a four-character prefix before "Wandao <version>".
  ${VersionCompare} "$R1" "1.3.0" $R2
  StrCmp $R2 "2" wandao_legacy_untrusted
  ${VersionCompare} "$R1" "1.4.0" $R2
  StrCmp $R2 "2" 0 wandao_legacy_untrusted
  ${StrLoc} $R2 $R0 "Wandao $R1" ">"
  StrCmp $R2 "0" wandao_legacy_display_name_found
  StrCmp $R2 "4" 0 wandao_legacy_untrusted
  wandao_legacy_display_name_found:
  StrCpy $R3 "Wandao $R1"
  StrLen $R4 $R3
  IntOp $R4 $R4 + $R2
  StrLen $R5 $R0
  StrCmp $R4 $R5 0 wandao_legacy_untrusted

  ReadRegStr $R2 HKCU "${WANDAO_LEGACY_UNINSTALL_KEY}" "Publisher"
  StrCmp $R2 "tllovesxs" 0 wandao_legacy_untrusted

  ReadRegStr $R2 HKCU "${WANDAO_LEGACY_UNINSTALL_KEY}" "DisplayIcon"
  StrCmp $R2 "${WANDAO_LEGACY_MAIN_EXE},0" 0 wandao_legacy_untrusted

  ReadRegStr $R2 HKCU "${WANDAO_LEGACY_UNINSTALL_KEY}" "UninstallString"
  StrCpy $R3 '$\"${WANDAO_LEGACY_UNINSTALLER}$\" /currentuser'
  StrCmp $R2 $R3 0 wandao_legacy_untrusted

  ; Read and validate QuietUninstallString, but do not execute registry text.
  ; Reconstructing the command from a fixed path and fixed arguments prevents
  ; extra arguments or shell metacharacters from being injected through HKCU.
  ReadRegStr $R2 HKCU "${WANDAO_LEGACY_UNINSTALL_KEY}" "QuietUninstallString"
  StrCpy $R3 '$\"${WANDAO_LEGACY_UNINSTALLER}$\" /currentuser /S'
  StrCmp $R2 $R3 0 wandao_legacy_untrusted

  ${IfNot} ${FileExists} "${WANDAO_LEGACY_MAIN_EXE}"
    Goto wandao_legacy_untrusted
  ${EndIf}
  ${IfNot} ${FileExists} "${WANDAO_LEGACY_UNINSTALLER}"
    Goto wandao_legacy_untrusted
  ${EndIf}

  ; Never terminate a matching Wandao process from the installer. This
  ; name-only check can also match a manually launched or newer Wandao build,
  ; so the user-facing message must not claim a specific version.
  nsis_tauri_utils::FindProcessCurrentUser "Wandao.exe"
  Pop $R0
  ; FindProcessCurrentUser returns 1 only when no matching process exists.
  ; Treat an unexpected plugin result as unsafe instead of starting uninstall.
  StrCmp $R0 "1" wandao_legacy_process_not_running
  SetErrorLevel 1
  Abort "检测到 Wandao 正在运行。请关闭所有 Wandao 窗口，等待正在执行的任务结束后，再重新运行安装程序。$\r$\nWandao is running. Close all Wandao windows, wait for active tasks to finish, then run the installer again."

  wandao_legacy_process_not_running:

  ClearErrors
  ExecWait '"${WANDAO_LEGACY_UNINSTALLER}" /currentuser /S' $R0
  IfErrors wandao_legacy_uninstall_failed
  StrCmp $R0 "0" 0 wandao_legacy_uninstall_failed

  ; A successful exit is not enough: refuse to install alongside a legacy
  ; executable or a still-registered legacy package.
  ClearErrors
  ReadRegStr $R0 HKCU "${WANDAO_LEGACY_UNINSTALL_KEY}" "DisplayName"
  IfErrors wandao_legacy_registry_removed
  Goto wandao_legacy_uninstall_failed

  wandao_legacy_registry_removed:
  ${If} ${FileExists} "${WANDAO_LEGACY_MAIN_EXE}"
    Goto wandao_legacy_uninstall_failed
  ${EndIf}
  Goto wandao_legacy_done

  wandao_legacy_not_registered:
  ; A runnable legacy copy without its uninstall metadata cannot be verified
  ; safely and would otherwise leave two independently launchable versions.
  ${If} ${FileExists} "${WANDAO_LEGACY_MAIN_EXE}"
    Goto wandao_legacy_untrusted
  ${EndIf}
  Goto wandao_legacy_done

  wandao_legacy_untrusted:
  SetErrorLevel 1
  Abort "An existing Wandao 1.3.x installation could not be verified. Repair or uninstall it before installing Wandao 1.4.0."

  wandao_legacy_uninstall_failed:
  SetErrorLevel 1
  Abort "Wandao 1.3.x could not be removed cleanly. Wandao 1.4.0 was not installed."

  wandao_legacy_done:
!macroend

; Tauri's stock template targets $APPDATA\${BUNDLEID}; Wandao deliberately
; preserves Electron's historical $APPDATA\wandao data directory instead.
!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    SetShellVarContext current
    RMDir /r "$APPDATA\wandao"
  ${EndIf}
!macroend
