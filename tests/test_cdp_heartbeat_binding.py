"""Regression tests for the CDP binding heartbeat channel.

Feishu's production bundle replaces ``console.log``/``console.info``/
``console.warn`` with ``function(){}`` stubs. Plugins emit long-running
heartbeats through ``console.info(prefix + json)``, so those heartbeats were
silently swallowed and the 45s idle watchdog aborted a perfectly healthy page.

The fix routes heartbeats through a browser-owned binding
(``Runtime.addBinding``), which page code cannot stub out, and keeps the console
channel working for compatibility.
"""

from __future__ import annotations

import unittest

from wandao_core.browser import (
    HEARTBEAT_BINDING_NAME,
    HEARTBEAT_BRIDGE_JS,
    HEARTBEAT_MARKER,
    CDPClient,
    ExportError,
    console_heartbeat_watchdog,
    heartbeat_as_console_event,
)

PREFIX = "__WANDAO_TEST_WATCHDOG__"


def binding_event(payload: str, *, name: str = HEARTBEAT_BINDING_NAME) -> dict:
    return {
        "method": "Runtime.bindingCalled",
        "params": {"name": name, "payload": payload, "executionContextId": 7},
    }


def console_event(value: str) -> dict:
    return {"method": "Runtime.consoleAPICalled", "params": {"args": [{"type": "string", "value": value}]}}


class RecordingClient(CDPClient):
    """A client that records outbound CDP calls instead of touching a socket."""

    def __init__(self) -> None:
        super().__init__("ws://127.0.0.1:9222/devtools/page/DEADBEEF")
        self.sock = object()  # type: ignore[assignment]
        self.sent: list[tuple[str, dict]] = []

    def send(self, method, params=None, timeout=30, *, max_timeout=None, watchdog=None):  # type: ignore[override]
        self.sent.append((method, dict(params or {})))
        return {"id": 1, "result": {"result": {"value": True}}}

    def methods(self) -> list[str]:
        return [method for method, _params in self.sent]


class IdleClient(CDPClient):
    """A client whose peer only ever emits events that never renew the watchdog."""

    def __init__(self) -> None:
        super().__init__("ws://127.0.0.1:9222/devtools/page/DEADBEEF")
        self.sock = object()  # type: ignore[assignment]

    def _send_text(self, _text: str) -> None:
        return None

    def _recv_checked(self, _label: str, _timeout: float) -> dict:
        return {"method": "Runtime.executionContextCreated", "params": {}}


class ConsoleChannelTests(unittest.TestCase):
    """The pre-existing console channel must keep working unchanged."""

    def test_console_heartbeat_still_renews(self) -> None:
        received: list[dict] = []
        watchdog = console_heartbeat_watchdog(PREFIX, lambda payload, _elapsed: received.append(payload))

        self.assertTrue(watchdog(console_event(PREFIX + '{"phase":"page","count":3}')))
        self.assertEqual(received[0]["phase"], "page")
        self.assertEqual(received[0]["count"], 3)

    def test_console_heartbeat_requires_the_prefix(self) -> None:
        watchdog = console_heartbeat_watchdog(PREFIX)

        self.assertFalse(watchdog(console_event("unrelated page log")))
        self.assertFalse(watchdog({"method": "Runtime.consoleAPICalled", "params": {"args": []}}))


class BindingChannelTests(unittest.TestCase):
    def test_binding_heartbeat_renews_watchdog(self) -> None:
        received: list[tuple[dict, int]] = []
        watchdog = console_heartbeat_watchdog(
            PREFIX,
            lambda payload, elapsed: received.append((payload, elapsed)),
        )

        self.assertTrue(watchdog(binding_event(PREFIX + '{"phase":"读取目录","nodes":12}')))
        self.assertEqual(received[0][0]["phase"], "读取目录")
        self.assertEqual(received[0][0]["nodes"], 12)
        self.assertGreaterEqual(received[0][1], 0)
        self.assertIn("elapsedSeconds", received[0][0])

    def test_binding_heartbeat_ignores_other_binding_names(self) -> None:
        watchdog = console_heartbeat_watchdog(PREFIX)

        self.assertFalse(watchdog(binding_event(PREFIX + '{"phase":"x"}', name="somePageBinding")))

    def test_binding_heartbeat_requires_the_prefix(self) -> None:
        watchdog = console_heartbeat_watchdog(PREFIX)

        self.assertFalse(watchdog(binding_event("page-owned binding traffic")))
        self.assertFalse(watchdog(binding_event("")))

    def test_unrelated_events_never_renew(self) -> None:
        """Reverse case: traffic that proves nothing must not extend the deadline."""

        watchdog = console_heartbeat_watchdog(PREFIX)

        for message in (
            {"method": "Runtime.executionContextCreated", "params": {}},
            {"method": "Page.loadEventFired", "params": {}},
            {"method": "Runtime.bindingCalled"},
            {"method": "Runtime.bindingCalled", "params": "not-a-dict"},
        ):
            with self.subTest(message=message):
                self.assertFalse(watchdog(message))


class ReplayTests(unittest.TestCase):
    """Plugin watchdogs that only understand consoleAPICalled must keep working."""

    def test_binding_heartbeat_is_replayed_as_a_console_event(self) -> None:
        replayed = heartbeat_as_console_event(binding_event(PREFIX + '{"phase":"page"}'))

        self.assertIsNotNone(replayed)
        assert replayed is not None
        self.assertEqual(replayed["method"], "Runtime.consoleAPICalled")
        self.assertEqual(replayed["params"]["args"][0]["value"], PREFIX + '{"phase":"page"}')
        self.assertEqual(replayed["params"]["executionContextId"], 7)

    def test_replay_ignores_non_heartbeat_bindings(self) -> None:
        self.assertIsNone(heartbeat_as_console_event(console_event(PREFIX + "{}")))
        self.assertIsNone(heartbeat_as_console_event(binding_event("no marker here")))
        self.assertIsNone(heartbeat_as_console_event({"method": "Runtime.bindingCalled", "params": "not-a-dict"}))

    def test_console_only_watchdog_renews_on_a_binding_event(self) -> None:
        """The end-to-end path: a console-only watchdog still sees the heartbeat."""

        seen: list[dict] = []

        def console_only(message: dict) -> bool:
            if message.get("method") != "Runtime.consoleAPICalled":
                return False
            seen.append(message)
            return True

        client = RecordingClient()
        self.assertTrue(client._watchdog_renewed(console_only, binding_event(PREFIX + '{"phase":"page"}')))
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["method"], "Runtime.consoleAPICalled")

    def test_watchdog_raising_on_binding_events_still_gets_the_replay(self) -> None:
        """A watchdog that assumes consoleAPICalled must not swallow the heartbeat."""

        def strict(message: dict) -> bool:
            if message.get("method") != "Runtime.consoleAPICalled":
                raise KeyError("args")
            return True

        client = RecordingClient()
        self.assertTrue(client._watchdog_renewed(strict, binding_event(PREFIX + '{"phase":"page"}')))


class BridgeInstallationTests(unittest.TestCase):
    def test_add_binding_sends_runtime_addbinding(self) -> None:
        client = RecordingClient()

        client.add_binding(HEARTBEAT_BINDING_NAME)

        self.assertEqual(client.sent[0][0], "Runtime.addBinding")
        self.assertEqual(client.sent[0][1]["name"], HEARTBEAT_BINDING_NAME)

    def test_evaluate_installs_the_binding_before_a_watched_send(self) -> None:
        client = RecordingClient()

        client.evaluate("1 + 1", watchdog=lambda _message: False)

        self.assertEqual(
            client.methods(),
            ["Runtime.addBinding", "Runtime.evaluate", "Runtime.evaluate"],
        )
        self.assertEqual(client.sent[0][1]["name"], HEARTBEAT_BINDING_NAME)
        self.assertEqual(client.sent[1][1]["expression"], HEARTBEAT_BRIDGE_JS)
        self.assertEqual(client.sent[2][1]["expression"], "1 + 1")

    def test_evaluate_skips_the_bridge_without_a_watchdog(self) -> None:
        client = RecordingClient()

        client.evaluate("1 + 1")

        self.assertEqual(client.methods(), ["Runtime.evaluate"])
        self.assertEqual(client.sent[0][1]["expression"], "1 + 1")

    def test_binding_is_added_once_but_the_page_shim_is_reinjected(self) -> None:
        """The binding survives navigation; the page-side shim does not."""

        client = RecordingClient()

        client.evaluate("first", watchdog=lambda _message: False)
        client.navigate("https://example.feishu.cn/wiki/TOKEN")
        client.evaluate("second", watchdog=lambda _message: False)

        self.assertEqual(client.methods().count("Runtime.addBinding"), 1)
        self.assertEqual(client.methods().count("Page.navigate"), 1)
        self.assertEqual(client.methods().count("Runtime.evaluate"), 4)

    def test_bridge_script_is_prefix_scoped_and_self_healing(self) -> None:
        """The shim must forward only Wandao heartbeats and survive re-stubbing."""

        self.assertIn(HEARTBEAT_BINDING_NAME, HEARTBEAT_BRIDGE_JS)
        self.assertIn(HEARTBEAT_MARKER, HEARTBEAT_BRIDGE_JS)
        # Per-method marker rather than one global flag: a page that re-assigns
        # console.info after us gets patched again on the next evaluation.
        self.assertIn("__wandaoHeartbeatBridge", HEARTBEAT_BRIDGE_JS)
        self.assertIn("return original.apply(this, args)", HEARTBEAT_BRIDGE_JS)


class WatchdogIdleDeadlineTests(unittest.TestCase):
    def test_idle_deadline_reports_the_heartbeat_timeout(self) -> None:
        """The watchdog message used to be unreachable dead code."""

        client = IdleClient()

        with self.assertRaisesRegex(ExportError, "看门狗超时"):
            client.send("Runtime.evaluate", {}, timeout=0.05, max_timeout=30, watchdog=lambda _message: False)

    def test_no_watchdog_keeps_the_plain_timeout_message(self) -> None:
        client = IdleClient()

        with self.assertRaisesRegex(ExportError, "Timed out waiting for CDP response"):
            client.send("Runtime.evaluate", {}, timeout=0.05)


if __name__ == "__main__":
    unittest.main()
