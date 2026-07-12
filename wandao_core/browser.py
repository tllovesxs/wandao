#!/usr/bin/env python3
"""Stable browser-automation helpers shared by installable Wandao plugins."""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
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

class ExportError(RuntimeError):
    pass


class ExportStopped(ExportError):
    pass


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
    return (cleaned or fallback)[:max_len]


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

    def connect(self) -> None:
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
            raise ExportError(f"DevTools WebSocket handshake failed: {response[:200]!r}")
        self.sock = sock

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def send(self, method: str, params: dict[str, Any] | None = None, timeout: float = 30) -> dict[str, Any]:
        if not self.sock:
            raise ExportError("CDP socket is not connected")
        self.next_id += 1
        msg_id = self.next_id
        payload = json.dumps({"id": msg_id, "method": method, "params": params or {}}, ensure_ascii=False)
        self._send_text(payload)
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = self._recv_json(timeout=max(0.5, deadline - time.time()))
            if message.get("id") == msg_id:
                if "error" in message:
                    raise ExportError(f"CDP {method} failed: {message['error']}")
                return message
            if message.get("method"):
                self.pending_events.append(message)
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
            message = self._recv_json(timeout=max(0.5, deadline - time.time()))
            if matches(message):
                return message
            if message.get("method"):
                self.pending_events.append(message)
        raise ExportError(f"Timed out waiting for CDP event: {method}")

    def evaluate(self, expression: str, timeout: float = 60) -> Any:
        response = self.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        result = response.get("result", {})
        if result.get("exceptionDetails"):
            raise ExportError(f"Page evaluation failed: {result['exceptionDetails']}")
        return result.get("result", {}).get("value")

    def navigate(self, url: str) -> None:
        self.send("Page.navigate", {"url": url}, timeout=20)

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
                raise ExportError("DevTools WebSocket closed")
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
                raise ExportError("Unexpected EOF from DevTools WebSocket")
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
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/microsoft-edge",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    for name in ("chrome", "google-chrome", "chromium", "msedge", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return found
    return None


def start_chrome(port: int, profile_dir: Path, url: str, browser_path: str | None = None) -> subprocess.Popen[Any]:
    chrome = find_chrome(browser_path)
    if not chrome:
        raise ExportError("未找到 Chrome、Edge 或 Chromium，请先在万能导设置中选择自动化浏览器。")
    profile_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [chrome, f"--remote-debugging-port={port}", f"--user-data-dir={profile_dir}", "--no-first-run", "--disable-popup-blocking", url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_debug_port(port: int, timeout: int = 30) -> None:
    deadline = time.time() + timeout
    status, detail = "unknown", ""
    while time.time() < deadline:
        status, detail = debug_port_probe(port)
        if status == "available":
            return
        time.sleep(1)
    raise ExportError(debug_port_error_message(port, status, detail))


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
