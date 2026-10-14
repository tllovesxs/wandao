#!/usr/bin/env python3
"""Stable browser-automation helpers shared by installable Wandao plugins."""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import signal
import shutil
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from wandao_core.logging import WandaoLogger, print_text, structured_logs_enabled

DEFAULT_PORT = 9222
FORBIDDEN_FILENAME_CHARS = r'<>:"/\\|?*'
WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}

class ExportError(RuntimeError):
    pass


class CDPTimeoutError(ExportError):
    """A socket-level read timeout, distinct from a watchdog timeout."""


class CDPConnectionError(ExportError):
    """The CDP transport layer was reset (socket closed, EOF, connection refused).

    This is distinct from a timeout: the socket is dead and the client should
    reconnect before retrying rather than treating the current command as a
    per-item failure.
    """


class ExportStopped(ExportError):
    pass


# Registry: debug port -> launch info recorded by ``start_chrome`` in this
# process. CDPClient uses this to restart a browser it did not itself launch.
_browser_launch_info: dict[int, dict[str, Any]] = {}


def register_browser_launch(
    port: int,
    *,
    exe: str,
    profile_dir: Path,
    proc: subprocess.Popen[Any] | None = None,
    url: str = "",
) -> None:
    """Record how a Chrome process was started so it can be restarted later."""

    _browser_launch_info[port] = {
        "exe": exe,
        "profile_dir": str(profile_dir),
        "pid": getattr(proc, "pid", None),
        "proc": proc,
        "url": url,
    }


def terminate_registered_browser(port: int) -> None:
    """Terminate the browser tree registered for a task-owned debug port."""

    info = _browser_launch_info.pop(int(port), None)
    if not info:
        return
    proc = info.get("proc")
    pid = info.get("pid")
    if proc is not None:
        if proc.poll() is None:
            terminate_process_tree(proc)
    elif pid:
        terminate_pid_tree(int(pid))
    wait_for_debug_port_closed(int(port), timeout=15)


def default_data_dir() -> Path:
    value = os.environ.get("WANDAO_PLUGIN_DATA_DIR") or os.environ.get("WANDAO_DATA_DIR")
    return Path(value).expanduser().resolve() if value else Path.cwd().resolve()


def default_state_path(filename: str, *, migrate_legacy_file: bool = True) -> Path:
    del migrate_legacy_file
    return default_data_dir() / filename


def prepare_cookie_for_set(cookie: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "name", "value", "url", "domain", "path", "secure", "httpOnly", "sameSite",
        "expires", "priority", "sameParty", "sourceScheme", "sourcePort", "partitionKey",
    }
    result = {key: value for key, value in cookie.items() if key in allowed and value is not None}
    if result.get("expires") in (-1, 0):
        result.pop("expires", None)
    return result


def sanitize_filename(value: str, fallback: str = "未命名", max_len: int = 90) -> str:
    import re

    cleaned = "".join("-" if char in FORBIDDEN_FILENAME_CHARS or ord(char) < 32 else char for char in value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(". ")
    cleaned = (cleaned or fallback)[:max_len].rstrip(". ") or fallback
    if cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        cleaned = "_" + cleaned
    return cleaned


def pad(number: int) -> str:
    return f"{number:02d}"


def js_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def stop_requested(args: argparse.Namespace | None) -> bool:
    event = getattr(args, "stop_event", None) if args is not None else None
    if event and event.is_set():
        return True
    stop_file = os.environ.get("WANDAO_STOP_FILE", "").strip()
    return bool(stop_file and os.path.exists(stop_file))


def check_stopped(args: argparse.Namespace | None) -> None:
    if stop_requested(args):
        raise ExportStopped("用户已停止当前任务")


def wait_with_stop(args: argparse.Namespace | None, seconds: float) -> None:
    deadline = time.time() + max(0.0, seconds)
    while time.time() < deadline:
        check_stopped(args)
        time.sleep(min(1.0, deadline - time.time()))


def throttle_request(args: argparse.Namespace | None) -> None:
    if not args:
        return
    delay = max(0.0, float(getattr(args, "request_delay", 0.1) or 0))
    jitter = max(0.0, float(getattr(args, "request_jitter", 0.0) or 0))
    pause = delay + (random.uniform(0, jitter) if jitter else 0)
    if pause > 0:
        wait_with_stop(args, pause)
    args._request_count = int(getattr(args, "_request_count", 0) or 0) + 1


HEARTBEAT_BINDING_NAME = "__wandaoHeartbeat"

# Every Wandao page-side heartbeat payload starts with this marker. The console
# bridge only forwards strings carrying it, so a page's own console calls keep
# reaching whatever implementation the page installed.
HEARTBEAT_MARKER = "__WANDAO_"

# Injected into the page before a watchdog-guarded evaluation. Production web
# apps routinely replace console.log/info/warn with no-op stubs, which silently
# swallows console-based heartbeats. This forwards only Wandao heartbeat strings
# to a browser-owned binding and delegates every other call to the page's
# original console method, so page behaviour is unchanged.
HEARTBEAT_BRIDGE_JS = r"""
(() => {
  const globalObject = globalThis;
  if (!globalObject) return false;
  const binding = globalObject["__wandaoHeartbeat"];
  if (typeof binding !== "function") return false;
  const marker = "__WANDAO_";
  const consoleObject = globalObject.console;
  if (!consoleObject) return false;
  for (const name of ["info", "log", "debug", "warn", "error"]) {
    const original = consoleObject[name];
    if (typeof original !== "function" || original.__wandaoHeartbeatBridge) continue;
    const patched = function (...args) {
      try {
        const first = args[0];
        if (typeof first === "string" && first.indexOf(marker) === 0) {
          binding(first);
        }
      } catch (_) {}
      return original.apply(this, args);
    };
    patched.__wandaoHeartbeatBridge = true;
    try { consoleObject[name] = patched; } catch (_) {}
  }
  return true;
})()
"""


def console_heartbeat_watchdog(
    prefix: str,
    on_heartbeat: Callable[[dict[str, Any], int], None] | None = None,
    *,
    binding_name: str = HEARTBEAT_BINDING_NAME,
) -> Callable[[dict[str, Any]], bool]:
    """Renew a CDP evaluation when the page emits a structured heartbeat.

    Long page-side JavaScript often performs several sequential fetches and
    DOM operations inside one awaited ``Runtime.evaluate`` call.  Providers
    can emit heartbeats from that script and use this callback as an idle
    watchdog while retaining a separate hard limit.

    Two channels are recognized:

    * ``Runtime.consoleAPICalled`` for ``console.log(prefix + JSON payload)``.
    * ``Runtime.bindingCalled`` for the browser-owned binding installed by
      :meth:`CDPClient.ensure_heartbeat_bridge`.  Production web apps (Feishu
      included) replace ``console.log``/``console.info``/``console.warn`` with
      no-op stubs, which silently swallows console heartbeats and makes a
      perfectly healthy page look hung; a binding cannot be silenced by page
      code.
    """

    started = time.monotonic()

    def deliver(raw: str) -> bool:
        if not raw or not raw.startswith(prefix):
            return False
        payload: dict[str, Any] = {}
        encoded = raw[len(prefix) :].strip()
        if encoded:
            try:
                value = json.loads(encoded)
                if isinstance(value, dict):
                    payload = value
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {"message": encoded}
        payload.setdefault("elapsedSeconds", max(0, int(time.monotonic() - started)))
        if on_heartbeat:
            on_heartbeat(payload, payload["elapsedSeconds"])
        return True

    def observe(message: dict[str, Any]) -> bool:
        params = message.get("params")
        if not isinstance(params, dict):
            return False
        method = message.get("method")
        if method == "Runtime.consoleAPICalled":
            values = params.get("args")
            if not isinstance(values, list):
                return False
            raw = next(
                (
                    str(item.get("value") or "")
                    for item in values
                    if isinstance(item, dict)
                    and str(item.get("value") or "").startswith(prefix)
                ),
                "",
            )
            return deliver(raw)
        if method == "Runtime.bindingCalled":
            if not binding_name or str(params.get("name") or "") != binding_name:
                return False
            return deliver(str(params.get("payload") or ""))
        return False

    return observe


def heartbeat_as_console_event(message: dict[str, Any]) -> dict[str, Any] | None:
    """Re-shape a binding heartbeat as a ``Runtime.consoleAPICalled`` event.

    Plugins written before the binding channel existed inspect
    ``Runtime.consoleAPICalled`` directly in their own watchdog callbacks.
    Rather than requiring every plugin to learn the new channel, the client
    replays binding heartbeats in the shape those observers already understand.
    """

    if message.get("method") != "Runtime.bindingCalled":
        return None
    params = message.get("params")
    if not isinstance(params, dict):
        return None
    payload = str(params.get("payload") or "")
    if not payload.startswith(HEARTBEAT_MARKER):
        return None
    return {
        "method": "Runtime.consoleAPICalled",
        "params": {
            "type": "info",
            "args": [{"type": "string", "value": payload}],
            "executionContextId": params.get("executionContextId"),
            "timestamp": time.time() * 1000.0,
        },
    }


class CDPClient:
    """Small dependency-free Chrome DevTools Protocol WebSocket client."""

    def __init__(self, websocket_url: str) -> None:
        parsed = urllib.parse.urlparse(websocket_url)
        if parsed.scheme != "ws":
            raise ExportError(f"Only ws:// DevTools URLs are supported: {websocket_url}")
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 80
        self.path = parsed.path + (("?" + parsed.query) if parsed.query else "")
        self.sock: socket.socket | None = None
        self.next_id = 0
        self.pending_events: list[dict[str, Any]] = []
        self._heartbeat_binding_installed = False
        self._enabled_commands: dict[str, dict[str, Any]] = {}
        self._navigate_count = 0
        try:
            self._recycle_every = max(0, int(os.environ.get("WANDAO_CDP_RECYCLE_NAVIGATIONS", "10")))
        except ValueError:
            self._recycle_every = 10
        self._reconnect_attempted = False
        self._current_url = ""

    def connect(self) -> None:
        sock: socket.socket | None = None
        try:
            sock = socket.create_connection((self.host, self.port), timeout=15)
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            request = (
                f"GET {self.path} HTTP/1.1\r\n"
                f"Host: {self.host}:{self.port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            )
            sock.sendall(request.encode("ascii"))
            response = b""
            while b"\r\n\r\n" not in response:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
            if b" 101 " not in response.split(b"\r\n", 1)[0]:
                raise CDPConnectionError(f"CDP DevTools WebSocket 握手失败：{response[:200]!r}")
            self.sock = sock
        except CDPConnectionError:
            if sock:
                sock.close()
            raise
        except OSError as exc:
            if sock:
                sock.close()
            raise CDPConnectionError(f"CDP DevTools WebSocket 连接失败：{exc}") from exc

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30,
        *,
        max_timeout: float | None = None,
        watchdog: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._send_once(method, params, timeout, max_timeout=max_timeout, watchdog=watchdog)
            if method.endswith(".enable"):
                self._enabled_commands[method] = dict(params or {})
            elif method.endswith(".disable"):
                self._enabled_commands.pop(f"{method[:-len('.disable')]}.enable", None)
            return response
        except CDPConnectionError:
            if self._reconnect_attempted:
                raise
            self._reconnect_attempted = True
            try:
                self.reconnect(force_restart=self._has_registered_launch())
            except Exception:
                raise
            response = self._send_once(
                method,
                params,
                timeout,
                max_timeout=max_timeout,
                watchdog=watchdog,
            )
            if method.endswith(".enable"):
                self._enabled_commands[method] = dict(params or {})
            elif method.endswith(".disable"):
                self._enabled_commands.pop(f"{method[:-len('.disable')]}.enable", None)
            return response
        finally:
            self._reconnect_attempted = False

    def _send_once(
        self,
        method: str,
        params: dict[str, Any] | None,
        timeout: float,
        *,
        max_timeout: float | None = None,
        watchdog: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        if not self.sock:
            raise CDPConnectionError("CDP socket is not connected")
        self.next_id += 1
        msg_id = self.next_id
        payload = json.dumps({"id": msg_id, "method": method, "params": params or {}}, ensure_ascii=False)
        try:
            self._send_text(payload)
        except OSError as exc:
            raise CDPConnectionError(f"CDP {method} 发送失败：{exc}") from exc
        idle_timeout = max(0.5, float(timeout))
        started = time.monotonic()
        hard_deadline = (
            started + max(idle_timeout, float(max_timeout))
            if max_timeout is not None and float(max_timeout) > 0
            else None
        )
        deadline = started + idle_timeout
        if hard_deadline is not None:
            deadline = min(deadline, hard_deadline)
        while time.monotonic() < deadline:
            try:
                message = self._recv_checked(method, max(0.5, deadline - time.monotonic()))
            except CDPTimeoutError as exc:
                if watchdog:
                    if hard_deadline is not None and time.monotonic() >= hard_deadline:
                        raise ExportError(
                            f"CDP {method} 看门狗达到最大执行时间：{int(max_timeout or 0)} 秒"
                        ) from exc
                    raise ExportError(
                        f"CDP {method} 看门狗超时：{max(1, int(idle_timeout))} 秒内没有收到页面心跳"
                    ) from exc
                raise
            except CDPConnectionError:
                raise
            except ExportError as exc:
                if watchdog and time.monotonic() >= deadline:
                    if hard_deadline is not None and time.monotonic() >= hard_deadline:
                        raise ExportError(
                            f"CDP {method} 看门狗达到最大执行时间：{int(max_timeout or 0)} 秒"
                        ) from exc
                    raise ExportError(
                        f"CDP {method} 看门狗超时：{max(1, int(idle_timeout))} 秒内没有收到页面心跳"
                    ) from exc
                raise
            if message.get("id") == msg_id:
                if "error" in message:
                    raise ExportError(f"CDP {method} failed: {message['error']}")
                return message
            if message.get("method"):
                if watchdog and self._watchdog_renewed(watchdog, message):
                    deadline = time.monotonic() + idle_timeout
                    if hard_deadline is not None:
                        deadline = min(deadline, hard_deadline)
                self.pending_events.append(message)
        if watchdog:
            if hard_deadline is not None and time.monotonic() >= hard_deadline:
                raise ExportError(
                    f"CDP {method} 看门狗达到最大执行时间：{int(max_timeout or 0)} 秒"
                )
            raise ExportError(
                f"CDP {method} 看门狗超时：{max(1, int(idle_timeout))} 秒内没有收到页面心跳"
            )
        raise ExportError(f"Timed out waiting for CDP response: {method}")

    def wait_for_event(
        self,
        method: str,
        *,
        timeout: float = 30,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        def matches(message: dict[str, Any]) -> bool:
            return message.get("method") == method and (predicate(message) if predicate else True)

        for index, message in enumerate(self.pending_events):
            if matches(message):
                return self.pending_events.pop(index)
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = self._recv_checked(f"event {method}", max(0.5, deadline - time.time()))
            if matches(message):
                return message
            if message.get("method"):
                self.pending_events.append(message)
        raise ExportError(f"Timed out waiting for CDP event: {method}")

    def evaluate(
        self,
        expression: str,
        timeout: float = 60,
        *,
        max_timeout: float | None = None,
        watchdog: Callable[[dict[str, Any]], bool] | None = None,
    ) -> Any:
        if watchdog is not None:
            self.ensure_heartbeat_bridge()
        response = self.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
            max_timeout=max_timeout,
            watchdog=watchdog,
        )
        result = response.get("result", {})
        if result.get("exceptionDetails"):
            raise ExportError(f"Page evaluation failed: {result['exceptionDetails']}")
        return result.get("result", {}).get("value")

    def navigate(self, url: str) -> None:
        # Chrome renderer memory grows ~85-110 MB per navigation; after ~35
        # navigations it reaches ~3.5 GB and the OS kills the renderer, dropping
        # the CDP socket. Recycling the browser process every N navigations caps
        # peak memory and avoids the cascade.
        if (
            self._recycle_every
            and self._navigate_count >= self._recycle_every
            and self._has_registered_launch()
        ):
            self._restart_browser()
            self.reconnect()
            self._navigate_count = 0
        self.send("Page.navigate", {"url": url}, timeout=20)
        self._current_url = url
        self._navigate_count += 1

    def add_binding(self, name: str, *, timeout: float = 15) -> None:
        """Install ``window.<name>`` so page code can raise CDP events.

        Bindings are created by the browser rather than by the page, so unlike
        ``console.*`` they cannot be replaced with a no-op stub by page code.
        The page calls ``window.<name>(text)`` and the client receives a
        ``Runtime.bindingCalled`` event. The binding is also installed on
        execution contexts created later, so it survives page navigations.
        """

        self.send("Runtime.addBinding", {"name": name}, timeout=timeout)

    def ensure_heartbeat_bridge(self) -> bool:
        """Make page-side heartbeats observable even when console is stubbed.

        Best effort: any failure simply leaves the original console channel in
        place, so older browsers or restricted pages behave as before.
        """

        if not self._heartbeat_binding_installed:
            try:
                self.add_binding(HEARTBEAT_BINDING_NAME)
            except ExportError:
                return False
            self._heartbeat_binding_installed = True
        # The binding survives navigation, but the console shim lives in the
        # page's JavaScript context and does not; re-inject it every time. The
        # script is idempotent per context, so repeat calls are cheap.
        try:
            self.send(
                "Runtime.evaluate",
                {"expression": HEARTBEAT_BRIDGE_JS, "returnByValue": True},
                timeout=15,
            )
        except ExportError:
            return False
        return True

    def _watchdog_renewed(
        self,
        watchdog: Callable[[dict[str, Any]], bool],
        message: dict[str, Any],
    ) -> bool:
        """Ask a watchdog whether an incoming event proves the page is alive.

        A binding heartbeat is first offered in its native shape, then replayed
        as a ``Runtime.consoleAPICalled`` event so plugin-side watchdogs that
        only understand the console channel keep renewing.
        """

        try:
            if watchdog(message):
                return True
        except Exception:
            # A watchdog that only understands the console shape may raise on a
            # native binding event; fall through and offer the replayed form.
            pass
        replayed = heartbeat_as_console_event(message)
        if replayed is None:
            return False
        try:
            return bool(watchdog(replayed))
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Connection recovery and browser recycling
    # ------------------------------------------------------------------

    def reconnect(self, *, force_restart: bool = False) -> None:
        """Re-establish the CDP connection after a transport-level disconnection.

        Closes the dead socket, waits for the debug port to respond, finds an
        available page target, reconnects, and re-issues Runtime.enable /
        Page.enable plus the heartbeat binding. If the browser process itself
        is gone, restarts it using recorded or discovered launch info.
        """

        had_heartbeat_binding = self._heartbeat_binding_installed
        self.close()
        self.pending_events.clear()
        self._heartbeat_binding_installed = False

        try:
            if force_restart or not chrome_debug_available(self.port):
                if not self._has_registered_launch():
                    raise CDPConnectionError(
                        "浏览器连接已断开，但当前没有本任务启动的浏览器可安全重启。"
                    )
                self._restart_browser()
            wait_for_debug_port(self.port, timeout=20)
            target_url = self._current_url
            page_ws = self._find_page_websocket(target_url)
            if not page_ws and self._has_registered_launch():
                self._restart_browser()
                wait_for_debug_port(self.port, timeout=20)
                page_ws = self._find_page_websocket(target_url)
            if not page_ws:
                raise CDPConnectionError(
                    f"CDP 调试端口已恢复，但没有找到当前飞书页面：{target_url or '未知页面'}"
                )

            # Re-parse the websocket URL (host/port/path may differ after restart).
            parsed = urllib.parse.urlparse(page_ws)
            if parsed.scheme != "ws":
                raise CDPConnectionError(f"CDP 恢复后返回了无效页面地址：{page_ws}")
            self.host = parsed.hostname or self.host
            self.port = parsed.port or self.port
            self.path = parsed.path + (("?" + parsed.query) if parsed.query else "")
            self.next_id = 0
            self.connect()
            for method, params in self._enabled_commands.items():
                self._send_once(method, params, timeout=10)
            if had_heartbeat_binding:
                self._send_once("Runtime.addBinding", {"name": HEARTBEAT_BINDING_NAME}, timeout=10)
                self._heartbeat_binding_installed = True
                self._send_once(
                    "Runtime.evaluate",
                    {"expression": HEARTBEAT_BRIDGE_JS, "returnByValue": True},
                    timeout=10,
                )
        except CDPConnectionError:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise CDPConnectionError(f"CDP 浏览器连接恢复失败：{exc}") from exc

    def _has_registered_launch(self) -> bool:
        info = _browser_launch_info.get(self.port)
        return bool(info and info.get("pid") and info.get("exe") and info.get("profile_dir"))

    def _find_page_websocket(self, target_url: str = "") -> str | None:
        """Find the page target matching the current URL, not an arbitrary tab."""

        try:
            pages = http_json(f"http://127.0.0.1:{self.port}/json/list", timeout=5)
        except Exception:
            return None
        page_targets = [
            page for page in pages
            if page.get("type") == "page" and page.get("webSocketDebuggerUrl")
        ]
        if not target_url:
            return str(page_targets[0]["webSocketDebuggerUrl"]) if page_targets else None
        expected = urllib.parse.urlparse(target_url)
        expected_host = expected.netloc.lower()
        expected_path = expected.path.rstrip("/") or "/"
        for page in page_targets:
            actual = urllib.parse.urlparse(str(page.get("url") or ""))
            if (
                actual.netloc.lower() == expected_host
                and (actual.path.rstrip("/") or "/") == expected_path
            ):
                return str(page["webSocketDebuggerUrl"])
        same_host = [
            page for page in page_targets
            if urllib.parse.urlparse(str(page.get("url") or "")).netloc.lower() == expected_host
        ]
        return str(same_host[0]["webSocketDebuggerUrl"]) if len(same_host) == 1 else None

    def _find_launch_info(self) -> dict[str, Any] | None:
        """Locate the Chrome executable, profile directory, and PID.

        Lookup order:
        1. In-process registry populated by :func:`start_chrome`.
        2. ``Browser.getBrowserCommandLine`` (only works with --enable-automation).
        3. Directory convention: ``$WANDAO_PLUGIN_DATA_DIR/.*chrome-profile``.
        """

        # 1. In-process registry
        info = _browser_launch_info.get(self.port)
        if info and info.get("pid"):
            return info

        # 2. CDP query (best-effort; may be rejected without --enable-automation)
        try:
            result = self.send("Browser.getBrowserCommandLine", timeout=5)
            argv = result.get("result", {}).get("arguments", [])
            if argv:
                exe = argv[0]
                profile = None
                for arg in argv:
                    if arg.startswith("--user-data-dir="):
                        profile = arg[len("--user-data-dir="):]
                if exe and profile:
                    return {"exe": exe, "profile_dir": profile, "pid": None}
        except ExportError:
            pass

        # 3. Directory convention fallback
        return self._guess_launch_info()

    def _guess_launch_info(self) -> dict[str, Any] | None:
        """Find a ``*chrome-profile*`` directory under the plugin data dir."""

        data_dir = default_data_dir()
        if not data_dir.exists():
            return None
        for child in sorted(data_dir.iterdir()):
            if child.is_dir() and "chrome-profile" in child.name:
                exe = find_chrome()
                if exe:
                    return {"exe": exe, "profile_dir": str(child), "pid": None}
        return None

    def _restart_browser(self) -> None:
        """Kill the browser process tree and relaunch with the same profile."""

        info = _browser_launch_info.get(self.port)
        if not info or not info.get("pid"):
            raise CDPConnectionError(
                "当前浏览器不是由本任务启动的，无法安全地自动回收或重启。"
            )

        pid = int(info["pid"])
        registered_proc = info.get("proc")
        if registered_proc is None or registered_proc.poll() is None:
            self._kill_process_tree(pid)
        wait_for_debug_port_closed(self.port, timeout=15)

        exe = info["exe"]
        profile_dir = Path(info["profile_dir"])
        launch_url = self._current_url or str(info.get("url") or "about:blank")
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        proc = subprocess.Popen(
            [
                exe,
                f"--remote-debugging-port={self.port}",
                f"--user-data-dir={profile_dir}",
                "--enable-automation",
                "--no-first-run",
                "--disable-popup-blocking",
                launch_url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            start_new_session=os.name != "nt",
        )
        register_browser_launch(
            self.port,
            exe=exe,
            profile_dir=profile_dir,
            proc=proc,
            url=launch_url,
        )
        wait_for_debug_port(self.port, timeout=20)

    @staticmethod
    def _kill_process_tree(pid: int) -> None:
        terminate_pid_tree(pid)

    def _send_text(self, text: str) -> None:
        assert self.sock is not None
        payload = text.encode("utf-8")
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        mask = os.urandom(4)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def _recv_checked(self, label: str, timeout: float) -> dict[str, Any]:
        try:
            return self._recv_json(timeout=timeout)
        except TimeoutError as exc:
            raise CDPTimeoutError(f"CDP {label} 通信超时或中断：{exc}") from exc
        except OSError as exc:
            raise CDPConnectionError(f"CDP {label} 通信中断：{exc}") from exc

    def _recv_json(self, timeout: float) -> dict[str, Any]:
        assert self.sock is not None
        self.sock.settimeout(timeout)
        while True:
            first = self._recv_exact(2)
            opcode = first[0] & 0x0F
            masked = bool(first[1] & 0x80)
            length = first[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length)
            if masked:
                payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
            if opcode == 0x8:
                raise CDPConnectionError("DevTools WebSocket closed")
            if opcode == 0x9:
                self._send_pong(payload)
                continue
            if opcode == 0x1:
                return json.loads(payload.decode("utf-8"))

    def _send_pong(self, payload: bytes) -> None:
        assert self.sock is not None
        header = bytearray([0x8A, 0x80 | len(payload)])
        mask = os.urandom(4)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def _recv_exact(self, size: int) -> bytes:
        assert self.sock is not None
        chunks = bytearray()
        while len(chunks) < size:
            chunk = self.sock.recv(size - len(chunks))
            if not chunk:
                raise CDPConnectionError("Unexpected EOF from DevTools WebSocket")
            chunks.extend(chunk)
        return bytes(chunks)


def http_json(url: str, timeout: int = 10) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def chrome_debug_available(port: int) -> bool:
    try:
        http_json(f"http://127.0.0.1:{port}/json/version", timeout=2)
        return True
    except Exception:
        return False


def debug_port_probe(port: int) -> tuple[str, str]:
    try:
        data = http_json(f"http://127.0.0.1:{port}/json/version", timeout=2)
        if isinstance(data, dict) and (data.get("Browser") or data.get("webSocketDebuggerUrl")):
            return "available", ""
        return "occupied", "端口有响应，但不是 Chrome DevTools 调试服务。"
    except urllib.error.HTTPError as exc:
        return "occupied", f"端口有 HTTP 响应，但返回 HTTP {exc.code}。"
    except urllib.error.URLError as exc:
        return "closed", str(getattr(exc, "reason", exc))
    except TimeoutError:
        return "timeout", "连接端口超时。"
    except Exception as exc:  # noqa: BLE001
        return "unknown", str(exc)


def debug_port_error_message(port: int, status: str, detail: str = "") -> str:
    message = [
        f"无法连接浏览器调试端口 {port}。",
        detail or "浏览器没有在预期时间内开放 DevTools 调试端口。",
        "请到“设置 > 自动化浏览器”重新检测并选择 Chrome、Edge 或 Chromium。",
        "如果浏览器已经打开，请关闭后重试，避免旧进程占用调试端口。",
    ]
    if status == "occupied":
        message.insert(2, "端口可能被其他程序占用。")
    return "\n".join(message)


def find_chrome(explicit_path: str | None = None) -> str | None:
    values = [explicit_path, os.environ.get("WANDAO_BROWSER"), os.environ.get("BROWSER")]
    for value in values:
        if not value:
            continue
        expanded = str(Path(value).expanduser())
        if Path(expanded).exists():
            return expanded
        found = shutil.which(value)
        if found:
            return found
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe"),
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        str(Path.home() / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"),
        str(Path.home() / "Applications" / "Microsoft Edge.app" / "Contents" / "MacOS" / "Microsoft Edge"),
        str(Path.home() / "Applications" / "Chromium.app" / "Contents" / "MacOS" / "Chromium"),
        str(Path.home() / "Applications" / "Brave Browser.app" / "Contents" / "MacOS" / "Brave Browser"),
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/microsoft-edge",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    for name in ("chrome", "google-chrome", "chromium", "msedge", "microsoft-edge", "brave", "brave-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def start_chrome(port: int, profile_dir: Path, url: str, browser_path: str | None = None, *, breakaway: bool = False) -> subprocess.Popen[Any]:
    chrome = find_chrome(browser_path)
    if not chrome:
        raise ExportError("未找到 Chrome、Edge 或 Chromium，请先在万能导设置中选择自动化浏览器。")
    profile_dir.mkdir(parents=True, exist_ok=True)
    creation_flags = 0x01000000 if breakaway and os.name == "nt" else 0
    proc = subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--enable-automation",
            "--no-first-run",
            "--disable-popup-blocking",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
        start_new_session=os.name != "nt",
    )
    register_browser_launch(port, exe=chrome, profile_dir=profile_dir, proc=proc, url=url)
    return proc


def wait_for_debug_port(port: int, timeout: int = 30) -> None:
    deadline = time.time() + timeout
    status, detail = "unknown", ""
    while time.time() < deadline:
        status, detail = debug_port_probe(port)
        if status == "available":
            return
        time.sleep(1)
    raise ExportError(debug_port_error_message(port, status, detail))


def wait_for_debug_port_closed(port: int, timeout: int = 15) -> None:
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        status, _detail = debug_port_probe(port)
        if status == "closed":
            return
        time.sleep(0.25)
    raise CDPConnectionError(f"浏览器调试端口 {port} 在结束旧进程后仍被占用")


def terminate_pid_tree(pid: int, timeout: float = 10) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass


def terminate_process_tree(process: subprocess.Popen[Any], timeout: float = 10) -> None:
    if process.poll() is not None:
        return
    terminate_pid_tree(int(process.pid), timeout=timeout)
    try:
        process.wait(timeout=max(0.1, timeout))
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            try:
                process.kill()
            except OSError:
                pass
        else:
            try:
                os.killpg(int(process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                try:
                    process.kill()
                except OSError:
                    pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def open_tab(port: int, url: str) -> None:
    encoded = urllib.parse.quote(url, safe="")
    endpoint = f"http://127.0.0.1:{port}/json/new?{encoded}"
    try:
        urllib.request.urlopen(endpoint, timeout=5).close()
    except urllib.error.HTTPError as exc:
        if exc.code != 405:
            raise
        urllib.request.urlopen(urllib.request.Request(endpoint, method="PUT"), timeout=5).close()


def emit(
    args: argparse.Namespace | None,
    message: str,
    *,
    event: str = "log.message",
    level: str = "info",
    **fields: Any,
) -> None:
    callback = getattr(args, "log_callback", None) if args is not None else None
    if callback:
        callback(message)
    elif structured_logs_enabled():
        provider = str(
            getattr(args, "provider_id", "")
            or getattr(args, "provider", "")
            or os.environ.get("WANDAO_PROVIDER_ID")
            or os.environ.get("WANDAO_PLUGIN_ID")
            or "plugin"
        )
        WandaoLogger(provider=provider).event(event, message, level=level, **fields)
    else:
        print_text(message)
