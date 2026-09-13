"""Regression tests for CDP read timeouts surfacing as ExportError.

``socket.recv`` raises the builtin ``TimeoutError`` when the deadline passes, so
the ``raise ExportError("Timed out waiting for ...")`` lines at the end of
``send``/``wait_for_event`` were unreachable and callers guarding with
``except ExportError`` saw an unknown exception instead.
"""

import socket
import unittest
from unittest import mock

from wandao_core.browser import CDPClient, ExportError


class CDPTimeoutTests(unittest.TestCase):
    def _silent_client(self) -> CDPClient:
        """A connected client whose peer never answers, so reads time out."""
        near, far = socket.socketpair()
        self.addCleanup(near.close)
        self.addCleanup(far.close)
        client = CDPClient("ws://127.0.0.1:9222/devtools/page/DEADBEEF")
        client.sock = near
        return client

    def test_send_read_timeout_raises_export_error(self) -> None:
        client = self._silent_client()

        with self.assertRaises(ExportError) as ctx:
            client.send("Page.navigate", {"url": "https://example.com"}, timeout=0.1)

        message = str(ctx.exception)
        self.assertIn("超时", message)
        self.assertIn("Page.navigate", message)

    def test_wait_for_event_read_timeout_raises_export_error(self) -> None:
        client = self._silent_client()

        with self.assertRaises(ExportError) as ctx:
            client.wait_for_event("Page.loadEventFired", timeout=0.1)

        message = str(ctx.exception)
        self.assertIn("超时", message)
        self.assertIn("Page.loadEventFired", message)

    def test_timeout_error_does_not_escape_as_a_builtin(self) -> None:
        client = self._silent_client()

        try:
            client.send("Runtime.evaluate", {}, timeout=0.1)
        except ExportError:
            pass
        except TimeoutError as exc:  # pragma: no cover - the bug being fixed
            self.fail(f"builtin TimeoutError escaped instead of ExportError: {exc!r}")

    def test_watchdog_renews_deadline_for_page_events(self) -> None:
        class ScriptedClient(CDPClient):
            def __init__(self) -> None:
                super().__init__("ws://127.0.0.1:9222/devtools/page/DEADBEEF")
                self.sock = object()  # type: ignore[assignment]
                self.messages = [
                    {"method": "Runtime.consoleAPICalled", "params": {"args": []}},
                    {"id": 1, "result": {"result": {"value": True}}},
                ]
                self.timeouts: list[float] = []

            def _send_text(self, _text: str) -> None:
                return None

            def _recv_checked(self, _label: str, timeout: float) -> dict:
                self.timeouts.append(timeout)
                return self.messages.pop(0)

        client = ScriptedClient()
        watchdog = mock.Mock(return_value=True)
        result = client.send("Runtime.evaluate", {}, timeout=3, max_timeout=30, watchdog=watchdog)

        self.assertEqual(result["id"], 1)
        watchdog.assert_called_once()
        self.assertEqual(len(client.timeouts), 2)
        self.assertAlmostEqual(client.timeouts[0], 3, delta=0.1)
        self.assertAlmostEqual(client.timeouts[1], 3, delta=0.1)

    def test_watchdog_stops_when_no_page_heartbeat_arrives(self) -> None:
        client = self._silent_client()

        with self.assertRaisesRegex(ExportError, "看门狗超时"):
            client.send("Runtime.evaluate", {}, timeout=0.1, max_timeout=1, watchdog=lambda _message: False)


if __name__ == "__main__":
    unittest.main()
