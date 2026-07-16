from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from plugins.wps.backend import export_wps


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request_json(self, method, url, params=None, body=None):
        self.calls.append((method, url, params, body))
        return self.responses.pop(0)


class WPSDocumentTests(unittest.TestCase):
    def test_document_source_includes_smart_and_regular_documents_but_excludes_device_documents(self):
        transport = FakeTransport([{
            "status": 200,
            "payload": {
                "files": [
                    {"fileid": "101", "groupid": "7", "fname": "智能文档 A", "filetype": "o", "modify_time": 123},
                    {"fileid": "102", "fname": "普通文档", "filetype": "d"},
                    {"fileid": "103", "fname": "设备文档", "filetype": "d", "location": "device"},
                    {"fileid": "104", "fname": "文件夹", "type": "folder"},
                    {"fileid": "105", "fname": "回收站文档", "filetype": "d", "status": "trash"},
                    {"fname": "缺少 ID 的文档", "filetype": "d"},
                ],
                "next_offset": 0,
            },
        }])
        source = export_wps.WPSDocumentDataSource(transport=transport)

        nodes, cursor = source.list_children(export_wps.WPS_DOCUMENT_ROOT_ID)

        self.assertIsNone(cursor)
        self.assertEqual([node["file_id"] for node in nodes], ["101", "102"])
        self.assertEqual(nodes[0]["title"], "智能文档 A")
        self.assertEqual(nodes[0]["group_id"], "7")
        self.assertEqual(nodes[1]["title"], "普通文档")
        self.assertEqual([node["type"] for node in nodes], ["file", "file"])
        self.assertEqual(source.get_root()["title"], "WPS 文档")
        self.assertEqual(transport.calls[0][0], "GET")
        self.assertIn("/3rd/drive/api/v6/search/files", transport.calls[0][1])
        self.assertEqual(transport.calls[0][2]["searchname"], "")

    def test_actual_search_shape_excludes_folders_and_device_records(self):
        transport = FakeTransport([{
            "status": 200,
            "payload": {
                "files": [
                    {"id": "201", "fname": "Cloud document", "ftype": "file"},
                    {"id": "202", "fname": "Folder", "ftype": "folder"},
                    {"id": "203", "fname": "Device record", "ftype": "sharefile", "device_info": {"device_id": "private"}},
                ],
                "total": 3,
            },
        }])
        source = export_wps.WPSDocumentDataSource(transport=transport)

        nodes, cursor = source.list_children(export_wps.WPS_DOCUMENT_ROOT_ID)

        self.assertIsNone(cursor)
        self.assertEqual([node["file_id"] for node in nodes], ["201"])

    def test_search_forbidden_is_reported_as_an_expired_session(self):
        transport = FakeTransport([{"status": 403, "payload": {"result": "loginRequired"}}])
        source = export_wps.WPSDocumentDataSource(transport=transport)

        with self.assertRaises(export_wps.WPSAuthExpiredError):
            source.list_children(export_wps.WPS_DOCUMENT_ROOT_ID)

    def test_search_paginates_using_total_when_next_offset_is_absent(self):
        transport = FakeTransport([{
            "status": 200,
            "payload": {
                "files": [
                    {"id": "301", "fname": "Document one", "ftype": "file"},
                    {"id": "302", "fname": "Document two", "ftype": "file"},
                ],
                "total": 5,
            },
        }])
        source = export_wps.WPSDocumentDataSource(transport=transport)

        _nodes, cursor = source.list_children(export_wps.WPS_DOCUMENT_ROOT_ID)

        self.assertEqual(export_wps._decode_cursor(cursor)["offset"], 2)


    def test_smart_document_download_does_not_use_personal_cloud_special_group_api(self):
        transport = FakeTransport([{
            "status": 200,
            "payload": {"url": "https://download.wpscdn.cn/file/101?signature=secret"},
        }])
        source = export_wps.WPSDocumentDataSource(transport=transport)

        url = source.open_download("101")

        self.assertEqual(url, "https://download.wpscdn.cn/file/101?signature=secret")
        self.assertIn("/api/v3/office/file/101/download", transport.calls[0][1])
        self.assertNotIn("groups/special", transport.calls[0][1])

    def test_unsupported_office_download_uses_group_original_file_endpoint(self):
        transport = FakeTransport([
            {"status": 403, "payload": {"result": "unSupport", "errno": 10000}},
            {"status": 200, "payload": {"fileinfo": {"url": "https://download.wpscdn.cn/file/101?signature=secret"}}},
        ])
        source = export_wps.WPSDocumentDataSource(transport=transport)

        url = source.open_download("101", "7")

        self.assertEqual(url, "https://download.wpscdn.cn/file/101?signature=secret")
        self.assertIn("/api/v3/office/file/101/download", transport.calls[0][1])
        self.assertIn("drive.wps.cn/api/v3/groups/7/files/101/download", transport.calls[1][1])

    def test_smart_document_group_rejection_still_allows_markdown_fallback(self):
        transport = FakeTransport([
            {"status": 403, "payload": {"result": "unSupport", "errno": 10000}},
            {"status": 403, "payload": {"result": "notAllowType"}},
        ])
        source = export_wps.WPSDocumentDataSource(transport=transport)

        with self.assertRaises(export_wps.WPSDownloadUnavailableError) as caught:
            source.open_download("101", "7")

        self.assertTrue(caught.exception.allow_content_query)
        self.assertNotIsInstance(caught.exception, export_wps.WPSAuthExpiredError)

    def test_online_only_sheet_is_reported_as_unsupported_not_session_expired(self):
        transport = FakeTransport([
            {"status": 403, "payload": {"result": "unSupport", "errno": 10000}},
            {"status": 403, "payload": {"result": "ErrForbidDownloadLinkFile"}},
        ])
        source = export_wps.WPSDocumentDataSource(transport=transport)

        with self.assertRaises(export_wps.WPSDownloadUnavailableError) as caught:
            source.open_download("101", "7")

        self.assertFalse(caught.exception.allow_content_query)
        self.assertIn("智能表格", str(caught.exception))
        self.assertNotIsInstance(caught.exception, export_wps.WPSAuthExpiredError)

    def test_real_unauthorized_download_stays_an_authentication_error(self):
        transport = FakeTransport([{"status": 401, "payload": {}}])
        source = export_wps.WPSDocumentDataSource(transport=transport)

        with self.assertRaises(export_wps.WPSAuthExpiredError):
            source.open_download("101", "7")

        self.assertEqual(len(transport.calls), 1)

    def test_unknown_forbidden_download_rechecks_session_before_reporting_expiry(self):
        transport = FakeTransport([
            {"status": 403, "payload": {"result": "forbidden"}},
            {"status": 403, "payload": {"result": "loginRequired"}},
        ])
        source = export_wps.WPSDocumentDataSource(transport=transport)

        with self.assertRaises(export_wps.WPSAuthExpiredError):
            source.open_download("101", "7")

        self.assertIn("/3rd/drive/api/v6/search/files", transport.calls[1][1])

    def test_unknown_forbidden_download_with_valid_session_is_not_auth_expiry(self):
        transport = FakeTransport([
            {"status": 403, "payload": {"result": "forbidden"}},
            {"status": 200, "payload": {"files": []}},
        ])
        source = export_wps.WPSDocumentDataSource(transport=transport)

        with self.assertRaises(export_wps.WPSDownloadUnavailableError) as caught:
            source.open_download("101", "7")

        self.assertNotIsInstance(caught.exception, export_wps.WPSAuthExpiredError)
        self.assertIn("下载权限", str(caught.exception))

    def test_smart_document_content_query_is_read_only(self):
        transport = FakeTransport([{
            "status": 200,
            "payload": {"result": "ok", "detail": {"result": {"blocks": []}}},
        }])
        source = export_wps.WPSDocumentDataSource(transport=transport)

        source.query_content("101")

        method, url, _params, body = transport.calls[0]
        self.assertEqual(method, "POST")
        self.assertIn("/api/v3/office/file/101/core/execute", url)
        self.assertEqual(body["command"], "http.otl.query")
        self.assertEqual(body["param"]["name"], "block.query")
        self.assertNotIn("exec", json.dumps(body))

    def test_export_parser_accepts_provider_progress_argument(self):
        args = export_wps.build_parser().parse_args(["--progress-every", "1"])

        self.assertEqual(args.progress_every, 1)

    def test_smart_document_falls_back_to_markdown_when_original_download_is_unavailable(self):
        encoded = base64.b64encode(json.dumps({
            "blocks": [{
                "id": "doc",
                "type": "doc",
                "content": [
                    {"type": "title", "content": [{"type": "text", "content": "Internal title"}]},
                    {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "content": "Section"}]},
                    {"type": "paragraph", "content": [
                        {"type": "text", "content": "Body"},
                        {"type": "text", "attrs": {"bold": True}, "content": "bold"},
                    ]},
                    {"type": "paragraph", "attrs": {"listAttrs": {"type": 1, "level": 0}}, "content": [
                        {"type": "text", "content": "Item"},
                    ]},
                ],
            }],
        }).encode("utf-8")).decode("ascii")
        source = mock.Mock(spec=export_wps.WPSDocumentDataSource)
        source.open_download.side_effect = export_wps.WPSDownloadUnavailableError(
            "Original download unavailable", status=404, allow_content_query=True
        )
        source.query_content.return_value = {"detail": {"result": encoded}}
        root = export_wps.WPSNode(
            id=export_wps.WPS_DOCUMENT_ROOT_ID, file_id="", title="WPS Documents", parent_id=None, type="folder"
        )
        node = export_wps.WPSNode(
            id="101", file_id="101", title="Smart document", parent_id=export_wps.WPS_DOCUMENT_ROOT_ID, type="file", group_id="7"
        )

        with tempfile.TemporaryDirectory() as directory:
            task = export_wps.WPSExportTask(source, Path(directory))
            report = task.export([root, node], ["101"])
            exported = Path(directory) / "WPS Documents" / "Smart document.md"

            self.assertEqual(report["successCount"], 1)
            self.assertEqual(report["failureCount"], 0)
            self.assertTrue(exported.is_file())
            self.assertEqual(
                exported.read_text(encoding="utf-8"),
                "# Internal title\n\n## Section\n\nBody**bold**\n\n- Item\n",
            )
            source.open_download.assert_called_once_with("101", "7")
            source.query_content.assert_called_once_with("101")

    def test_auth_failure_does_not_fall_back_to_content_query(self):
        source = mock.Mock(spec=export_wps.WPSDocumentDataSource)
        source.open_download.side_effect = export_wps.WPSAuthExpiredError("Session expired", status=401)
        node = export_wps.WPSNode(
            id="101", file_id="101", title="Document", parent_id=export_wps.WPS_DOCUMENT_ROOT_ID, type="file"
        )

        with tempfile.TemporaryDirectory() as directory:
            task = export_wps.WPSExportTask(source, Path(directory))
            report = task.export([node], ["101"])

        self.assertEqual(report["successCount"], 0)
        self.assertEqual(report["failureCount"], 1)
        source.query_content.assert_not_called()

    def test_scan_keeps_browser_open_after_reading_documents(self):
        cdp = mock.Mock()
        process = mock.Mock()
        args = mock.Mock(auth_file=None, request_delay=0.1)
        root = {"id": export_wps.WPS_DOCUMENT_ROOT_ID}
        source = mock.Mock()
        source.get_root.return_value = root
        with (
            mock.patch.object(export_wps, "connect_wps_browser", return_value=(cdp, process)),
            mock.patch.object(export_wps, "load_auth_state"),
            mock.patch.object(export_wps, "WPSDocumentDataSource", return_value=source),
            mock.patch.object(export_wps, "scan_tree", return_value=[]),
            mock.patch.object(export_wps, "close_owned_browser") as close_browser,
        ):
            result = export_wps.scan_wps(args)

        self.assertEqual(result, {"nodes": []})
        close_browser.assert_not_called()

    def test_export_still_closes_owned_browser(self):
        cdp = mock.Mock()
        process = mock.Mock()
        checkpoint = mock.Mock()
        task = mock.Mock()
        task.scan.return_value = []
        task.export.return_value = {"totalDocs": 0, "successCount": 0, "failureCount": 0}
        task.report_file = Path("report.json")
        args = mock.Mock(output="out", auth_file=None, selected_file_ids=[], retry_failed=False)
        with (
            mock.patch.object(export_wps, "open_checkpoint_from_args", return_value=checkpoint),
            mock.patch.object(export_wps, "connect_wps_browser", return_value=(cdp, process)),
            mock.patch.object(export_wps, "load_auth_state"),
            mock.patch.object(export_wps, "WPSDocumentDataSource"),
            mock.patch.object(export_wps, "WPSExportTask", return_value=task),
            mock.patch.object(export_wps, "_write_report"),
            mock.patch.object(export_wps, "close_owned_browser") as close_browser,
        ):
            export_wps.export_wps(args)

        close_browser.assert_called_once_with(cdp, process)
        checkpoint.close.assert_called_once_with()

    def test_login_prompt_targets_wps_documents(self):
        cdp = mock.Mock()
        process = mock.Mock()
        with (
            mock.patch.object(export_wps, "connect_wps_browser", return_value=(cdp, process)),
            mock.patch.object(export_wps, "save_auth_state", return_value={"cookieCount": 1}),
            mock.patch("sys.stdin", io.StringIO("\n")),
            mock.patch("sys.stderr", io.StringIO()) as stderr,
        ):
            export_wps.login_wps(mock.Mock())

        text = stderr.getvalue()
        self.assertIn("WPS 文档", text)
        self.assertNotIn("我的云文档", text)


if __name__ == "__main__":
    unittest.main()
