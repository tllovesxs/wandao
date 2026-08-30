import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.wiz.backend import export_wiz


class WizExportRegressionTests(unittest.TestCase):
    def test_failed_checkpoint_item_is_not_skipped_by_incremental_export(self) -> None:
        self.assertFalse(
            export_wiz.should_skip_existing_doc(
                checkpoint_status="failed",
                incremental=True,
                path_exists=True,
                retry_failed=True,
            )
        )

    def test_completed_checkpoint_item_is_skipped_by_incremental_export(self) -> None:
        self.assertTrue(
            export_wiz.should_skip_existing_doc(
                checkpoint_status="completed",
                incremental=True,
                path_exists=True,
                retry_failed=True,
            )
        )

    def test_helper_version_is_bumped_for_diagnostic_protocol(self) -> None:
        self.assertIn("version === 8", export_wiz.WIZ_HELPER_JS)
        self.assertIn("version: 8", export_wiz.WIZ_HELPER_JS)

    def test_extract_dom_editor_html_requires_matching_title(self) -> None:
        editor_html = """
        <div class="editor-container root-container editor-with-title">
          <h1 class="title-block">测试协作笔记</h1>
          <div data-node-type="block"><div class="editor-text-node">测试正文第一段。</div></div>
          <div data-node-type="block"><div class="editor-text-node">测试正文第二段。</div></div>
        </div>
        """

        result = export_wiz.extract_dom_editor_html(editor_html, "测试协作笔记")

        self.assertEqual(result["title"], "测试协作笔记")
        self.assertIn("测试正文第二段", result["html"])
        self.assertIsNone(export_wiz.extract_dom_editor_html(editor_html, "另一篇笔记"))

    def test_dom_fallback_handles_virtualized_note_list(self) -> None:
        self.assertIn("virtual-list-container", export_wiz.WIZ_HELPER_JS)
        self.assertIn("domDocumentIndex", export_wiz.WIZ_HELPER_JS)

    def test_upgrade_page_diagnostic_contains_metadata_without_body(self) -> None:
        html = "当前客户端版本较低，无法编辑协作笔记"
        metadata = export_wiz.note_download_diagnostic({"html": html, "__wandaoMeta": {
            "httpStatus": 200,
            "bodyLength": len(html),
            "json": False,
        }})

        self.assertEqual(metadata["httpStatus"], 200)
        self.assertEqual(metadata["bodyLength"], len(html))
        self.assertTrue(metadata["upgradePage"])
        self.assertNotIn(html, str(metadata))

    def test_short_note_download_response_records_safe_shape(self) -> None:
        metadata = export_wiz.note_download_diagnostic({
            "returnCode": 200,
            "html": "<p>短响应</p>",
            "unexpected": "body value should not be logged",
            "__wandaoMeta": {"httpStatus": 200, "contentType": "application/json"},
        })

        self.assertEqual(metadata["bodyLength"], len("<p>短响应</p>"))
        self.assertIn("html", metadata["topLevelKeys"])
        self.assertIn("unexpected", metadata["topLevelKeys"])
        self.assertNotIn("短响应", str(metadata))
        self.assertNotIn("body value", str(metadata))

    def test_image_failure_diagnostic_uses_redacted_url(self) -> None:
        url = "https://example.invalid/image.jpg?tracking=removed"
        redacted = export_wiz.redact_wiz_url(url)

        self.assertEqual(redacted, "https://example.invalid/image.jpg")
        self.assertNotIn("tracking", redacted)

    def test_browser_image_fallback_reads_cdp_response_body(self) -> None:
        class FakeCdp:
            def __init__(self) -> None:
                self.calls = []

            def send(self, method, params=None, timeout=0):
                self.calls.append((method, params, timeout))
                if method == "Network.getResponseBody":
                    return {"result": {"body": "aW1hZ2U=", "base64Encoded": True}}
                return {"result": {}}

            def evaluate(self, _expression, timeout=0):
                return True

            def wait_for_event(self, method, timeout=0, predicate=None):
                if method == "Network.requestWillBeSent":
                    event = {"params": {"requestId": "request-1", "request": {"url": "https://img0.baidu.com/image.png"}}}
                elif method == "Network.loadingFinished":
                    event = {"params": {"requestId": "request-1"}}
                elif method == "Network.loadingFailed":
                    raise export_wiz.ExportError("Timed out waiting for CDP event: Network.loadingFailed")
                else:
                    event = {
                        "params": {
                            "requestId": "request-1",
                            "type": "Image",
                            "response": {
                                "url": "https://img0.baidu.com/image.png",
                                "status": 200,
                                "headers": {"content-type": "image/png"},
                            },
                        }
                    }
                if predicate and not predicate(event):
                    raise AssertionError(f"unexpected {method} predicate")
                return event

        doc = export_wiz.WizDoc("kb", "doc", "笔记", "/", "note", "", 0, 0, {})
        with tempfile.TemporaryDirectory() as temp_dir:
            saver = export_wiz.ResourceSaver(
                FakeCdp(),
                doc,
                Path(temp_dir) / "笔记.md",
                "https://as.wiz.cn",
                argparse.Namespace(request_delay=0, request_jitter=0),
            )
            result = saver.fetch_image_via_browser("https://img0.baidu.com/image.png")

        self.assertEqual(result["base64"], "aW1hZ2U=")
        self.assertEqual(result["contentType"], "image/png")

    def test_browser_image_fallback_accepts_a_successful_non_image_cdp_type(self) -> None:
        """Chrome may classify a browser-loaded image response as Other."""
        class FakeCdp:
            def send(self, method, params=None, timeout=0):
                if method == "Network.getResponseBody":
                    return {"result": {"body": "aW1hZ2U=", "base64Encoded": True}}
                return {"result": {}}

            def evaluate(self, _expression, timeout=0):
                return True

            def wait_for_event(self, method, timeout=0, predicate=None):
                if method == "Network.requestWillBeSent":
                    event = {"params": {"requestId": "request-1", "request": {"url": "https://img0.baidu.com/image.png"}}}
                elif method == "Network.loadingFinished":
                    event = {"params": {"requestId": "request-1"}}
                elif method == "Network.loadingFailed":
                    raise export_wiz.ExportError("Timed out waiting for CDP event: Network.loadingFailed")
                else:
                    event = {
                        "params": {
                            "requestId": "request-1",
                            "type": "Other",
                            "response": {
                                "url": "https://img0.baidu.com/image.png",
                                "status": 200,
                                "headers": {"content-type": "image/png"},
                            },
                        }
                    }
                if predicate and not predicate(event):
                    raise AssertionError(f"unexpected {method} predicate")
                return event

        doc = export_wiz.WizDoc("kb", "doc", "笔记", "/", "note", "", 0, 0, {})
        with tempfile.TemporaryDirectory() as temp_dir:
            saver = export_wiz.ResourceSaver(
                FakeCdp(),
                doc,
                Path(temp_dir) / "笔记.md",
                "https://as.wiz.cn",
                argparse.Namespace(request_delay=0, request_jitter=0),
            )
            result = saver.fetch_image_via_browser("https://img0.baidu.com/image.png")

        self.assertEqual(result["base64"], "aW1hZ2U=")

    def test_browser_image_fallback_waits_for_loading_finished_before_reading_body(self) -> None:
        class FakeCdp:
            def __init__(self) -> None:
                self.finished = False

            def send(self, method, params=None, timeout=0):
                if method == "Network.getResponseBody":
                    if not self.finished:
                        raise AssertionError("response body read before loadingFinished")
                    return {"result": {"body": "aW1hZ2U=", "base64Encoded": True}}
                return {"result": {}}

            def evaluate(self, _expression, timeout=0):
                return True

            def wait_for_event(self, method, timeout=0, predicate=None):
                events = {
                    "Network.requestWillBeSent": {
                        "params": {"requestId": "request-1", "request": {"url": "https://img0.baidu.com/image.png"}},
                    },
                    "Network.responseReceived": {
                        "params": {
                            "requestId": "request-1",
                            "type": "Image",
                            "response": {
                                "url": "https://img0.baidu.com/image.png",
                                "status": 200,
                                "headers": {"content-type": "image/png"},
                            },
                        },
                    },
                    "Network.loadingFinished": {"params": {"requestId": "request-1"}},
                }
                if method == "Network.loadingFailed":
                    raise export_wiz.ExportError("Timed out waiting for CDP event: Network.loadingFailed")
                event = events[method]
                if predicate and not predicate(event):
                    raise AssertionError(f"unexpected {method} predicate")
                if method == "Network.loadingFinished":
                    self.finished = True
                return event

        doc = export_wiz.WizDoc("kb", "doc", "笔记", "/", "note", "", 0, 0, {})
        with tempfile.TemporaryDirectory() as temp_dir:
            saver = export_wiz.ResourceSaver(FakeCdp(), doc, Path(temp_dir) / "笔记.md", "https://as.wiz.cn", argparse.Namespace(request_delay=0, request_jitter=0))
            result = saver.fetch_image_via_browser("https://img0.baidu.com/image.png")

        self.assertEqual(result["contentType"], "image/png")

    def test_browser_image_fallback_reports_loading_failed_without_waiting_for_response(self) -> None:
        class FakeCdp:
            def send(self, _method, params=None, timeout=0):
                return {"result": {}}

            def evaluate(self, _expression, timeout=0):
                return True

            def wait_for_event(self, method, timeout=0, predicate=None):
                if method == "Network.requestWillBeSent":
                    event = {"params": {"requestId": "request-1", "request": {"url": "https://img0.baidu.com/image.png"}}}
                elif method == "Network.loadingFailed":
                    event = {"params": {"requestId": "request-1", "errorText": "net::ERR_CONNECTION_RESET", "canceled": False}}
                else:
                    raise export_wiz.ExportError(f"Timed out waiting for CDP event: {method}")
                if predicate and not predicate(event):
                    raise AssertionError(f"unexpected {method} predicate")
                return event

        doc = export_wiz.WizDoc("kb", "doc", "笔记", "/", "note", "", 0, 0, {})
        with tempfile.TemporaryDirectory() as temp_dir:
            saver = export_wiz.ResourceSaver(FakeCdp(), doc, Path(temp_dir) / "笔记.md", "https://as.wiz.cn", argparse.Namespace(request_delay=0, request_jitter=0))
            with self.assertRaisesRegex(export_wiz.ExportError, "网络加载失败"):
                saver.fetch_image_via_browser("https://img0.baidu.com/image.png")

    def test_browser_image_fallback_rejects_non_image_response_before_reading_body(self) -> None:
        class FakeCdp:
            def send(self, method, params=None, timeout=0):
                if method == "Network.getResponseBody":
                    raise AssertionError("non-image response body must not be read")
                return {"result": {}}

            def evaluate(self, _expression, timeout=0):
                return True

            def wait_for_event(self, method, timeout=0, predicate=None):
                if method == "Network.requestWillBeSent":
                    event = {"params": {"requestId": "request-1", "request": {"url": "https://as.wiz.cn/resources/progress"}}}
                elif method == "Network.responseReceived":
                    event = {
                        "params": {
                            "requestId": "request-1",
                            "type": "Other",
                            "response": {
                                "url": "https://as.wiz.cn/resources/progress",
                                "status": 200,
                                "headers": {"content-type": "application/json"},
                            },
                        },
                    }
                else:
                    raise export_wiz.ExportError(f"Timed out waiting for CDP event: {method}")
                if predicate and not predicate(event):
                    raise AssertionError(f"unexpected {method} predicate")
                return event

        doc = export_wiz.WizDoc("kb", "doc", "笔记", "/", "note", "", 0, 0, {})
        with tempfile.TemporaryDirectory() as temp_dir:
            saver = export_wiz.ResourceSaver(FakeCdp(), doc, Path(temp_dir) / "笔记.md", "https://as.wiz.cn", argparse.Namespace(request_delay=0, request_jitter=0))
            with self.assertRaisesRegex(export_wiz.ExportError, "不是图片"):
                saver.fetch_image_via_browser("https://as.wiz.cn/resources/progress")

    def test_browser_network_resource_fallback_reads_image_stream(self) -> None:
        class FakeCdp:
            def __init__(self) -> None:
                self.closed = False

            def send(self, method, params=None, timeout=0):
                if method == "Page.getFrameTree":
                    return {"result": {"frameTree": {"frame": {"id": "frame-1"}}}}
                if method == "Network.loadNetworkResource":
                    return {"result": {"resource": {
                        "success": True,
                        "headers": {"content-type": "image/png"},
                        "stream": "stream-1",
                    }}}
                if method == "IO.read":
                    return {"result": {"data": "aW1hZ2U=", "base64Encoded": True, "eof": True}}
                if method == "IO.close":
                    self.closed = True
                    return {"result": {}}
                raise AssertionError(method)

        doc = export_wiz.WizDoc("kb", "doc", "笔记", "/", "note", "", 0, 0, {})
        cdp = FakeCdp()
        with tempfile.TemporaryDirectory() as temp_dir:
            saver = export_wiz.ResourceSaver(cdp, doc, Path(temp_dir) / "笔记.md", "https://as.wiz.cn", argparse.Namespace(request_delay=0, request_jitter=0))
            result = saver.fetch_image_via_network_resource("https://gips1.baidu.com/image.png")

        self.assertEqual(result, {"base64": "aW1hZ2U=", "contentType": "image/png"})
        self.assertTrue(cdp.closed)

    def test_fetch_image_uses_network_resource_before_new_image_request(self) -> None:
        args = argparse.Namespace(request_delay=0, request_jitter=0)
        doc = export_wiz.WizDoc("kb", "doc", "笔记", "/", "note", "", 0, 0, {})
        payload = {"base64": "aW1hZ2U=", "contentType": "image/png"}
        with tempfile.TemporaryDirectory() as temp_dir:
            saver = export_wiz.ResourceSaver(None, doc, Path(temp_dir) / "笔记.md", "https://as.wiz.cn", args)
            with (
                patch.object(saver, "fetch_base64", side_effect=RuntimeError("CORS")),
                patch.object(saver, "fetch_image_via_network_resource", return_value=payload) as network_resource,
                patch.object(saver, "fetch_image_via_browser") as image_request,
            ):
                result = saver.fetch_image_base64("https://gips1.baidu.com/image.png")

        self.assertEqual(result, payload)
        network_resource.assert_called_once_with("https://gips1.baidu.com/image.png")
        image_request.assert_not_called()

    def test_cors_blocked_image_uses_browser_network_fallback(self) -> None:
        args = argparse.Namespace(request_delay=0, request_jitter=0)
        doc = export_wiz.WizDoc("kb", "doc", "笔记", "/", "note", "", 0, 0, {})
        with tempfile.TemporaryDirectory() as temp_dir:
            saver = export_wiz.ResourceSaver(
                None,
                doc,
                Path(temp_dir) / "笔记.md",
                "https://as.wiz.cn",
                args,
            )
            payload = {"base64": "aW1hZ2U=", "contentType": "image/png"}
            with (
                patch.object(saver, "fetch_base64", side_effect=RuntimeError("CORS")),
                patch.object(saver, "fetch_image_via_browser", return_value=payload) as fallback,
            ):
                result = saver.fetch_image_base64("https://img0.baidu.com/image.png")

        self.assertEqual(result, payload)
        fallback.assert_called_once_with("https://img0.baidu.com/image.png")

    def test_collaboration_external_image_does_not_use_wiz_browser_credentials(self) -> None:
        args = argparse.Namespace(request_delay=0, request_jitter=0)
        doc = export_wiz.WizDoc("kb", "doc", "笔记", "/", "note", "", 0, 0, {})
        payload = {"base64": "aW1hZ2U=", "contentType": "image/png"}
        with tempfile.TemporaryDirectory() as temp_dir:
            saver = export_wiz.ResourceSaver(
                None,
                doc,
                Path(temp_dir) / "笔记.md",
                "https://as.wiz.cn",
                args,
            )
            with (
                patch.object(saver, "fetch_external_base64", return_value=payload) as external,
                patch.object(saver, "fetch_base64") as authenticated_fetch,
                patch.object(saver, "fetch_base64_via_browser") as browser_fallback,
            ):
                result = saver.save_collab_image("https://img0.baidu.com/image.png")

        self.assertEqual(result, "笔记_assets/001-image.png")
        external.assert_called_once_with("https://img0.baidu.com/image.png")
        authenticated_fetch.assert_not_called()
        browser_fallback.assert_not_called()

    def test_wiz_upgrade_page_is_not_valid_note_content(self) -> None:
        html = """
        <html><body>
          当前客户端版本较低，无法编辑协作笔记
          The current client version is too low to edit collaborative notes
          <a href="https://as.wiz.cn/upgrade">升级客户端</a>
        </body></html>
        """

        self.assertTrue(export_wiz.is_wiz_upgrade_page(html))

    def test_wiz_upgrade_page_is_reported_instead_of_exported(self) -> None:
        args = argparse.Namespace(request_delay=0, request_jitter=0)
        doc = export_wiz.WizDoc("kb", "doc", "协作笔记", "/", "note", "", 0, 0, {})
        html = "当前客户端版本较低，无法编辑协作笔记"
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(export_wiz, "fetch_ot_document", return_value=None),
            patch.object(export_wiz, "fetch_note_download", return_value={"html": html}),
        ):
            with self.assertRaisesRegex(export_wiz.ExportError, "客户端升级提示"):
                export_wiz.export_doc(
                    None,
                    {"account": {}, "kbs": [{"kbGuid": "kb", "kbServer": "https://as.wiz.cn"}]},
                    doc,
                    Path(temp_dir) / "协作笔记.md",
                    args,
                )

    def test_upgrade_page_uses_matching_editor_dom_as_content_fallback(self) -> None:
        args = argparse.Namespace(request_delay=0, request_jitter=0)
        doc = export_wiz.WizDoc("kb", "doc", "协作笔记", "/", "collaboration", "", 0, 0, {})
        editor_html = """
        <div class="editor-container root-container editor-with-title">
          <h1 class="title-block">协作笔记</h1>
          <div data-node-type="block"><div class="editor-text-node">网页正文可导出。</div></div>
        </div>
        """
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(export_wiz, "fetch_ot_document", return_value=None),
            patch.object(export_wiz, "fetch_note_download", return_value={"html": "当前客户端版本较低，无法编辑协作笔记"}),
            patch.object(export_wiz, "fetch_dom_editor_document", return_value={"html": editor_html, "title": "协作笔记", "blockCount": 1}),
        ):
            target = Path(temp_dir) / "协作笔记.md"
            export_wiz.export_doc(
                None,
                {"account": {}, "kbs": [{"kbGuid": "kb", "kbServer": "https://as.wiz.cn"}]},
                doc,
                target,
                args,
            )
            self.assertIn("网页正文可导出。", target.read_text(encoding="utf-8"))

    def test_explicitly_empty_successful_note_exports_its_title(self) -> None:
        args = argparse.Namespace(request_delay=0, request_jitter=0)
        doc = export_wiz.WizDoc("kb", "doc", "空白笔记", "/", "note", "", 0, 0, {})
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "空白笔记.md"
            with (
                patch.object(export_wiz, "fetch_ot_document", return_value=None),
                patch.object(export_wiz, "fetch_note_download", return_value={"html": ""}),
            ):
                export_wiz.export_doc(
                    None,
                    {"account": {}, "kbs": [{"kbGuid": "kb", "kbServer": "https://as.wiz.cn"}]},
                    doc,
                    target,
                    args,
                )

            self.assertEqual(target.read_text(encoding="utf-8"), "# 空白笔记\n")

    def test_successful_note_with_empty_html_markup_exports_its_title(self) -> None:
        args = argparse.Namespace(request_delay=0, request_jitter=0)
        doc = export_wiz.WizDoc("kb", "doc", "大纲笔记", "/", "note", "", 0, 0, {})
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "大纲笔记.md"
            with (
                patch.object(export_wiz, "fetch_ot_document", return_value=None),
                patch.object(export_wiz, "fetch_note_download", return_value={"html": "<p><br></p>"}),
            ):
                export_wiz.export_doc(
                    None,
                    {"account": {}, "kbs": [{"kbGuid": "kb", "kbServer": "https://as.wiz.cn"}]},
                    doc,
                    target,
                    args,
                )

            self.assertEqual(target.read_text(encoding="utf-8"), "# 大纲笔记\n")

if __name__ == "__main__":
    unittest.main()
