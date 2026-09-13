!define WANDAO_LEGACY_UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\1b0cbfbe-638f-5e34-9149-e794da0edaeb"
!define WANDAO_LEGACY_INSTALL_KEY "Software\1b0cbfbe-638f-5e34-9149-e794da0edaeb"
!define WANDAO_LEGACY_INSTALL_DIR "$LOCALAPPDATA\Programs\wandao"
!define WANDAO_LEGACY_MAIN_EXE "${WANDAO_LEGACY_INSTALL_DIR}\Wandao.exe"
!define WANDAO_OLD_TAURI_UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Wandao"

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
    Goto wandao_legacy_skip_all
  ${EndIf}

  wandao_legacy_validate:
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

  ; Electron allowed users to choose an install directory. Recover the path
  ; from its product key, normalize it, and reconstruct every executable path.
  ; Registry command text is validated for consistency but never executed.
  ReadRegStr $R6 HKCU "${WANDAO_LEGACY_INSTALL_KEY}" "InstallLocation"
  StrCmp $R6 "" wandao_legacy_untrusted
  StrLen $R3 $R6
  IntCmp $R3 3 wandao_legacy_untrusted wandao_legacy_untrusted 0
  StrCpy $R4 $R6 1 1
  StrCmp $R4 ":" 0 wandao_legacy_untrusted
  StrCpy $R4 $R6 1 2
  StrCmp $R4 "\" 0 wandao_legacy_untrusted
  StrCpy $R4 $R6 "" 2
  ${StrLoc} $R5 $R4 ":" ">"
  StrCmp $R5 "" 0 wandao_legacy_untrusted
  ${StrLoc} $R5 $R6 '$\"' ">"
  StrCmp $R5 "" 0 wandao_legacy_untrusted
  ${StrLoc} $R5 $R6 "%" ">"
  StrCmp $R5 "" 0 wandao_legacy_untrusted

  ClearErrors
  GetFullPathName $R4 "$R6"
  IfErrors wandao_legacy_untrusted
  StrCmp $R4 $R6 0 wandao_legacy_untrusted

  StrCpy $R7 "$R6\Wandao.exe"
  StrCpy $R8 "$R6\Uninstall Wandao.exe"
  StrCpy $R3 "$R7,0"

  ReadRegStr $R2 HKCU "${WANDAO_LEGACY_UNINSTALL_KEY}" "DisplayIcon"
  StrCmp $R2 $R3 0 wandao_legacy_untrusted

  ReadRegStr $R2 HKCU "${WANDAO_LEGACY_UNINSTALL_KEY}" "UninstallString"
  StrCpy $R3 '$\"$R8$\" /currentuser'
  StrCmp $R2 $R3 0 wandao_legacy_untrusted

  ReadRegStr $R2 HKCU "${WANDAO_LEGACY_UNINSTALL_KEY}" "QuietUninstallString"
  StrCpy $R3 '$\"$R8$\" /currentuser /S'
  StrCmp $R2 $R3 0 wandao_legacy_untrusted

  ${IfNot} ${FileExists} "$R7"
    Goto wandao_legacy_untrusted
  ${EndIf}
  ${IfNot} ${FileExists} "$R8"
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
  MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "检测到 Wandao 正在运行。请关闭所有 Wandao 窗口，并等待正在执行的任务结束，然后点击“重试”；安装器不会强制结束程序。" /SD IDCANCEL IDRETRY wandao_legacy_validate
  SetErrorLevel 11
  Abort

  wandao_legacy_process_not_running:

  StrCpy $R5 13
  ClearErrors
  ; Electron's uninstaller scans its install directory for running processes.
  ; Running it in place makes that scan see the uninstaller itself and can
  ; leave this installer waiting forever. Copy the verified executable out of
  ; the legacy directory first, then use _?= only to tell it which directory
  ; to uninstall. The directory argument must remain last.
  CreateDirectory "$PLUGINSDIR\wandao-legacy-uninstaller"
  ClearErrors
  CopyFiles /SILENT "$R8" "$PLUGINSDIR\wandao-legacy-uninstaller"
  IfErrors wandao_legacy_uninstall_failed
  StrCpy $R3 "$PLUGINSDIR\wandao-legacy-uninstaller\Uninstall Wandao.exe"
  ExecWait '"$R3" /currentuser /S /KEEP_APP_DATA _?=$R6' $R0
  IfErrors wandao_legacy_uninstall_failed

  ; Some historical Electron uninstallers return a non-zero status after
  ; completing successfully. The runnable legacy executable disappearing is
  ; the authoritative postcondition. electron-builder defers registry cleanup
  ; until its caller exits, so remove only the two already-validated fixed keys
  ; here instead of deadlocking while waiting for its cleanup helper.
  StrCpy $R9 40
  wandao_legacy_wait_for_cleanup:
    ${IfNot} ${FileExists} "$R7"
      Goto wandao_legacy_remove_keys
    ${EndIf}
    StrCpy $R5 23

    IntOp $R9 $R9 - 1
    StrCmp $R9 "0" wandao_legacy_uninstall_failed
    Sleep 250
    Goto wandao_legacy_wait_for_cleanup

  wandao_legacy_remove_keys:
  DeleteRegKey HKCU "${WANDAO_LEGACY_UNINSTALL_KEY}"
  DeleteRegKey HKCU "${WANDAO_LEGACY_INSTALL_KEY}"
  Delete "$R8"
  Goto wandao_legacy_done

  wandao_legacy_not_registered:
  ; A runnable legacy copy without its uninstall metadata cannot be verified
  ; safely and would otherwise leave two independently launchable versions.
  ClearErrors
  ReadRegStr $R0 HKCU "${WANDAO_LEGACY_INSTALL_KEY}" "InstallLocation"
  IfErrors wandao_legacy_default_path_check
  Goto wandao_legacy_untrusted

  wandao_legacy_default_path_check:
  ${If} ${FileExists} "${WANDAO_LEGACY_MAIN_EXE}"
    Goto wandao_legacy_untrusted
  ${EndIf}
  Goto wandao_legacy_done

  wandao_legacy_untrusted:
  MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "检测到旧版 Wandao，但安装信息不完整或路径不可信。请修复旧版安装，或从 Windows“设置 → 应用 → 已安装的应用”卸载旧版后点击“重试”。这与开发环境无关；请保留用户数据目录 %APPDATA%\wandao。" /SD IDCANCEL IDRETRY wandao_legacy_validate
  SetErrorLevel 12
  Abort

  wandao_legacy_uninstall_failed:
  MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "旧版 Wandao 未能完整卸载。请确认旧版窗口和任务均已关闭，必要时从 Windows“设置 → 应用 → 已安装的应用”手动卸载旧版，然后点击“重试”。用户数据目录 %APPDATA%\wandao 会保留。" /SD IDCANCEL IDRETRY wandao_legacy_validate
  SetErrorLevel $R5
  Abort

  wandao_legacy_done:
  ; v1.4.x Tauri used the Wandao display name in its uninstall entry. Reuse
  ; that verified install root so a product-name change cannot create a second
  ; application directory during a manual install or an in-app update.
  Call WandaoReuseOldTauriInstall
  wandao_legacy_skip_all:
!macroend

; The updater invokes this installer in /UPDATE mode. Re-assert the final
; install identity after copying files so Windows shell entries and
; Add/Remove Programs cannot retain an older install root or version.
!macro NSIS_HOOK_POSTINSTALL
  WriteRegStr SHCTX "${MANUPRODUCTKEY}" "" $INSTDIR
  WriteRegStr SHCTX "${UNINSTKEY}" "MainBinaryName" "${MAINBINARYNAME}.exe"
  WriteRegStr SHCTX "${UNINSTKEY}" "DisplayName" "${PRODUCTNAME}"
  WriteRegStr SHCTX "${UNINSTKEY}" "DisplayIcon" "$\"$INSTDIR\${MAINBINARYNAME}.exe$\""
  WriteRegStr SHCTX "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr SHCTX "${UNINSTKEY}" "Publisher" "${MANUFACTURER}"
  WriteRegStr SHCTX "${UNINSTKEY}" "InstallLocation" "$\"$INSTDIR$\""
  WriteRegStr SHCTX "${UNINSTKEY}" "UninstallString" "$\"$INSTDIR\uninstall.exe$\""
  WriteRegDWORD SHCTX "${UNINSTKEY}" "NoModify" "1"
  WriteRegDWORD SHCTX "${UNINSTKEY}" "NoRepair" "1"
  DeleteRegKey HKCU "${WANDAO_OLD_TAURI_UNINSTALL_KEY}"
  Call WandaoRepairExistingShortcuts
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

Function WandaoRepairExistingShortcuts
  ; Keep user-deleted shortcuts deleted, but repair any remaining product
  ; shortcut that still targets an older executable.
  ${If} ${FileExists} "$SMPROGRAMS\万能导.lnk"
    !insertmacro SetShortcutTarget "$SMPROGRAMS\万能导.lnk" "$INSTDIR\wandao.exe"
    !insertmacro SetLnkAppUserModelId "$SMPROGRAMS\万能导.lnk"
  ${EndIf}
  ${If} ${FileExists} "$DESKTOP\万能导.lnk"
    !insertmacro SetShortcutTarget "$DESKTOP\万能导.lnk" "$INSTDIR\wandao.exe"
    !insertmacro SetLnkAppUserModelId "$DESKTOP\万能导.lnk"
  ${EndIf}
FunctionEnd

Function WandaoReuseOldTauriInstall
  wandao_old_tauri_validate:
  StrCpy $R6 $INSTDIR
  ClearErrors
  ReadRegStr $R0 HKCU "${WANDAO_OLD_TAURI_UNINSTALL_KEY}" "DisplayName"
  IfErrors wandao_old_tauri_not_found
  StrCmp $R0 "Wandao" 0 wandao_old_tauri_not_found

  ReadRegStr $R1 HKCU "${WANDAO_OLD_TAURI_UNINSTALL_KEY}" "Publisher"
  ; This function is compiled before Tauri defines ${MANUFACTURER}; keep the
  ; verified publisher literal here instead of expanding an empty macro.
  StrCmp $R1 "tllovesxs" 0 wandao_old_tauri_untrusted
  ReadRegStr $R2 HKCU "${WANDAO_OLD_TAURI_UNINSTALL_KEY}" "InstallLocation"
  StrCmp $R2 "" wandao_old_tauri_untrusted

  ; Our hook writes InstallLocation quoted for Windows registry consumers.
  ; Normalize that value before using it as an installer directory.
  ${StrLoc} $R3 $R2 '$\"' ">"
  StrCmp $R3 "0" 0 wandao_old_tauri_path_unquoted
  StrLen $R4 $R2
  IntCmp $R4 2 wandao_old_tauri_untrusted wandao_old_tauri_untrusted 0
  IntOp $R4 $R4 - 2
  StrCpy $R2 $R2 $R4 1
  wandao_old_tauri_path_unquoted:
  StrLen $R3 $R2
  IntCmp $R3 3 wandao_old_tauri_untrusted wandao_old_tauri_untrusted 0
  StrCpy $R4 $R2 1 1
  StrCmp $R4 ":" 0 wandao_old_tauri_untrusted
  StrCpy $R4 $R2 1 2
  StrCmp $R4 "\" 0 wandao_old_tauri_untrusted
  ${StrLoc} $R4 $R2 '$\"' ">"
  StrCmp $R4 "" 0 wandao_old_tauri_untrusted
  ${StrLoc} $R4 $R2 "%" ">"
  StrCmp $R4 "" 0 wandao_old_tauri_untrusted
  StrCpy $R4 "$R2\wandao.exe"
  StrCpy $R5 "$R2\uninstall.exe"
  ${IfNot} ${FileExists} "$R4"
    Goto wandao_old_tauri_untrusted
  ${EndIf}
  ${IfNot} ${FileExists} "$R5"
    Goto wandao_old_tauri_untrusted
  ${EndIf}

  StrCpy $INSTDIR $R2
  ; SetOutPath runs before this hook. Remove only the empty default directory
  ; created for the abandoned localized path; never recurse into user files.
  StrCmp $R6 $R2 wandao_old_tauri_keep_original_dir
  ${If} ${FileExists} "$R6"
    RMDir "$R6"
  ${EndIf}
  wandao_old_tauri_keep_original_dir:
  Call WandaoMigrateOldShortcuts
  Return

  wandao_old_tauri_untrusted:
  MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "检测到旧版 Wandao 的安装记录，但安装路径不完整或不可信。请从 Windows“设置 → 应用 → 已安装的应用”卸载旧版后点击“重试”。用户数据目录 %APPDATA%\wandao 会保留。" /SD IDCANCEL IDRETRY wandao_old_tauri_validate
  SetErrorLevel 13
  Abort

  wandao_old_tauri_not_found:
FunctionEnd

Function WandaoMigrateOldShortcuts
  ; Rename the old shell entries before the stock NSIS template creates the
  ; localized entries. This keeps the user's existing shortcut placement.
  ${If} ${FileExists} "$SMPROGRAMS\Wandao.lnk"
    ${IfNot} ${FileExists} "$SMPROGRAMS\万能导.lnk"
      Rename "$SMPROGRAMS\Wandao.lnk" "$SMPROGRAMS\万能导.lnk"
    ${Else}
      Delete "$SMPROGRAMS\Wandao.lnk"
    ${EndIf}
  ${EndIf}
  ${If} ${FileExists} "$DESKTOP\Wandao.lnk"
    ${IfNot} ${FileExists} "$DESKTOP\万能导.lnk"
      Rename "$DESKTOP\Wandao.lnk" "$DESKTOP\万能导.lnk"
    ${Else}
      Delete "$DESKTOP\Wandao.lnk"
    ${EndIf}
  ${EndIf}
FunctionEnd
