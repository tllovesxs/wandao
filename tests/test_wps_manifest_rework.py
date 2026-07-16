from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WPSManifestTests(unittest.TestCase):
    def test_plugin_is_scoped_to_wps_and_does_not_request_local_read_access(self) -> None:
        manifest = json.loads((ROOT / "plugins/wps/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["id"], "wps")
        self.assertIn("browser-automation", manifest["permissions"])
        self.assertNotIn("filesystem:read", manifest["permissions"])
        self.assertEqual(manifest["entrypoints"]["providers"], ["providers/wps-export/provider.json"])

    def test_provider_exposes_only_wps_login_scan_export_and_clear_auth(self) -> None:
        provider = json.loads((ROOT / "plugins/wps/providers/wps-export/provider.json").read_text(encoding="utf-8"))
        self.assertEqual(provider["id"], "wps-export")
        self.assertEqual({action["id"] for action in provider["actions"]}, {"login", "scan", "export", "clearAuth"})
        self.assertEqual(provider["toc"]["selectionArg"], "--file-id")
        self.assertEqual(provider["toc"]["selectableTypes"], ["file"])
        self.assertFalse(provider["capabilities"]["guide"])
        self.assertTrue(provider["capabilities"]["tree"])
        self.assertTrue(provider["capabilities"]["images"])
        self.assertTrue(provider["capabilities"]["attachments"])
        self.assertTrue(provider["checkpoint"]["resourceTracking"])
        self.assertNotIn("guide", provider)
        self.assertIn("智能文档", provider["title"])
        self.assertIn("其他文档", provider["title"])
        self.assertIn("不读取设备文档", provider["description"])

    def test_readme_is_for_desktop_users_and_documents_current_limits(self) -> None:
        readme = (ROOT / "plugins/wps/providers/wps-export/README.md").read_text(encoding="utf-8")
        self.assertNotIn("```powershell", readme)
        self.assertNotIn("python plugins/wps/backend/export_wps.py", readme)
        self.assertIn("登录 WPS 并保存认证", readme)
        self.assertIn("扫描 WPS 文档", readme)
        self.assertIn("在线智能表格", readme)
        self.assertIn("尚未上传完成", readme)
        self.assertIn("保留 WPS 返回的目录层级", readme)
        self.assertIn("图片和可识别附件", readme)
        self.assertIn("相对链接", readme)
        self.assertNotIn("只保留文字占位提示", readme)
        self.assertIn("Markdown", readme)
        self.assertIn("xmgzxmgz/wps-cloud-export", readme)
        self.assertIn("Clanel/wps-ai-note-export-markdown", readme)
        self.assertIn("独立实现", readme)


if __name__ == "__main__":
    unittest.main()
