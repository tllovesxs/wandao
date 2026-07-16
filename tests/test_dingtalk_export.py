from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "plugins" / "dingtalk" / "backend" / "export_dingtalk.py"
SPEC = importlib.util.spec_from_file_location("wandao_dingtalk_export", MODULE_PATH)
assert SPEC and SPEC.loader
dingtalk = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dingtalk
SPEC.loader.exec_module(dingtalk)


class DingTalkExportTests(unittest.TestCase):
    def test_parse_source_url_accepts_current_and_legacy_hosts(self) -> None:
        self.assertEqual(
            dingtalk.parse_source_url("https://docs.dingtalk.com/i/nodes/abc_123?from=copy"),
            "abc_123",
        )
        self.assertEqual(
            dingtalk.parse_source_url("https://alidocs.dingtalk.com/i/nodes/legacy-node"),
            "legacy-node",
        )

    def test_parse_source_url_rejects_unrelated_urls(self) -> None:
        with self.assertRaises(dingtalk.ExportError):
            dingtalk.parse_source_url("https://example.com/i/nodes/not-dingtalk")

    def test_renderer_preserves_common_markdown_and_reports_unknown_nodes(self) -> None:
        document = {
            "parts": {
                "main": {
                    "type": "application/x-alidocs-word",
                    "data": {
                        "body": [
                            "root",
                            {},
                            ["h1", {}, ["span", {"bold": True}, "标题"]],
                            ["p", {}, ["span", {}, "正文"], ["br", {}], ["inlineCode", {}, "x = 1"]],
                            ["p", {"list": {"listId": "one", "level": 0, "isOrdered": False}}, "项目"],
                            ["mystery", {}, "未知内容"],
                        ]
                    },
                }
            }
        }
        renderer = dingtalk.DingMarkdownRenderer("https://docs.dingtalk.com/i/nodes/test")
        markdown = renderer.render_document(document, "测试文档")
        self.assertIn("# 测试文档", markdown)
        self.assertIn("# **标题**", markdown)
        self.assertIn("正文<br>`x = 1`", markdown)
        self.assertIn("- 项目", markdown)
        self.assertIn("未知内容", markdown)
        self.assertEqual(renderer.warnings, ["发现暂不支持的钉钉结构：mystery"])

    def test_safe_path_segment_never_uses_path_traversal(self) -> None:
        self.assertEqual(dingtalk.safe_path_segment("../../报告?.md"), "--报告-.md")

    def test_asset_urls_are_restricted_and_redacted_for_reports(self) -> None:
        self.assertTrue(dingtalk.is_trusted_asset_url("https://img.alicdn.com/example.png?signature=secret"))
        self.assertFalse(dingtalk.is_trusted_asset_url("http://img.alicdn.com/example.png"))
        self.assertFalse(dingtalk.is_trusted_asset_url("https://example.com/image.png"))
        self.assertEqual(
            dingtalk.safe_resource_url("https://img.alicdn.com/example.png?signature=secret#part"),
            "https://img.alicdn.com/example.png",
        )


if __name__ == "__main__":
    unittest.main()
