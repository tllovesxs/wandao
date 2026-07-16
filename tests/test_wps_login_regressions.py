from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
    def test_login_keeps_stdout_as_one_json_result_and_closes_owned_browser(self) -> None:
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
        self.assertTrue(browser.terminated)
        self.assertTrue(browser.waited)

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

    def test_root_without_title_uses_personal_cloud_label(self) -> None:
        source = export_wps.WPSApiDataSource(
            transport=mock.Mock(request_json=mock.Mock(return_value={"data": {"id": "special"}}))
        )
        root = source.get_root()
        self.assertEqual(root["title"], "我的云文档")


if __name__ == "__main__":
    unittest.main()
