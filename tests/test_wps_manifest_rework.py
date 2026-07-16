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
        self.assertNotIn("guide", provider)
        self.assertIn("智能文档", provider["title"])
        self.assertIn("Markdown", provider["title"])
        self.assertIn("其他文档", provider["title"])
        self.assertIn("原始文件", provider["title"])
        self.assertIn("智能文档转换为 Markdown", provider["description"])
        self.assertIn("其他文档保留原始文件", provider["description"])
        self.assertIn("不读取设备文档", provider["description"])
        export_action = next(action for action in provider["actions"] if action["id"] == "export")
        self.assertIn("Markdown", export_action["progressDetail"])
        self.assertIn("原始文件", export_action["progressDetail"])


if __name__ == "__main__":
    unittest.main()
