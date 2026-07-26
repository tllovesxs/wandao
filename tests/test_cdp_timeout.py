"""Regression tests for CDP read timeouts surfacing as ExportError.

``socket.recv`` raises the builtin ``TimeoutError`` when the deadline passes, so
the ``raise ExportError("Timed out waiting for ...")`` lines at the end of
``send``/``wait_for_event`` were unreachable and callers guarding with
``except ExportError`` saw an unknown exception instead.
"""

import socket
import unittest

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


if __name__ == "__main__":
    unittest.main()
