from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from plugins.wps.backend import export_wps


class FakeBrowserProcess:
    def __init__(self) -> None:
        self.terminated = False
        self.killed = False
        self.waited = False

    def poll(self):
        return None if not self.terminated and not self.killed else 0

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None) -> None:
        self.waited = True

    def kill(self) -> None:
        self.killed = True


class FakeCDP:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class WPSLoginRegressionTests(unittest.TestCase):
    def test_login_keeps_stdout_as_one_json_result_and_leaves_owned_browser_open(self) -> None:
        cdp = FakeCDP()
        browser = FakeBrowserProcess()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(export_wps, "connect_wps_browser", return_value=(cdp, browser)),
            mock.patch.object(export_wps, "save_auth_state", return_value={"cookieCount": 2}),
            mock.patch("sys.stdin", io.StringIO("\n")),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = export_wps.main(["--login"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), {
            "provider": "wps-export",
            "status": "authenticated",
            "cookieCount": 2,
        })
        self.assertIn("Enter", stderr.getvalue())
        self.assertTrue(cdp.closed)
        self.assertFalse(browser.terminated)
        self.assertFalse(browser.waited)

    def test_connect_waits_until_wps_page_is_ready_before_returning(self) -> None:
        class ReadyStateCDP(FakeCDP):
            def __init__(self) -> None:
                super().__init__()
                self.evaluations = []
                self.states = iter((
                    {"href": "about:blank", "readyState": "complete"},
                    {"href": "https://www.kdocs.cn/latest", "readyState": "interactive"},
                ))

            def connect(self) -> None:
                pass

            def send(self, method, params=None, timeout=30):
                return {}

            def evaluate(self, expression, timeout=60):
                self.evaluations.append(expression)
                return next(self.states)

        cdp = ReadyStateCDP()
        browser = FakeBrowserProcess()
        page = {
            "type": "page",
            "url": "https://www.kdocs.cn/latest",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9237/devtools/page/1",
        }
        args = SimpleNamespace(port=9237, profile_dir=None, browser_path=None)
        with (
            mock.patch.object(export_wps, "chrome_debug_available", return_value=True),
            mock.patch.object(export_wps, "http_json", return_value=[page]),
            mock.patch.object(export_wps, "CDPClient", return_value=cdp),
            mock.patch.object(export_wps.time, "sleep"),
        ):
            connected, owned_process = export_wps.connect_wps_browser(args)

        self.assertIs(connected, cdp)
        self.assertIsNone(owned_process)
        self.assertGreaterEqual(len(cdp.evaluations), 2)

    def test_close_owned_browser_kills_when_graceful_shutdown_times_out(self) -> None:
        class StubbornProcess(FakeBrowserProcess):
            def wait(self, timeout=None) -> None:
                raise TimeoutError("still running")

        browser = StubbornProcess()
        export_wps.close_owned_browser(None, browser)
        self.assertTrue(browser.terminated)
        self.assertTrue(browser.killed)

    def test_api_item_without_title_gets_stable_fallback_name(self) -> None:
        normalized = export_wps._normalize_api_item(
            {"id": "file-123", "type": "file", "name": ""},
            "special",
        )
        self.assertEqual(normalized["title"], "未命名文件-file-123")
        self.assertEqual(normalized["parent_id"], "special")


    def test_plugin_data_paths_do_not_repeat_wps_directory(self) -> None:
        plugin_data = Path("C:/Users/test/AppData/Roaming/wandao/plugin-data/wps")
        with mock.patch.object(export_wps, "default_data_dir", return_value=plugin_data):
            self.assertEqual(export_wps.default_auth_path(), plugin_data / "auth.json")
            self.assertEqual(export_wps.default_profile_path(), plugin_data / "browser-profile")

    def test_save_auth_reads_minimal_official_cookies_then_verifies_session(self) -> None:
        events = []

        class AuthCDP:
            def send(self, method, params=None, timeout=30):
                events.append(method)
                if method == "Network.getAllCookies":
                    return {"result": {"cookies": [
                        {"name": "wps_sid", "value": "session", "domain": ".kdocs.cn", "path": "/"},
                        {"name": "kso_sid", "value": "secondary", "domain": ".kdocs.cn", "path": "/"},
                        {"name": "csrf", "value": "csrf-value", "domain": ".kdocs.cn", "path": "/"},
                        {"name": "uid", "value": "private-user-id", "domain": ".kdocs.cn", "path": "/"},
                        {"name": "wps_sid", "value": "foreign", "domain": ".example.com", "path": "/"},
                    ]}}
                return {}

        captured = {}
        with (
            mock.patch.object(export_wps, "verify_cloud_session", side_effect=lambda _cdp: events.append("verify") or True),
            mock.patch.object(export_wps, "write_private_json", side_effect=lambda path, payload: captured.update(path=path, payload=payload)),
        ):
            result = export_wps.save_auth_state(AuthCDP(), Path("auth.json"))

        self.assertEqual(events, ["Network.enable", "Network.getAllCookies", "verify"])
        self.assertEqual(result["cookieCount"], 3)
        self.assertEqual({cookie["name"] for cookie in captured["payload"]["cookies"]}, {"wps_sid", "kso_sid", "csrf"})
        self.assertNotIn("uid", {cookie["name"] for cookie in captured["payload"]["cookies"]})

    def test_save_auth_rejects_missing_session_cookie_with_readable_message(self) -> None:
        cdp = mock.Mock()
        cdp.send.side_effect = [
            {},
            {"result": {"cookies": [
                {"name": "csrf", "value": "csrf-value", "domain": ".kdocs.cn", "path": "/"},
            ]}},
        ]

        with self.assertRaisesRegex(export_wps.ExportError, "未找到 WPS 登录凭证"):
            export_wps.save_auth_state(cdp, Path("auth.json"))


    def test_load_auth_restores_cookies_and_rejects_unverified_session(self) -> None:
        cdp = mock.Mock()
        payload = {"version": 1, "cookies": [
            {"name": "wps_sid", "value": "session", "domain": ".kdocs.cn", "path": "/"},
            {"name": "csrf", "value": "csrf-value", "domain": ".kdocs.cn", "path": "/"},
        ]}
        with (
            mock.patch.object(Path, "read_text", return_value=json.dumps(payload)),
            mock.patch.object(export_wps, "verify_cloud_session", return_value=False),
        ):
            with self.assertRaisesRegex(export_wps.ExportError, "WPS 登录状态已失效，请重新登录"):
                export_wps.load_auth_state(cdp, Path("auth.json"))

        methods = [call.args[0] for call in cdp.send.call_args_list]
        self.assertEqual(methods, ["Network.enable", "Network.setCookies"])

    def test_verify_session_navigates_to_document_origin_and_uses_search_api(self) -> None:
        class VerifyCDP:
            def __init__(self) -> None:
                self.sent = []
                self.expressions = []

            def send(self, method, params=None, timeout=30):
                self.sent.append((method, params))
                return {}

            def evaluate(self, expression, timeout=60):
                self.expressions.append(expression)
                if "readyState" in expression:
                    return {"href": "https://365.kdocs.cn/", "readyState": "complete"}
                return {"status": 200, "contentType": "application/json", "payload": {"files": []}}

        cdp = VerifyCDP()
        self.assertTrue(export_wps.verify_cloud_session(cdp))
        self.assertEqual(cdp.sent[0][0], "Page.navigate")
        self.assertEqual(cdp.sent[0][1]["url"], "https://365.kdocs.cn")
        self.assertTrue(any("/3rd/drive/api/v6/search/files" in expression for expression in cdp.expressions))

    def test_get_transport_does_not_add_csrf_header(self) -> None:
        cdp = mock.Mock()
        cdp.evaluate.return_value = {"status": 200, "headers": {}, "payload": {"files": []}}
        transport = export_wps.CDPJSONTransport(cdp)

        transport.request_json("GET", "https://365.kdocs.cn/3rd/drive/api/v6/search/files")

        expression = cdp.evaluate.call_args.args[0]
        post_guard = expression.index("if (method === 'POST'")
        csrf_header = expression.index("if (csrfValue) headers['x-csrf-rand']")
        self.assertLess(post_guard, csrf_header)

    def test_transport_status_zero_is_not_treated_as_success(self) -> None:
        transport = mock.Mock()
        transport.request_json.return_value = {"status": 0, "headers": {}, "payload": {}}
        source = export_wps.WPSDocumentDataSource(transport=transport)

        with self.assertRaises(export_wps.WPSApiError):
            source.list_children(export_wps.WPS_DOCUMENT_ROOT_ID)


    def test_root_uses_wps_document_label(self) -> None:
        source = export_wps.WPSDocumentDataSource(transport=mock.Mock())
        root = source.get_root()
        self.assertEqual(root["title"], "WPS 文档")


if __name__ == "__main__":
    unittest.main()
