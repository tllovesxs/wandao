import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


class TauriLauncherContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.powershell = read_text("start-wandao.ps1")
        self.shell = read_text("start-wandao.sh")

    def test_launchers_use_tauri_dev_contract(self) -> None:
        manifest = json.loads(read_text("wandao_electron/package.json"))
        self.assertEqual(manifest["scripts"]["dev"], "tauri dev")
        for launcher in (self.powershell, self.shell):
            self.assertIn("npm run dev", launcher)
            self.assertIn("@tauri-apps", launcher)
            self.assertIn("package.json", launcher)
            self.assertIn("package-lock.json", launcher)
            self.assertIn("node_modules/.package-lock.json", launcher.replace("\\", "/"))
            self.assertNotIn("node_modules/electron", launcher.replace("\\", "/"))
            self.assertNotIn("electron-builder", launcher)
            self.assertNotIn("npm start", launcher)
            self.assertIn("npm ci", launcher)
            self.assertIn("replace-registry-host=always", launcher)

    def test_registry_probe_targets_tauri_cli(self) -> None:
        for launcher in (self.powershell, self.shell):
            self.assertIn("registry.npmjs.org/@tauri-apps%2fcli", launcher)
            self.assertIn("registry.npmmirror.com/@tauri-apps%2fcli", launcher)
            self.assertNotIn("registry.npmjs.org/electron", launcher)
            self.assertNotIn("registry.npmmirror.com/electron", launcher)

    def test_pinned_node_downloads_remain_verified(self) -> None:
        self.assertIn('$NodeVersion = "v22.12.0"', self.powershell)
        self.assertIn('NODE_VERSION="v22.12.0"', self.shell)
        self.assertIn("Get-FileHash", self.powershell)
        self.assertIn("verify_sha256", self.shell)
        self.assertIn("2b8f2256382f97ad51e29ff71f702961af466c4616393f767455501e6aece9b8", self.powershell)
        self.assertIn("22982235e1b71fa8850f82edd09cdae7e3f32df1764a9ec298c72d25ef2c164f", self.shell)
        self.assertIn("node-%s-linux-x64.tar.xz", self.shell)

    def test_rust_and_native_prerequisites_are_checked(self) -> None:
        for launcher in (self.powershell, self.shell):
            self.assertIn("1.88.0", launcher)
            self.assertIn("RUSTUP_TOOLCHAIN", launcher)
            self.assertIn("rustup toolchain install", launcher)
        self.assertIn("Microsoft.VisualStudio.Component.VC.Tools", self.powershell)
        self.assertIn("Windows Kits", self.powershell)
        self.assertIn("WebView2 Runtime", self.powershell)
        self.assertIn("xcode-select --install", self.shell)
        self.assertIn("webkit2gtk-4.1", self.shell)
        self.assertIn("libxdo", self.shell)

    def test_cmd_wrapper_propagates_failure(self) -> None:
        wrapper = read_text("start-wandao.cmd")
        self.assertIn('set "WANDAO_EXIT_CODE=%ERRORLEVEL%"', wrapper)
        self.assertIn("exit /b %WANDAO_EXIT_CODE%", wrapper)


if __name__ == "__main__":
    unittest.main()
