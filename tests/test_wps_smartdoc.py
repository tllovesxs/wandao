from __future__ import annotations

import io
import json
import unittest
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
                    {"fileid": "101", "fname": "智能文档 A", "filetype": "o", "modify_time": 123},
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
        self.assertEqual(nodes[1]["title"], "普通文档")
        self.assertEqual([node["type"] for node in nodes], ["file", "file"])
        self.assertEqual(source.get_root()["title"], "WPS 文档")
        self.assertEqual(transport.calls[0][0], "GET")
        self.assertIn("/3rd/drive/api/v6/search/files", transport.calls[0][1])
        self.assertEqual(transport.calls[0][2]["searchname"], "")

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
