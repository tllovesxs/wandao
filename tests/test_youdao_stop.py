import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class YoudaoStopContractTests(unittest.TestCase):
    def test_youdao_export_records_stopped_item_and_returns_code_130(self) -> None:
        source = (REPO_ROOT / "plugins/youdao/backend/export_youdao.py").read_text(encoding="utf-8")

        self.assertIn("ExportStopped", source)
        self.assertIn("check_stopped(args)", source)
        self.assertIn('checkpoint.fail_item(item_key, "stopped")', source)
        self.assertIn('return 130 if result.get("stopped") else 0', source)

    def test_shared_runtime_and_generic_export_ui_handle_controlled_stop(self) -> None:
        browser = (REPO_ROOT / "wandao_core/browser.py").read_text(encoding="utf-8")
        main_js = (REPO_ROOT / "wandao_electron/main.js").read_text(encoding="utf-8")
        app_js = (REPO_ROOT / "wandao_electron/renderer/app.js").read_text(encoding="utf-8")

        self.assertIn("WANDAO_STOP_FILE", browser)
        self.assertIn("fs.writeFileSync(pythonStopFile, 'stop', 'utf8')", main_js)
        self.assertIn("else if (result.code === 130)", app_js)


if __name__ == "__main__":
    unittest.main()
