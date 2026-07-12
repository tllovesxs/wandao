import tempfile
import unittest
from pathlib import Path
from unittest import mock

import export_yuque


class YuqueExportTests(unittest.TestCase):
    def test_doc_api_error_includes_title_http_code_and_redacts_sensitive_message(self) -> None:
        class FakeCdp:
            def evaluate(self, _expression, timeout):
                return {
                    "ok": False,
                    "status": 401,
                    "code": "AUTH_EXPIRED",
                    "message": "token=very-secret-cookie login expired",
                }

        with self.assertRaises(export_yuque.ExportError) as raised:
            export_yuque.fetch_doc_markdown(FakeCdp(), 1, {"url": "doc", "title": "Private document"})

        message = str(raised.exception)
        self.assertIn("Private document", message)
        self.assertIn("HTTP 401", message)
        self.assertIn("AUTH_EXPIRED", message)
        self.assertNotIn("very-secret-cookie", message)

    def test_selected_toc_docs_filters_by_doc_id_not_tree_uuid(self) -> None:
        toc = [
            {"uuid": "folder-uuid", "type": "TITLE", "title": "Folder"},
            {"uuid": "tree-uuid", "doc_id": "export-id", "type": "DOC", "title": "Document"},
        ]

        selected = export_yuque.selected_toc_docs(toc, {"export-id"})

        self.assertEqual([item["uuid"] for item in selected], ["tree-uuid"])

    def test_resource_401_refreshes_current_session_and_retries_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            md_path = root / "document.md"
            target = root / "assets" / "image.png"
            refresh_calls = []

            def refresh_cookies():
                refresh_calls.append(True)
                return [{"name": "refreshed", "value": "cookie", "domain": ".yuque.com"}]

            with mock.patch.object(
                export_yuque,
                "download_resource",
                side_effect=[export_yuque.ResourceUnauthorizedError("resource 401"), target],
            ) as download:
                markdown, success, failures = export_yuque.localize_resources(
                    "![image](https://www.yuque.com/image.png)",
                    [{"url": "https://www.yuque.com/image.png", "kind": "image", "title": "image"}],
                    md_path,
                    timeout=5,
                    keep_remote=True,
                    cookies=[],
                    download_attachments=True,
                    refresh_cookies=refresh_cookies,
                )

        self.assertEqual(refresh_calls, [True])
        self.assertEqual(download.call_count, 2)
        self.assertEqual(success["image"], 1)
        self.assertEqual(failures, [])
        self.assertIn("assets/image.png", markdown)

    def test_resource_401_reports_retry_failure_without_marking_account_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md_path = Path(tmp) / "document.md"
            with mock.patch.object(
                export_yuque,
                "download_resource",
                side_effect=export_yuque.ResourceUnauthorizedError("resource 401"),
            ):
                _markdown, _success, failures = export_yuque.localize_resources(
                    "![image](https://www.yuque.com/image.png)",
                    [{"url": "https://www.yuque.com/image.png", "kind": "image", "title": "image"}],
                    md_path,
                    timeout=5,
                    keep_remote=True,
                    cookies=[],
                    download_attachments=True,
                    refresh_cookies=lambda: [],
                )

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["error"], "资源 401，刷新会话后重试仍失败")


if __name__ == "__main__":
    unittest.main()
