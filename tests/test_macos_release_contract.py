import json
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class MacosReleaseContractTests(unittest.TestCase):
    def test_desktop_versions_are_synchronized(self) -> None:
        package = json.loads((REPO_ROOT / "wandao_electron/package.json").read_text(encoding="utf-8"))
        tauri = json.loads(
            (REPO_ROOT / "wandao_electron/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
        )
        cargo = (REPO_ROOT / "wandao_electron/src-tauri/Cargo.toml").read_text(encoding="utf-8")
        package_section = cargo.split("[package]", 1)[1].split("\n[", 1)[0]
        cargo_version = re.search(r'^version\s*=\s*"([^"]+)"', package_section, re.MULTILINE)

        self.assertIsNotNone(cargo_version)
        self.assertEqual(package["version"], tauri["version"])
        self.assertEqual(package["version"], cargo_version.group(1))

    def test_apple_silicon_release_alias_is_explicitly_unsigned(self) -> None:
        package = json.loads((REPO_ROOT / "wandao_electron/package.json").read_text(encoding="utf-8"))
        tauri = json.loads(
            (REPO_ROOT / "wandao_electron/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
        )

        self.assertEqual(tauri["bundle"]["macOS"]["minimumSystemVersion"], "11.0")
        self.assertIsNone(tauri["bundle"]["macOS"]["signingIdentity"])
        unsigned_build = package["scripts"]["build:mac:arm64:unsigned"]
        release_build = package["scripts"]["build:mac:arm64:release"]
        self.assertIn("--no-sign", unsigned_build)
        self.assertEqual(release_build, "npm run build:mac:arm64:unsigned")
        self.assertIn("--target aarch64-apple-darwin", unsigned_build)
        self.assertIn("--bundles app", unsigned_build)

    def test_tag_release_publishes_unsigned_artifacts_without_signing_credentials(self) -> None:
        package = json.loads((REPO_ROOT / "wandao_electron/package.json").read_text(encoding="utf-8"))
        workflow = (REPO_ROOT / ".github/workflows/build-desktop.yml").read_text(encoding="utf-8")
        release_job = workflow.split("\n  release:\n", 1)[1]

        self.assertEqual(package["scripts"]["build:win:release"], "npm run build:win:unsigned")
        self.assertEqual(
            package["scripts"]["build:mac:arm64:release"],
            "npm run build:mac:arm64:unsigned",
        )
        self.assertIn("Build ${{ matrix.name }} (unsigned)", workflow)
        self.assertIn("run: ${{ matrix.unsigned_command }}", workflow)
        self.assertNotIn("desktop-release", workflow)
        for secret in (
            "WINDOWS_CERTIFICATE",
            "WINDOWS_CERTIFICATE_PASSWORD",
            "WINDOWS_CERTIFICATE_THUMBPRINT",
            "WINDOWS_TIMESTAMP_URL",
            "APPLE_CERTIFICATE",
            "APPLE_CERTIFICATE_PASSWORD",
            "APPLE_SIGNING_IDENTITY",
            "APPLE_ID",
            "APPLE_PASSWORD",
            "APPLE_TEAM_ID",
            "KEYCHAIN_PASSWORD",
        ):
            self.assertNotIn(f"secrets.{secret}", workflow)
        self.assertNotIn("Get-AuthenticodeSignature", workflow)
        self.assertNotIn("codesign --verify --deep --strict", workflow)
        self.assertNotIn("xcrun stapler validate", workflow)
        self.assertIn("draft: true", release_job)
        self.assertIn("make_latest: false", release_job)

        for line in workflow.splitlines():
            if line.lstrip().startswith("if:"):
                self.assertNotIn("secrets.", line)

    def test_workflow_checks_real_arm64_bundle_and_publishes_metadata(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/build-desktop.yml").read_text(encoding="utf-8")

        self.assertIn("os: macos-15", workflow)
        self.assertNotIn("macos-14", workflow)
        self.assertIn("rust_target: aarch64-apple-darwin", workflow)
        self.assertIn("target/aarch64-apple-darwin/release/bundle/macos", workflow)
        self.assertIn("CFBundleExecutable", workflow)
        self.assertIn("LSMinimumSystemVersion", workflow)
        self.assertIn("grep -q 'arm64'", workflow)
        self.assertIn("assets plugins providers python python-runtime", workflow)
        self.assertIn('--resources "$app_path" --executable "$app_path"', workflow)
        self.assertIn("fail_on_unmatched_files: true", workflow)
        self.assertIn("release-artifacts/SHA256SUMS", workflow)
        self.assertIn("release-artifacts/wandao.spdx.json", workflow)

    def test_current_user_docs_match_macos_release_scope_and_safety(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        usage = (REPO_ROOT / "wandao_electron/USAGE.md").read_text(encoding="utf-8")
        desktop_readme = (REPO_ROOT / "wandao_electron/README.md").read_text(encoding="utf-8")

        for current_doc in (readme, usage):
            self.assertNotIn("xattr -cr", current_doc)
            self.assertIn("macOS Apple Silicon", current_doc)
            self.assertIn("arm64", current_doc)
            self.assertIn("macOS 11+", current_doc)
        for maintainer_doc in (readme, usage, desktop_readme):
            self.assertIn("unsigned", maintainer_doc)
            self.assertIn("未签名", maintainer_doc)

    def test_keychain_lookup_errors_cannot_overwrite_legacy_key(self) -> None:
        security = (REPO_ROOT / "wandao_electron/src-tauri/src/security.rs").read_text(encoding="utf-8")

        self.assertIn("use super::macos_keychain_item_is_missing;", security)
        self.assertIn("macos_keychain_item_is_missing(output.status.code())", security)
        self.assertIn("为避免覆盖历史任务密钥", security)
        add_command = security.split('"add-generic-password"', 1)[1].split("])", 1)[0]
        self.assertNotIn('"-U"', add_command)

    def test_macos_lifecycle_and_native_menu_remain_complete(self) -> None:
        lib_rs = (REPO_ROOT / "wandao_electron/src-tauri/src/lib.rs").read_text(encoding="utf-8")
        menu_rs = (REPO_ROOT / "wandao_electron/src-tauri/src/app_menu.rs").read_text(encoding="utf-8")

        close_handler = lib_rs.split(".on_window_event", 1)[1].split("builder.build", 1)[0]
        self.assertIn('cfg(target_os = "macos")', close_handler)
        self.assertIn("api.prevent_close()", close_handler)
        self.assertIn("window.hide()", close_handler)
        reopen_handler = lib_rs.split("RunEvent::Reopen", 1)[1].split("_ => {}", 1)[0]
        self.assertIn('get_webview_window("main")', reopen_handler)
        self.assertIn("window.show()", reopen_handler)
        self.assertIn("window.set_focus()", reopen_handler)

        for native_item in (
            "about_with_text",
            "services_with_text",
            "hide_with_text",
            "quit_with_text",
            "bring_all_to_front_with_text",
            "close_window_with_text",
        ):
            self.assertIn(native_item, menu_rs)


if __name__ == "__main__":
    unittest.main()
