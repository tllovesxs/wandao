from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


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

    def test_parse_space_url_accepts_a_knowledge_base_overview(self) -> None:
        self.assertEqual(
            dingtalk.parse_space_url("https://alidocs.dingtalk.com/i/spaces/dBgX4Ako2lM3Om8e/overview"),
            "dBgX4Ako2lM3Om8e",
        )
        self.assertIsNone(dingtalk.parse_space_url("https://alidocs.dingtalk.com/i/nodes/legacy-node"))

    def test_resolve_space_root_uuid_uses_the_overview_directory_request(self) -> None:
        class FakeCdp:
            def __init__(self) -> None:
                self.navigated_to = ""
                self.evaluations = 0

            def evaluate(self, _expression, timeout=0):
                self.evaluations += 1
                return None if self.evaluations == 1 else "space-root-123"

            def navigate(self, url):
                self.navigated_to = url

        cdp = FakeCdp()
        root = dingtalk.resolve_space_root_uuid(cdp, "https://alidocs.dingtalk.com/i/spaces/demo-space/overview")
        self.assertEqual(root, "space-root-123")
        self.assertEqual(cdp.navigated_to, "https://alidocs.dingtalk.com/i/spaces/demo-space/overview")

    def test_open_dingtalk_target_navigates_an_existing_browser_tab(self) -> None:
        class FakeCdp:
            def __init__(self) -> None:
                self.navigated_to = ""

            def navigate(self, url):
                self.navigated_to = url

            @staticmethod
            def evaluate(_expression, timeout=0):
                return {"host": "alidocs.dingtalk.com", "readyState": "complete"}

        cdp = FakeCdp()
        target = "https://alidocs.dingtalk.com/i/nodes/target-node"
        dingtalk.open_dingtalk_target(cdp, target)
        self.assertEqual(cdp.navigated_to, target)

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
                            ["h2", {"list": {"listId": "one", "level": 0, "isOrdered": False}}, "标题型列表"],
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
        self.assertIn("- ## 标题型列表", markdown)
        self.assertIn("未知内容", markdown)
        self.assertEqual(renderer.warnings, ["发现暂不支持的钉钉结构：mystery"])

    def test_collect_tree_can_explicitly_use_a_document_parent_folder(self) -> None:
        responses = {
            "selected": {"dentryUuid": "selected", "name": "当前文档", "contentType": "alidoc", "dentryType": "file", "parentDentryUuid": "folder"},
            "folder": {"dentryUuid": "folder", "name": "知识库", "contentType": "folder", "dentryType": "folder", "parentDentryUuid": "outer", "hasChildren": True},
            "children:folder": {"children": [{"dentryUuid": "child", "name": "子文档", "contentType": "alidoc", "dentryType": "file", "parentDentryUuid": "folder"}]},
        }

        original = dingtalk.call_helper

        def fake_call_helper(_cdp, method, *args, **_kwargs):
            if method == "info":
                return responses[str(args[0])]
            if method == "children":
                return responses[f"children:{args[0]}"]
            raise AssertionError(method)

        dingtalk.call_helper = fake_call_helper
        try:
            args = dingtalk.parse_args(["--source-url", "https://alidocs.dingtalk.com/i/nodes/selected", "--document-scope", "parent-folder"])
            entries = dingtalk.collect_tree(None, args.source_url, args)
        finally:
            dingtalk.call_helper = original

        self.assertEqual([(entry.uuid, entry.parent_uuid) for entry in entries], [("folder", ""), ("child", "folder")])

    def test_collect_tree_keeps_selected_document_by_default(self) -> None:
        original = dingtalk.call_helper

        def fake_call_helper(_cdp, method, *args, **_kwargs):
            if method == "info":
                return {"dentryUuid": "selected", "name": "当前文档", "contentType": "alidoc", "dentryType": "file", "parentDentryUuid": "folder"}
            raise AssertionError(method)

        dingtalk.call_helper = fake_call_helper
        try:
            args = dingtalk.parse_args(["--source-url", "https://alidocs.dingtalk.com/i/nodes/selected"])
            entries = dingtalk.collect_tree(None, args.source_url, args)
        finally:
            dingtalk.call_helper = original

        self.assertEqual([(entry.uuid, entry.parent_uuid) for entry in entries], [("selected", "")])

    def test_collect_tree_can_explicitly_use_the_topmost_folder(self) -> None:
        responses = {
            "selected": {"dentryUuid": "selected", "name": "当前文档", "contentType": "alidoc", "dentryType": "file", "parentDentryUuid": "section"},
            "section": {"dentryUuid": "section", "name": "分组", "contentType": "folder", "dentryType": "folder", "parentDentryUuid": "library", "hasChildren": True},
            "library": {"dentryUuid": "library", "name": "知识库", "contentType": "folder", "dentryType": "folder", "hasChildren": True},
            "children:library": [{"dentryUuid": "section", "name": "分组", "contentType": "folder", "dentryType": "folder", "parentDentryUuid": "library", "hasChildren": True}],
            "children:section": [{"dentryUuid": "selected", "name": "当前文档", "contentType": "alidoc", "dentryType": "file", "parentDentryUuid": "section"}],
        }

        original = dingtalk.call_helper

        def fake_call_helper(_cdp, method, *args, **_kwargs):
            if method == "info":
                return responses[str(args[0])]
            if method == "children":
                return {"children": responses[f"children:{args[0]}"]}
            raise AssertionError(method)

        dingtalk.call_helper = fake_call_helper
        try:
            args = dingtalk.parse_args(["--source-url", "https://alidocs.dingtalk.com/i/nodes/selected", "--document-scope", "library-root"])
            entries = dingtalk.collect_tree(None, args.source_url, args)
        finally:
            dingtalk.call_helper = original

        self.assertEqual([(entry.uuid, entry.parent_uuid) for entry in entries], [("library", ""), ("section", "library"), ("selected", "section")])

    def test_children_stop_when_the_server_repeats_a_pagination_cursor(self) -> None:
        calls = 0
        original = dingtalk.call_helper

        def fake_call_helper(_cdp, method, *_args, **_kwargs):
            nonlocal calls
            self.assertEqual(method, "children")
            calls += 1
            return {"children": [], "loadMoreId": "same-page"}

        dingtalk.call_helper = fake_call_helper
        try:
            args = dingtalk.parse_args([])
            entry = dingtalk.DingEntry("folder", "", "", "目录", "folder", "", True, True)
            self.assertEqual(dingtalk.children_of(None, entry, args), [])
        finally:
            dingtalk.call_helper = original

        self.assertEqual(calls, 2)

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

    def test_read_limited_response_rejects_oversized_content(self) -> None:
        class FakeResponse:
            headers = {"Content-Length": "6"}

            @staticmethod
            def read(_size):
                return b"123456"

        with self.assertRaises(dingtalk.ExportError):
            dingtalk.read_limited_response(FakeResponse(), max_bytes=5)

    def test_document_requests_have_a_bounded_browser_timeout(self) -> None:
        self.assertIn("version === 4", dingtalk.DINGTALK_HELPER_JS)
        self.assertIn("version: 4", dingtalk.DINGTALK_HELPER_JS)
        self.assertIn("fetchWithTimeout", dingtalk.DINGTALK_HELPER_JS)
        self.assertIn("readArrayBufferWithTimeout", dingtalk.DINGTALK_HELPER_JS)
        self.assertIn("AbortController", dingtalk.DINGTALK_HELPER_JS)
        self.assertIn("}, 45000)", dingtalk.DINGTALK_HELPER_JS)
        self.assertIn("}, 15000)", dingtalk.DINGTALK_HELPER_JS)
        self.assertEqual(dingtalk.ASSET_DOWNLOAD_WORKERS, 6)
        self.assertEqual(dingtalk.ASSET_DIRECT_RETRIES, 3)
        original = dingtalk.call_helper
        calls = []

        def fake_call_helper(_cdp, method, *args, **kwargs):
            calls.append((method, args, kwargs))
            return {"documentContent": {"checkpoint": {"content": "{}"}}}

        dingtalk.call_helper = fake_call_helper
        try:
            args = dingtalk.parse_args([])
            entry = dingtalk.DingEntry("doc", "dentry-key", "doc-key", "文档", "alidoc", "", False, False)
            dingtalk.document_payload(None, entry, args)
        finally:
            dingtalk.call_helper = original

        self.assertEqual(calls[0][0], "content")
        self.assertEqual(calls[0][2]["timeout"], dingtalk.DOCUMENT_REQUEST_TIMEOUT_SECONDS + 10)

    def test_browser_image_fallback_uses_a_native_image_and_cdp_response_body(self) -> None:
        class FakeCdp:
            def __init__(self):
                self.calls = []

            def send(self, method, params=None, timeout=0):
                self.calls.append((method, params, timeout))
                if method == "Network.getResponseBody":
                    return {"result": {"body": "aW1hZ2U=", "base64Encoded": True}}
                return {"result": {}}

            def evaluate(self, _expression, timeout=0):
                return True

            def wait_for_event(self, _method, timeout=0, predicate=None):
                event = {"params": {"requestId": "request-1", "response": {"url": "https://img.alicdn.com/a.png", "headers": {"content-type": "image/png"}}}}
                if not predicate(event):
                    raise AssertionError("response predicate did not match")
                return event

        cdp = FakeCdp()
        raw, content_type = dingtalk.download_image_in_browser(cdp, "https://img.alicdn.com/a.png")
        self.assertEqual(raw, b"image")
        self.assertEqual(content_type, "image/png")
        self.assertEqual([call[0] for call in cdp.calls], ["Network.enable", "Network.setCacheDisabled", "Network.getResponseBody", "Network.setCacheDisabled"])

    def test_document_asset_prefix_prevents_same_folder_image_overwrite(self) -> None:
        self.assertEqual(
            dingtalk.safe_path_segment("../doc:uuid", "document", 32),
            "-doc-uuid",
        )

    def test_login_prints_a_task_result_without_an_inline_prompt(self) -> None:
        class FakeCdp:
            def navigate(self, _url):
                return None

            def close(self):
                return None

        original_connect = dingtalk.connect_dingtalk_browser
        original_save = dingtalk.save_auth_summary
        dingtalk.connect_dingtalk_browser = lambda _args: (FakeCdp(), None)
        dingtalk.save_auth_summary = lambda _args, _cdp: {"authFile": "auth.json", "displayName": "tester"}
        output = io.StringIO()
        try:
            with patch("builtins.input", return_value=""), contextlib.redirect_stdout(output):
                self.assertEqual(dingtalk.main(["--login"]), 0)
        finally:
            dingtalk.connect_dingtalk_browser = original_connect
            dingtalk.save_auth_summary = original_save

        stdout = output.getvalue()
        self.assertNotIn("完成钉钉登录并能看到网页后，回到万能导点击“我已完成登录，保存凭证”...{", stdout)
        payload = json.loads(stdout[stdout.index("{") :])
        self.assertEqual(payload["kind"], "wandao.result")
        self.assertTrue(payload["loggedIn"])


if __name__ == "__main__":
    unittest.main()
