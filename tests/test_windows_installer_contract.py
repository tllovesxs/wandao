import json
import re
import struct
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TAURI_CONFIG = REPO_ROOT / "wandao_electron" / "src-tauri" / "tauri.conf.json"
INSTALLER_HOOK = (
    REPO_ROOT
    / "wandao_electron"
    / "src-tauri"
    / "installer"
    / "upgrade-hooks.nsh"
)
BUILD_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-desktop.yml"
LEGACY_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "windows_legacy_uninstaller.rs"
RELEASE_NOTES = REPO_ROOT / "RELEASE_NOTES.md"


def macro_body(source: str, name: str) -> str:
    match = re.search(
        rf"^!macro {re.escape(name)}\s*$\n(?P<body>.*?)^!macroend\s*$",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing NSIS macro: {name}")
    return match.group("body")


class WindowsInstallerMigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hook = INSTALLER_HOOK.read_text(encoding="utf-8")
        cls.preinstall = macro_body(cls.hook, "NSIS_HOOK_PREINSTALL")
        cls.postuninstall = macro_body(cls.hook, "NSIS_HOOK_POSTUNINSTALL")
        cls.workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")

    def test_tauri_config_enables_the_versioned_installer_hook(self) -> None:
        config = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
        nsis = config["bundle"]["windows"]["nsis"]
        hooks_path = nsis["installerHooks"]

        self.assertEqual(hooks_path, "installer/upgrade-hooks.nsh")
        self.assertEqual(config["bundle"]["publisher"], "tllovesxs")
        self.assertTrue((TAURI_CONFIG.parent / hooks_path).is_file())

    def test_nsis_uses_wandao_branding_assets_with_supported_formats(self) -> None:
        config = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
        nsis = config["bundle"]["windows"]["nsis"]

        self.assertEqual(nsis["installerIcon"], "../assets/icon.ico")
        self.assertEqual(nsis["uninstallerIcon"], "../assets/icon.ico")
        self.assertEqual(nsis["headerImage"], "installer/assets/wandao-header.bmp")
        self.assertEqual(nsis["sidebarImage"], "installer/assets/wandao-sidebar.bmp")
        self.assertIn("!define MUI_HEADERIMAGE_RIGHT", self.hook)

        icon_path = (TAURI_CONFIG.parent / nsis["installerIcon"]).resolve()
        self.assertTrue(icon_path.is_file())
        icon = icon_path.read_bytes()
        self.assertEqual(icon[:4], b"\x00\x00\x01\x00")
        icon_count = struct.unpack_from("<H", icon, 4)[0]
        icon_sizes = {
            (
                icon[offset] or 256,
                icon[offset + 1] or 256,
                struct.unpack_from("<H", icon, offset + 6)[0],
            )
            for offset in range(6, 6 + icon_count * 16, 16)
        }
        self.assertTrue({(16, 16, 32), (32, 32, 32), (256, 256, 32)} <= icon_sizes)

        for config_key, expected_size in (
            ("headerImage", (150, 57)),
            ("sidebarImage", (164, 314)),
        ):
            with self.subTest(config_key=config_key):
                bitmap_path = (TAURI_CONFIG.parent / nsis[config_key]).resolve()
                self.assertTrue(bitmap_path.is_file())
                bitmap = bitmap_path.read_bytes()
                self.assertEqual(bitmap[:2], b"BM")
                self.assertEqual(struct.unpack_from("<I", bitmap, 14)[0], 40)
                self.assertEqual(struct.unpack_from("<ii", bitmap, 18), expected_size)
                self.assertEqual(struct.unpack_from("<H", bitmap, 26)[0], 1)
                self.assertEqual(struct.unpack_from("<H", bitmap, 28)[0], 24)

    def test_localized_hook_is_explicit_utf8(self) -> None:
        self.assertTrue(INSTALLER_HOOK.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_preinstall_targets_only_a_consistent_legacy_wandao_install(self) -> None:
        self.assertIn(
            "1b0cbfbe-638f-5e34-9149-e794da0edaeb",
            self.hook,
        )
        self.assertIn('$LOCALAPPDATA\\Programs\\wandao', self.hook)
        self.assertIn('"DisplayName"', self.preinstall)
        self.assertIn('"DisplayVersion"', self.preinstall)
        self.assertIn('"Publisher"', self.preinstall)
        self.assertIn('"DisplayIcon"', self.preinstall)
        self.assertIn('"UninstallString"', self.preinstall)
        self.assertIn('"QuietUninstallString"', self.preinstall)
        self.assertIn('"1.3.0"', self.preinstall)
        self.assertIn('"1.4.0"', self.preinstall)
        self.assertIn('StrCmp $R2 "0" wandao_legacy_display_name_found', self.preinstall)
        self.assertIn('StrCmp $R2 "4" 0 wandao_legacy_untrusted', self.preinstall)
        self.assertIn("IntOp $R4 $R4 + $R2", self.preinstall)
        self.assertIn('StrCmp $R2 "tllovesxs"', self.preinstall)
        self.assertIn("wandao_legacy_validate:", self.preinstall)
        self.assertLess(
            self.preinstall.index("wandao_legacy_validate:"),
            self.preinstall.index("ReadRegStr"),
        )
        self.assertIn('"InstallLocation"', self.preinstall)
        self.assertIn('${WANDAO_LEGACY_INSTALL_KEY}', self.preinstall)
        self.assertIn('GetFullPathName $R4 "$R6"', self.preinstall)
        self.assertIn('StrCpy $R7 "$R6\\Wandao.exe"', self.preinstall)
        self.assertIn('StrCpy $R8 "$R6\\Uninstall Wandao.exe"', self.preinstall)
        self.assertIn('StrCmp $R4 ":" 0 wandao_legacy_untrusted', self.preinstall)
        self.assertIn('${StrLoc} $R5 $R4 ":" ">"', self.preinstall)
        self.assertIn('${StrLoc} $R5 $R6 \'$\\"\' ">"', self.preinstall)
        self.assertIn('${StrLoc} $R5 $R6 "%" ">"', self.preinstall)

    def test_registry_command_is_validated_but_never_executed_as_text(self) -> None:
        exec_waits = [
            line.strip()
            for line in self.preinstall.splitlines()
            if line.strip().startswith("ExecWait")
        ]

        self.assertEqual(
            exec_waits,
            ["ExecWait '\"$R8\" /currentuser /S _?=$R6' $R0"],
        )
        self.assertNotIn("ExecWait $R2", self.preinstall)
        self.assertNotIn("ExecWait '$R2", self.preinstall)
        self.assertNotIn("ExecWait '\"$R2", self.preinstall)
        self.assertLess(
            self.preinstall.index('"QuietUninstallString"'),
            self.preinstall.index("ExecWait"),
        )
        self.assertIn("StrCpy $R3 '$\\\"$R8$\\\" /currentuser'", self.preinstall)
        self.assertIn("StrCpy $R3 '$\\\"$R8$\\\" /currentuser /S'", self.preinstall)
        self.assertIn("_?= keeps the legacy uninstaller in-process", self.preinstall)
        self.assertIn('Delete "$R8"', self.preinstall)

    def test_running_legacy_process_is_rejected_without_force_kill(self) -> None:
        find_process = 'nsis_tauri_utils::FindProcessCurrentUser "Wandao.exe"'
        self.assertIn(find_process, self.preinstall)
        self.assertNotIn("CheckIfAppIsRunning", self.preinstall)
        self.assertNotIn("KillProcess", self.preinstall)

        guard = self.preinstall.split(find_process, 1)[1].split("ClearErrors", 1)[0]
        self.assertIn("Pop $R0", guard)
        self.assertIn(
            'StrCmp $R0 "1" wandao_legacy_process_not_running',
            guard,
        )
        self.assertIn("SetErrorLevel 11", guard)
        self.assertIn("Abort", guard)
        self.assertLess(guard.index("SetErrorLevel 11"), guard.index("Abort"))
        self.assertIn("MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION", guard)
        self.assertIn("/SD IDCANCEL", guard)
        self.assertIn("IDRETRY wandao_legacy_validate", guard)
        self.assertIn("检测到 Wandao 正在运行", guard)
        self.assertIn("请关闭所有 Wandao 窗口", guard)
        self.assertIn("不会强制结束程序", guard)

    def test_preinstall_checks_uninstaller_exit_and_postconditions(self) -> None:
        self.assertIn("IfErrors wandao_legacy_uninstall_failed", self.preinstall)
        self.assertNotIn('StrCmp $R0 "0" 0 wandao_legacy_uninstall_failed', self.preinstall)
        self.assertIn("authoritative postcondition", self.preinstall)
        self.assertIn('${FileExists} "$R7"', self.preinstall)
        self.assertIn('${FileExists} "$R8"', self.preinstall)
        self.assertIn('${FileExists} "${WANDAO_LEGACY_MAIN_EXE}"', self.preinstall)
        self.assertIn("wandao_legacy_wait_for_cleanup", self.preinstall)
        self.assertIn("StrCpy $R9 40", self.preinstall)
        self.assertIn("Sleep 250", self.preinstall)
        self.assertIn('StrCmp $R9 "0" wandao_legacy_uninstall_failed', self.preinstall)
        self.assertIn("wandao_legacy_remove_keys", self.preinstall)
        self.assertIn('DeleteRegKey HKCU "${WANDAO_LEGACY_UNINSTALL_KEY}"', self.preinstall)
        self.assertIn('DeleteRegKey HKCU "${WANDAO_LEGACY_INSTALL_KEY}"', self.preinstall)
        self.assertIn("SetErrorLevel 12", self.preinstall)
        self.assertIn("StrCpy $R5 13", self.preinstall)
        self.assertIn("StrCpy $R5 23", self.preinstall)
        self.assertIn("SetErrorLevel $R5", self.preinstall)
        self.assertNotIn("RMDir", self.preinstall)
        self.assertNotIn("$APPDATA\\wandao", self.preinstall)

        for label in ("wandao_legacy_untrusted:", "wandao_legacy_uninstall_failed:"):
            failure = self.preinstall.split(label, 1)[1].split("\n\n", 1)[0]
            self.assertIn("MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION", failure)
            self.assertIn("/SD IDCANCEL", failure)
            self.assertIn("IDRETRY wandao_legacy_validate", failure)
            error_level = re.search(r"SetErrorLevel (?:12|\$R5)", failure)
            self.assertIsNotNone(error_level)
            self.assertIn("Abort", failure)
            self.assertLess(error_level.start(), failure.index("Abort"))
        self.assertEqual(
            self.preinstall.count("MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION"),
            3,
        )
        self.assertEqual(self.preinstall.count("/SD IDCANCEL"), 3)
        self.assertEqual(self.preinstall.count("IDRETRY wandao_legacy_validate"), 3)
        self.assertIn("安装信息不完整或路径不可信", self.preinstall)
        self.assertIn("手动卸载旧版", self.preinstall)
        self.assertIn("与开发环境无关", self.preinstall)
        self.assertIn("用户数据目录 %APPDATA%\\wandao", self.preinstall)

    def test_skip_switch_is_explicit_and_precedes_registry_access(self) -> None:
        switch = '${GetOptions} $CMDLINE "/SKIPLEGACYUNINSTALL"'
        self.assertIn(switch, self.preinstall)
        self.assertLess(
            self.preinstall.index(switch),
            self.preinstall.index("ReadRegStr"),
        )

    def test_real_user_data_is_deleted_only_on_explicit_full_uninstall(self) -> None:
        self.assertIn('${If} $DeleteAppDataCheckboxState = 1', self.postuninstall)
        self.assertIn('${AndIf} $UpdateMode <> 1', self.postuninstall)
        self.assertIn('RMDir /r "$APPDATA\\wandao"', self.postuninstall)
        self.assertLess(
            self.postuninstall.index('$DeleteAppDataCheckboxState = 1'),
            self.postuninstall.index('RMDir /r "$APPDATA\\wandao"'),
        )
        self.assertLess(
            self.postuninstall.index('$UpdateMode <> 1'),
            self.postuninstall.index('RMDir /r "$APPDATA\\wandao"'),
        )

    def test_ci_directory_switch_is_last_and_skip_is_release_smoke_only(self) -> None:
        install_lines = [
            line.strip()
            for line in self.workflow.splitlines()
            if "Start-Process -FilePath $installer.FullName" in line
        ]

        self.assertEqual(len(install_lines), 4)
        for line in install_lines:
            with self.subTest(line=line):
                arguments = line.split("-ArgumentList @(", 1)[1].split(")", 1)[0]
                self.assertRegex(arguments.split(", ")[-1], r'^"/D=\$\w+Root"$')

        skip_lines = [line for line in install_lines if "/SKIPLEGACYUNINSTALL" in line]
        self.assertEqual(len(skip_lines), 1)
        self.assertIn(
            '-ArgumentList @("/S", "/SKIPLEGACYUNINSTALL", "/D=$installRoot")',
            skip_lines[0],
        )
        release_smoke = self.workflow.split(
            "- name: Smoke test Windows installer",
            1,
        )[1].split("- name: Smoke test macOS application", 1)[0]
        self.assertIn(skip_lines[0], release_smoke)

    def test_pr_smoke_exercises_rejection_and_real_legacy_migration(self) -> None:
        package_smoke_job = self.workflow.split(
            "  package-smoke:\n",
            1,
        )[1].split("\n  build:", 1)[0]

        self.assertIn('RUNNER_ENVIRONMENT -ne "github-hosted"', package_smoke_job)
        self.assertIn("windows_legacy_uninstaller.rs", package_smoke_job)
        self.assertIn('$displayVersion = "1.3.10"', package_smoke_job)
        self.assertIn(
            '-Name DisplayName -Value "万能导 Wandao $displayVersion"',
            package_smoke_job,
        )
        self.assertIn("wandao-custom-legacy", package_smoke_job)
        self.assertIn('wandao-custom-legacy\\Wandao', package_smoke_job)
        self.assertIn("$legacyInstallKey", package_smoke_job)
        self.assertIn("-Name InstallLocation -Value $legacyRoot", package_smoke_job)
        self.assertIn('Tauri uninstall key already exists', package_smoke_job)
        self.assertIn("WANDAO_LEGACY_FIXTURE_MARKER", package_smoke_job)
        self.assertNotIn("Installer accepted a custom legacy install directory", package_smoke_job)
        self.assertNotIn("Rejected custom-directory migration", package_smoke_job)
        self.assertIn('$maliciousQuietUninstall = "`"$env:ComSpec`" /d /c mkdir', package_smoke_job)
        self.assertIn("if ($blocked.ExitCode -eq 0)", package_smoke_job)
        self.assertIn("Rejected migration modified the legacy installation", package_smoke_job)
        self.assertIn("Rejected migration executed untrusted registry command text", package_smoke_job)
        self.assertIn(
            'Start-Process -FilePath $legacyMain -ArgumentList @("--stay-running")',
            package_smoke_job,
        )
        self.assertIn("Installer accepted a running legacy application", package_smoke_job)
        self.assertIn("Installer terminated the running legacy application", package_smoke_job)
        self.assertIn("Rejected running-process migration invoked the legacy uninstaller", package_smoke_job)
        self.assertIn("Stop-Process -Id $legacyProcess.Id -Force", package_smoke_job)
        self.assertIn("Legacy uninstall key survived the migration", package_smoke_job)
        self.assertIn("Legacy product key survived the migration", package_smoke_job)
        self.assertIn("Legacy main executable survived the migration", package_smoke_job)
        self.assertIn("Legacy user data was not preserved", package_smoke_job)
        self.assertIn("Trusted migration did not invoke the controlled legacy uninstaller", package_smoke_job)
        self.assertIn("Trusted migration invoked the legacy fixture with unexpected", package_smoke_job)
        self.assertNotIn("/SKIPLEGACYUNINSTALL", package_smoke_job)

        fixture = LEGACY_FIXTURE.read_text(encoding="utf-8")
        self.assertIn('"WANDAO_LEGACY_FIXTURE_MARKER"', fixture)
        self.assertIn('arguments == ["--stay-running"]', fixture)
        self.assertIn('eq_ignore_ascii_case("Wandao.exe")', fixture)
        self.assertIn("thread::park()", fixture)
        self.assertIn('"/currentuser".to_owned()', fixture)
        self.assertIn('"/S".to_owned()', fixture)
        self.assertIn('format!("_?={}", legacy_root.display())', fixture)
        self.assertIn("arguments != expected_arguments", fixture)
        self.assertIn('eq_ignore_ascii_case("wandao")', fixture)
        self.assertIn("LEGACY_INSTALL_KEY", fixture)
        self.assertIn("same_windows_path", fixture)
        self.assertIn("fs::remove_file(&legacy_main)", fixture)
        self.assertIn("reg.exe", fixture)
        self.assertNotIn('env::var_os("APPDATA")', fixture)

    def test_release_notes_document_the_safe_custom_directory_boundary(self) -> None:
        notes = RELEASE_NOTES.read_text(encoding="utf-8")
        current_release = notes.split("## 1.4.0", 1)[1].split("\n## ", 1)[0]

        self.assertIn("1.3.x", current_release)
        self.assertIn("%APPDATA%\\wandao", current_release)
        self.assertIn("自定义旧安装目录", current_release)
        self.assertIn("自动迁移", current_release)


if __name__ == "__main__":
    unittest.main()
