#!/usr/bin/env python3
# Author: tllovesxs
"""
Standalone exporter for Aliyun Thoughts workspace documents.

It can:
  - open Chrome/Edge for manual login and save reusable auth cookies;
  - restore saved auth without Codex or browser extensions;
  - export all workspace documents to Markdown;
  - incrementally add documents that do not exist locally yet;
  - run from CLI as the Electron desktop backend.

Usage:
  # 1. First-time login. This saves cookies, not your password.
  python export_aliyun_thoughts.py --login \
    --workspace-url https://thoughts.aliyun.com/workspaces/<workspace_id>/overview \
    --auth-file .aliyun_thoughts_auth.json

  # 2. Incrementally export only missing documents.
  python export_aliyun_thoughts.py \
    --workspace-url https://thoughts.aliyun.com/workspaces/<workspace_id>/overview \
    --output "./exports/aliyun-thoughts" \
    --auth-file .aliyun_thoughts_auth.json \
    --incremental

Desktop UI:
  Use start-wandao.cmd or ./start-wandao.sh. The old Python GUI is deprecated.

The exporter controls Chrome through Chrome DevTools Protocol. It does not need
Codex. Saved auth files contain session cookies, so keep them private.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import http.cookiejar
import json
import os
import queue
import random
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from wandao_core.checkpoint import add_checkpoint_args, open_checkpoint_from_args
from wandao_cli import extend_arg_list_from_file
from wandao_core.credentials import write_private_json
from wandao_core.logging import WandaoLogger, print_text, structured_logs_enabled
from wandao_core.report import finalize_report

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR
DEFAULT_PORT = 9222
DEFAULT_PROFILE = ".aliyun-thoughts-chrome-profile"
DEFAULT_AUTH_FILE = ".aliyun_thoughts_auth.json"
FORBIDDEN_FILENAME_CHARS = r'<>:"/\|?*'
ALIYUN_EDIT_AUTH_SALT = "<8si5gtaoi$w$i$k115"


class ExportError(RuntimeError):
    pass


class ExportStopped(ExportError):
    pass


def default_data_dir() -> Path:
    data_dir = os.environ.get("WANDAO_DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser().resolve()
    return PROJECT_DIR


def default_state_path(filename: str, *, migrate_legacy_file: bool = True) -> Path:
    target = default_data_dir() / filename
    legacy = PROJECT_DIR / filename
    if os.environ.get("WANDAO_DATA_DIR") and migrate_legacy_file and not target.exists() and legacy.is_file():
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy, target)
        except OSError:
            pass
    return target


def stop_requested(args: argparse.Namespace | None) -> bool:
    event = getattr(args, "stop_event", None) if args is not None else None
    return bool(event and event.is_set())


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
    """Tiny WebSocket client for Chrome DevTools Protocol over ws://localhost."""

    def __init__(self, websocket_url: str) -> None:
        parsed = urllib.parse.urlparse(websocket_url)
        if parsed.scheme != "ws":
            raise ExportError(f"Only ws:// DevTools URLs are supported: {websocket_url}")
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 80
        self.path = parsed.path
        if parsed.query:
            self.path += "?" + parsed.query
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
            if message.get("method") != method:
                return False
            return predicate(message) if predicate else True

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
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
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
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
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
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise ExportError("DevTools WebSocket closed")
            if opcode == 0x9:
                self._send_pong(payload)
                continue
            if opcode == 0x1:
                return json.loads(payload.decode("utf-8"))

    def _send_pong(self, payload: bytes) -> None:
        assert self.sock is not None
        header = bytearray([0x8A])
        header.append(0x80 | len(payload))
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
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


@dataclass
class Node:
    id: str
    title: str
    type: str
    parent_id: str | None
    pos: float
    raw: dict[str, Any]


def select_document_nodes(nodes: list[Node], selected_doc_ids: set[str] | None = None) -> list[Node]:
    documents = [node for node in nodes if node.type == "document"]
    if not selected_doc_ids:
        return documents
    selected = [node for node in documents if node.id in selected_doc_ids]
    if documents and not selected:
        preview = ", ".join(sorted(selected_doc_ids)[:5])
        raise ExportError(
            "选择的阿里云 Thoughts 文档未匹配当前目录，"
            "请重新读取目录后再试。未匹配 ID：" + preview
        )
    return selected


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
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ConnectionRefusedError):
            return "closed", "端口没有服务监听，浏览器可能没有成功启动。"
        return "network_error", str(reason)
    except TimeoutError:
        return "timeout", "连接端口超时。"
    except Exception as exc:  # noqa: BLE001 - used only for diagnostics.
        return "unknown", str(exc)


def debug_port_error_message(port: int, status: str, detail: str = "") -> str:
    suggestions = [
        f"无法连接浏览器调试端口 {port}。",
        detail or "浏览器没有在预期时间内开放 DevTools 调试端口。",
        "处理建议：",
        "1. 到“设置 > 自动化浏览器”检测并选择 Chrome、Edge 或 Chromium。",
        f"2. 关闭已经打开的 Chrome/Edge 后重试，避免端口 {port} 被旧浏览器或其他程序占用。",
        "3. 如果仍失败，重启电脑后再试，并把错误报告发给开发者。",
    ]
    if status == "occupied":
        suggestions.insert(2, "这通常表示端口被其他程序占用，或当前浏览器不是由万能导以调试模式启动。")
    elif status == "closed":
        suggestions.insert(2, "这通常表示浏览器启动失败、浏览器路径不正确，或系统拦截了自动化启动。")
    return "\n".join(suggestions)


def find_chrome(explicit_path: str | None = None) -> str | None:
    if explicit_path:
        expanded = str(Path(explicit_path).expanduser())
        if Path(expanded).exists():
            return expanded
        found = shutil.which(explicit_path)
        if found:
            return found
    for env_name in ("WANDAO_BROWSER", "BROWSER"):
        env_value = os.environ.get(env_name)
        if env_value:
            expanded = str(Path(env_value).expanduser())
            if Path(expanded).exists():
                return expanded
            found = shutil.which(env_value)
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
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/microsoft-edge",
        "/usr/bin/microsoft-edge-stable",
        "/snap/bin/chromium",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    for name in (
        "chrome",
        "chrome.exe",
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "msedge",
        "msedge.exe",
        "microsoft-edge",
        "microsoft-edge-stable",
    ):
        found = shutil.which(name)
        if found:
            return found
    return None


def start_chrome(port: int, profile_dir: Path, url: str, browser_path: str | None = None) -> subprocess.Popen[Any]:
    chrome = find_chrome(browser_path)
    if not chrome:
        raise ExportError(
            "Chrome/Edge executable was not found. Install Chrome/Edge, add it to PATH, "
            "or set WANDAO_BROWSER to the browser executable path."
        )
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--disable-popup-blocking",
        url,
    ]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_for_debug_port(port: int, timeout: int = 30) -> None:
    deadline = time.time() + timeout
    last_status = "unknown"
    last_detail = ""
    while time.time() < deadline:
        last_status, last_detail = debug_port_probe(port)
        if last_status == "available":
            return
        time.sleep(1)
    raise ExportError(debug_port_error_message(port, last_status, last_detail))


def page_for_workspace(port: int, workspace_id: str) -> dict[str, Any] | None:
    pages = http_json(f"http://127.0.0.1:{port}/json/list", timeout=5)
    for page in pages:
        url = page.get("url", "")
        parsed = urllib.parse.urlparse(url)
        if (
            page.get("type") == "page"
            and parsed.hostname == "thoughts.aliyun.com"
            and workspace_id in parsed.path
        ):
            return page
    return None


def open_tab(port: int, url: str) -> None:
    encoded = urllib.parse.quote(url, safe="")
    endpoint = f"http://127.0.0.1:{port}/json/new?{encoded}"
    try:
        urllib.request.urlopen(endpoint, timeout=5).close()
    except urllib.error.HTTPError as exc:
        if exc.code != 405:
            raise
        request = urllib.request.Request(endpoint, method="PUT")
        urllib.request.urlopen(request, timeout=5).close()


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
            or "aliyun-thoughts"
        )
        WandaoLogger(provider=provider).event(event, message, level=level, **fields)
    else:
        print_text(message)


def compact_error(error: Any, limit: int = 600) -> str:
    text = str(error or "未知错误").replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


def write_json_report(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    report = finalize_report(data, provider="aliyun-thoughts", mode="export", report_file=path, output=data.get("output"))
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def default_auth_path() -> Path:
    return default_state_path(DEFAULT_AUTH_FILE)


def default_profile_path() -> Path:
    env_profile = os.environ.get("ALIYUN_THOUGHTS_PROFILE_DIR")
    if env_profile:
        return Path(env_profile).expanduser().resolve()
    return default_data_dir() / DEFAULT_PROFILE


def auth_path_from_args(args: argparse.Namespace) -> Path:
    return Path(args.auth_file).resolve() if args.auth_file else default_auth_path().resolve()


def prepare_cookie_for_set(cookie: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "name",
        "value",
        "url",
        "domain",
        "path",
        "secure",
        "httpOnly",
        "sameSite",
        "expires",
        "priority",
        "sameParty",
        "sourceScheme",
        "sourcePort",
        "partitionKey",
    }
    result = {key: value for key, value in cookie.items() if key in allowed and value is not None}
    if result.get("expires") in (-1, 0):
        result.pop("expires", None)
    return result


def is_aliyun_cookie(cookie: dict[str, Any]) -> bool:
    domain = (cookie.get("domain") or "").lower()
    return any(token in domain for token in ("aliyun.com", "teambition.com", "alibaba.com", "alicdn.com"))


def save_auth_state(cdp: CDPClient, auth_file: Path, workspace_url: str) -> dict[str, Any]:
    cdp.send("Network.enable")
    data = cdp.send("Network.getAllCookies", timeout=20).get("result", {})
    cookies = [cookie for cookie in data.get("cookies", []) if is_aliyun_cookie(cookie)]
    if not cookies:
        raise ExportError("No Aliyun/Teambition cookies found. Make sure login is complete in Chrome.")
    payload = {
        "version": 1,
        "workspaceUrl": workspace_url,
        "savedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "cookies": cookies,
    }
    write_private_json(auth_file, payload)
    return {"cookieCount": len(cookies), "authFile": str(auth_file)}


def load_auth_state(cdp: CDPClient, auth_file: Path) -> int:
    if not auth_file.exists():
        raise ExportError(f"阿里云 Thoughts 登录凭证不存在：{auth_file}。请先点击“登录并保存凭证”。")
    payload = json.loads(auth_file.read_text(encoding="utf-8"))
    cookies = [prepare_cookie_for_set(cookie) for cookie in payload.get("cookies", [])]
    cookies = [cookie for cookie in cookies if cookie.get("name") and cookie.get("value")]
    if not cookies:
        raise ExportError(f"阿里云 Thoughts 登录凭证里没有可用 Cookie：{auth_file}。请重新登录并保存凭证。")
    cdp.send("Network.enable")
    cdp.send("Network.setCookies", {"cookies": cookies}, timeout=30)
    return len(cookies)


def connect_workspace_browser(
    args: argparse.Namespace,
    workspace_url: str,
    workspace_id: str,
    initial_url: str | None = None,
) -> tuple[CDPClient, subprocess.Popen[Any] | None]:
    chrome_proc: subprocess.Popen[Any] | None = None
    target_url = initial_url or workspace_url
    if not chrome_debug_available(args.port):
        profile = Path(args.profile_dir).resolve() if args.profile_dir else default_profile_path()
        chrome_proc = start_chrome(args.port, profile, target_url, getattr(args, "browser_path", None))
        wait_for_debug_port(args.port, timeout=30)

    page = page_for_workspace(args.port, workspace_id)
    if not page:
        open_tab(args.port, target_url)
        time.sleep(2)
        page = page_for_workspace(args.port, workspace_id)
    if not page:
        pages = http_json(f"http://127.0.0.1:{args.port}/json/list", timeout=5)
        page = next((p for p in pages if p.get("type") == "page"), None)
    if not page:
        raise ExportError("Could not find or create a Chrome page for export.")

    cdp = CDPClient(page["webSocketDebuggerUrl"])
    cdp.connect()
    cdp.send("Runtime.enable")
    cdp.send("Page.enable")
    return cdp, chrome_proc


def extract_workspace_id(workspace_url: str) -> str:
    match = re.search(r"/workspaces/([^/?#]+)", workspace_url)
    if not match:
        raise ExportError("Could not parse workspace id from --workspace-url")
    return match.group(1)


def sanitize_filename(value: str, fallback: str = "未命名", max_len: int = 90) -> str:
    cleaned = "".join("-" if ch in FORBIDDEN_FILENAME_CHARS or ord(ch) < 32 else ch for ch in value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(". ")
    return (cleaned or fallback)[:max_len]


def pad(number: int) -> str:
    return f"{number:02d}"


def js_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def page_fetch_json(cdp: CDPClient, url: str, args: argparse.Namespace | None = None) -> Any:
    throttle_request(args)
    # CDP evaluation can temporarily run in an about:blank context while the
    # Thoughts page is still loading. Relative XHR paths are invalid there, so
    # always give the browser a complete endpoint URL.
    if url.startswith("/"):
        url = f"https://thoughts.aliyun.com{url}"
    expression = f"""
new Promise((resolve, reject) => {{
  const xhr = new XMLHttpRequest();
  xhr.open("GET", {js_string(url)}, true);
  xhr.withCredentials = true;
  xhr.setRequestHeader("Accept", "application/json, text/plain, */*");
  xhr.onload = () => {{
    if (xhr.status < 200 || xhr.status >= 300) {{
      reject(new Error("HTTP " + xhr.status + ": " + xhr.responseText.slice(0, 300)));
      return;
    }}
    try {{
      resolve(JSON.parse(xhr.responseText));
    }} catch (error) {{
      reject(error);
    }}
  }};
  xhr.onerror = () => reject(new Error("XMLHttpRequest failed"));
  xhr.ontimeout = () => reject(new Error("XMLHttpRequest timed out"));
  xhr.timeout = 60000;
  xhr.send();
}})
"""
    return cdp.evaluate(expression, timeout=60)


def fetch_children(
    cdp: CDPClient,
    workspace_id: str,
    parent_id: str | None,
    args: argparse.Namespace | None = None,
) -> list[dict[str, Any]]:
    suffix = f"&_parentId={urllib.parse.quote(parent_id)}&withDetail=false" if parent_id else ""
    data = page_fetch_json(cdp, f"/api/workspaces/{workspace_id}/nodes?pageSize=1000{suffix}", args)
    return data.get("result", []) if isinstance(data, dict) else []


def load_tree(cdp: CDPClient, workspace_id: str, args: argparse.Namespace | None = None) -> list[Node]:
    nodes: list[Node] = []
    seen: set[str] = set()

    def visit(parent_id: str | None) -> None:
        for raw in fetch_children(cdp, workspace_id, parent_id, args):
            node_id = raw["_id"]
            if node_id in seen:
                continue
            seen.add(node_id)
            node = Node(
                id=node_id,
                title=raw.get("title") or "未命名",
                type=raw.get("type") or "",
                parent_id=raw.get("_parentId"),
                pos=float(raw.get("pos") or 0),
                raw=raw,
            )
            nodes.append(node)
            if node.type == "folder" or raw.get("withChild") or raw.get("extra", {}).get("withChild"):
                visit(node.id)

    visit(None)
    return nodes


def fetch_current_user_id(cdp: CDPClient, args: argparse.Namespace | None = None) -> str:
    data = page_fetch_json(cdp, "/api/users/me?pageSize=1000", args)
    if not isinstance(data, dict) or not data.get("_id"):
        raise ExportError("Could not fetch current Aliyun Thoughts user id")
    return str(data["_id"])


def cookie_jar_from_auth_file(auth_file: Path) -> http.cookiejar.CookieJar:
    if not auth_file.exists():
        raise ExportError(f"阿里云 Thoughts 登录凭证不存在：{auth_file}。请先点击“登录并保存凭证”。")
    payload = json.loads(auth_file.read_text(encoding="utf-8"))
    jar = http.cookiejar.CookieJar()
    for item in payload.get("cookies", []):
        name = item.get("name")
        value = item.get("value")
        domain = item.get("domain")
        if not name or value is None or not domain:
            continue
        expires = item.get("expires")
        cookie = http.cookiejar.Cookie(
            version=0,
            name=str(name),
            value=str(value),
            port=None,
            port_specified=False,
            domain=str(domain),
            domain_specified=True,
            domain_initial_dot=str(domain).startswith("."),
            path=str(item.get("path") or "/"),
            path_specified=True,
            secure=bool(item.get("secure")),
            expires=None if expires in (None, -1, 0) else int(expires),
            discard=expires in (None, -1, 0),
            comment=None,
            comment_url=None,
            rest={"HttpOnly": item.get("httpOnly")},
            rfc2109=False,
        )
        jar.set_cookie(cookie)
    return jar


class AliyunThoughtsRestClient:
    """Cookie-authenticated HTTP client for stable Thoughts metadata APIs."""

    def __init__(self, auth_file: Path) -> None:
        self.jar = cookie_jar_from_auth_file(auth_file)
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def get_json(self, url: str, *, timeout: int = 60) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                return self._get_json_once(url, timeout=min(max(timeout, 10), 20))
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code == 401:
                    detail = exc.read().decode("utf-8", errors="replace")[:300]
                    raise ExportError(f"阿里云 Thoughts 登录已失效，请重新点击“登录并保存凭证”后重试。HTTP 401：{detail}") from exc
                if exc.code not in (408, 429, 500, 502, 503, 504):
                    detail = exc.read().decode("utf-8", errors="replace")[:300]
                    raise ExportError(f"Aliyun Thoughts API HTTP {exc.code}: {detail}") from exc
            except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
                last_error = exc
            if attempt < 3:
                time.sleep(0.8 * attempt)
        raise ExportError(f"Aliyun Thoughts API failed after retries: {last_error}") from last_error

    def _get_json_once(self, url: str, *, timeout: int = 20) -> Any:
        full_url = urllib.parse.urljoin("https://thoughts.aliyun.com", url)
        request = urllib.request.Request(full_url, headers=self._headers(full_url), method="GET")
        with self.opener.open(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise ExportError(f"Aliyun Thoughts API returned invalid JSON: {body[:200]}") from exc

    def _headers(self, url: str) -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": "https://thoughts.aliyun.com/",
            "Origin": "https://thoughts.aliyun.com",
        }


def fetch_children_rest(
    client: AliyunThoughtsRestClient,
    workspace_id: str,
    parent_id: str | None,
    args: argparse.Namespace | None = None,
) -> list[dict[str, Any]]:
    throttle_request(args)
    suffix = f"&_parentId={urllib.parse.quote(parent_id)}&withDetail=false" if parent_id else ""
    data = client.get_json(f"/api/workspaces/{workspace_id}/nodes?pageSize=1000{suffix}")
    return data.get("result", []) if isinstance(data, dict) else []


def load_tree_rest(
    client: AliyunThoughtsRestClient,
    workspace_id: str,
    args: argparse.Namespace | None = None,
) -> list[Node]:
    nodes: list[Node] = []
    seen: set[str] = set()

    def visit(parent_id: str | None) -> None:
        for raw in fetch_children_rest(client, workspace_id, parent_id, args):
            node_id = raw["_id"]
            if node_id in seen:
                continue
            seen.add(node_id)
            node = Node(
                id=node_id,
                title=raw.get("title") or "未命名",
                type=raw.get("type") or "",
                parent_id=raw.get("_parentId"),
                pos=float(raw.get("pos") or 0),
                raw=raw,
            )
            nodes.append(node)
            if node.type == "folder" or raw.get("withChild") or raw.get("extra", {}).get("withChild"):
                visit(node.id)

    visit(None)
    return nodes


def fetch_current_user_id_rest(client: AliyunThoughtsRestClient, args: argparse.Namespace | None = None) -> str:
    throttle_request(args)
    data = client.get_json("/api/users/me?pageSize=1000")
    if not isinstance(data, dict) or not data.get("_id"):
        raise ExportError("Could not fetch current Aliyun Thoughts user id")
    return str(data["_id"])


def parse_engineio_packets(body: str) -> list[str]:
    packets: list[str] = []
    index = 0
    while index < len(body):
        cursor = index
        while cursor < len(body) and body[cursor].isdigit():
            cursor += 1
        if cursor > index and cursor < len(body) and body[cursor] == ":":
            length = int(body[index:cursor])
            start = cursor + 1
            packets.append(body[start : start + length])
            index = start + length
        else:
            packets.append(body[index:])
            break
    return packets


def parse_socketio_events(body: str) -> list[list[Any]]:
    events: list[list[Any]] = []
    for packet in parse_engineio_packets(body):
        if not packet.startswith("42"):
            continue
        try:
            parsed = json.loads(packet[2:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            events.append(parsed)
    return events


class AliyunThoughtsEditClient:
    """Minimal Engine.IO polling client for Thoughts document snapshots."""

    def __init__(self, auth_file: Path) -> None:
        self.jar = cookie_jar_from_auth_file(auth_file)
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.request_index = 0

    def fetch_document_value(
        self,
        workspace_id: str,
        doc_id: str,
        user_id: str,
        *,
        timeout: int = 60,
    ) -> dict[str, Any]:
        timeout = max(60, int(timeout or 60))
        last_error: Exception | None = None
        for attempt in range(1, 4):
            self._clear_edit_session_cookies()
            try:
                return self._fetch_document_value_once(workspace_id, doc_id, user_id, timeout=timeout)
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code == 401:
                    raise ExportError("阿里云 Thoughts 登录已失效，请重新点击“登录并保存凭证”后重试。") from exc
                if exc.code not in (400, 408, 429, 500, 502, 503, 504):
                    raise
            except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
                last_error = exc
            except ExportError as exc:
                last_error = exc
                retryable = ("Timed out", "socket session id", "sync time")
                if not any(token in str(exc) for token in retryable):
                    raise
            if attempt < 3:
                time.sleep(0.8 * attempt)
        raise ExportError(f"Aliyun Thoughts edit API failed after retries: {last_error}")

    def _fetch_document_value_once(
        self,
        workspace_id: str,
        doc_id: str,
        user_id: str,
        *,
        timeout: int,
    ) -> dict[str, Any]:
        client_id = str(uuid.uuid1())
        base_url = "https://thoughts.aliyun.com/edit/?" + urllib.parse.urlencode(
            {
                "clientId": client_id,
                "source": "thoughts",
                "_userId": user_id,
                "_documentId": doc_id,
                "_workspaceId": workspace_id,
                "EIO": "3",
                "transport": "polling",
            }
        )
        headers = self._headers(workspace_id, doc_id)

        body = self._get(base_url, headers, timeout=timeout)
        sid = self._extract_sid(body)
        sync_time = self._poll_sync_time(base_url, headers, sid, timeout=timeout)

        auth_uuid = str(uuid.uuid4())
        signature_payload = json.dumps(
            {"time": sync_time, "uuid": auth_uuid},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        # Aliyun's edit socket protocol requires this legacy SHA-1 wire value;
        # it is not used to protect locally stored credentials or user data.
        signature = hashlib.sha1((signature_payload + ALIYUN_EDIT_AUTH_SALT).encode("utf-8")).hexdigest()  # lgtm [py/weak-cryptographic-algorithm]
        auth_seq = self._post_event(
            base_url,
            headers,
            sid,
            "auth",
            {"s": signature, "time": sync_time, "uuid": auth_uuid},
            timeout=timeout,
        )
        self._wait_process_success(base_url, headers, sid, auth_seq, "auth", timeout=timeout)

        init_seq = self._post_event(base_url, headers, sid, "init", None, include_data=False, timeout=timeout)
        result = self._wait_process_success(base_url, headers, sid, init_seq, "init", timeout=timeout)
        response = result.get("response")
        if not isinstance(response, dict):
            raise ExportError("Aliyun Thoughts edit API did not return a document value")
        return response

    def _clear_edit_session_cookies(self) -> None:
        for cookie in list(self.jar):
            if cookie.name not in {"io", "THOUGHTS_EDIT_ROUTER"}:
                continue
            try:
                self.jar.clear(cookie.domain, cookie.path, cookie.name)
            except KeyError:
                pass

    def _headers(self, workspace_id: str, doc_id: str) -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Origin": "https://thoughts.aliyun.com",
            "Referer": f"https://thoughts.aliyun.com/workspaces/{workspace_id}/docs/{doc_id}",
        }

    def _next_t(self) -> str:
        self.request_index += 1
        return f"PyApi{int(time.time() * 1000)}_{self.request_index}"

    def _socket_url(self, base_url: str, sid: str | None = None) -> str:
        url = f"{base_url}&t={urllib.parse.quote(self._next_t())}"
        if sid:
            url += f"&sid={urllib.parse.quote(sid)}"
        return url

    def _get(self, base_url: str, headers: dict[str, str], sid: str | None = None, timeout: int = 60) -> str:
        request = urllib.request.Request(self._socket_url(base_url, sid), headers=headers, method="GET")
        with self.opener.open(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")

    def _post_event(
        self,
        base_url: str,
        headers: dict[str, str],
        sid: str,
        event: str,
        data: Any,
        *,
        include_data: bool = True,
        timeout: int = 60,
    ) -> str:
        seq = str(uuid.uuid1())
        payload: dict[str, Any] = {"seq": seq}
        if include_data:
            payload["data"] = data
        packet = "42" + json.dumps([event, payload], ensure_ascii=False, separators=(",", ":"))
        body = f"{len(packet)}:{packet}".encode("utf-8")
        request_headers = dict(headers)
        request_headers["Content-Type"] = "text/plain;charset=UTF-8"
        request = urllib.request.Request(
            self._socket_url(base_url, sid),
            data=body,
            headers=request_headers,
            method="POST",
        )
        with self.opener.open(request, timeout=timeout) as response:
            response.read()
        return seq

    def _extract_sid(self, body: str) -> str:
        for packet in parse_engineio_packets(body):
            if packet.startswith("0"):
                data = json.loads(packet[1:])
                sid = data.get("sid")
                if sid:
                    return str(sid)
        raise ExportError("Aliyun Thoughts edit API did not return a socket session id")

    def _poll_sync_time(self, base_url: str, headers: dict[str, str], sid: str, timeout: int) -> int:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                body = self._get(base_url, headers, sid, timeout=min(20, max(5, int(deadline - time.time()))))
            except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
                if "timed out" in str(exc).lower() and time.time() < deadline:
                    continue
                raise
            for event in parse_socketio_events(body):
                if event and event[0] == "syncTime":
                    data = event[1].get("data", {}) if isinstance(event[1], dict) else {}
                    if data.get("time"):
                        return int(data["time"])
        raise ExportError("Aliyun Thoughts edit API did not return sync time")

    def _wait_process_success(
        self,
        base_url: str,
        headers: dict[str, str],
        sid: str,
        seq: str,
        event_name: str,
        timeout: int,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout
        last_error = ""
        while time.time() < deadline:
            try:
                body = self._get(base_url, headers, sid, timeout=min(20, max(5, int(deadline - time.time()))))
            except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
                if "timed out" in str(exc).lower() and time.time() < deadline:
                    continue
                raise
            for event in parse_socketio_events(body):
                if not event or event[0] != "processStatus" or not isinstance(event[1], dict):
                    continue
                payload = event[1]
                if payload.get("seq") != seq or payload.get("eventName") != event_name:
                    continue
                data = payload.get("data", {})
                if data.get("status") == "success":
                    return data
                last_error = json.dumps(data.get("error") or data, ensure_ascii=False)
                raise ExportError(f"Aliyun Thoughts edit API {event_name} failed: {last_error}")
        raise ExportError(f"Timed out waiting for Aliyun Thoughts edit API {event_name}: {last_error}")


def slate_text(node: dict[str, Any]) -> str:
    if node.get("object") == "text":
        return "".join(str(leaf.get("text") or "") for leaf in node.get("leaves") or [])
    return "".join(slate_text(child) for child in node.get("nodes") or [])


def slate_code_text(node: dict[str, Any]) -> str:
    """Extract code text while preserving Slate code-line boundaries."""
    if node.get("object") == "text":
        return "".join(str(leaf.get("text") or "") for leaf in node.get("leaves") or [])

    children = [child for child in node.get("nodes") or [] if isinstance(child, dict)]
    if not children:
        return ""

    parts = [slate_code_text(child) for child in children]
    if any(is_slate_line_node(child) for child in children):
        return "\n".join(part.rstrip("\r\n") for part in parts)
    return "".join(parts)


def is_slate_line_node(node: dict[str, Any]) -> bool:
    node_type = str(node.get("type") or "").lower().replace("_", "-")
    return node.get("object") == "block" or node_type in {"code-line", "line", "paragraph"}


def fenced_code_markdown(language: str, code: str) -> str:
    language = str(language or "").strip()
    fence_len = max(3, max((len(match.group(0)) for match in re.finditer(r"`+", code)), default=0) + 1)
    fence = "`" * fence_len
    code = code.rstrip("\n")
    return f"{fence}{language}\n{code}\n{fence}\n\n"


def apply_slate_marks(text: str, marks: list[dict[str, Any]]) -> str:
    if not text or not text.strip():
        return text
    mark_types = {str(mark.get("type") or "").upper() for mark in marks if isinstance(mark, dict)}
    if "CODE" in mark_types:
        escaped_text = text.replace("`", "\\`")
        text = f"`{escaped_text}`"
    if "BOLD" in mark_types:
        text = f"**{text}**"
    if "ITALIC" in mark_types or "EM" in mark_types:
        text = f"*{text}*"
    if "STRIKETHROUGH" in mark_types or "STRIKE" in mark_types:
        text = f"~~{text}~~"
    return text


def first_url(data: dict[str, Any], keys: tuple[str, ...] = ("url", "src", "downloadUrl", "originUrl", "previewUrl")) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    for value in data.values():
        if isinstance(value, dict):
            nested = first_url(value, keys)
            if nested:
                return nested
    return ""


def slate_inline_markdown(node: dict[str, Any], images: list[str]) -> str:
    obj = node.get("object")
    if obj == "text":
        parts = []
        for leaf in node.get("leaves") or []:
            if isinstance(leaf, dict):
                parts.append(apply_slate_marks(str(leaf.get("text") or ""), leaf.get("marks") or []))
        return "".join(parts)

    node_type = str(node.get("type") or "").lower()
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    if "image" in node_type:
        url = first_url(data)
        if url:
            images.append(url)
            alt = data.get("fileName") or data.get("name") or data.get("title") or ""
            return f"![{alt}]({url})"

    inner = "".join(slate_inline_markdown(child, images) for child in node.get("nodes") or [])
    if node_type in {"link", "a"}:
        url = first_url(data, ("url", "href", "link"))
        return f"[{inner or url}]({url})" if url else inner
    return inner


def slate_cell_text(node: dict[str, Any], images: list[str]) -> str:
    if node.get("object") == "text" or node.get("object") == "inline":
        return slate_inline_markdown(node, images)
    parts: list[str] = []
    for child in node.get("nodes") or []:
        child_type = str(child.get("type") or "").lower()
        if child_type in {"paragraph", "title"} or child.get("object") in {"text", "inline"}:
            parts.append(slate_inline_markdown(child, images))
        else:
            parts.append(slate_cell_text(child, images))
    return "<br>".join(part.strip() for part in parts if part.strip())


def heading_level(node_type: str) -> int | None:
    normalized = node_type.replace("_", "-")
    named = {
        "heading-one": 1,
        "heading-two": 2,
        "heading-three": 3,
        "heading-four": 4,
        "heading-five": 5,
        "heading-six": 6,
    }
    if normalized in named:
        return named[normalized]
    match = re.search(r"(?:heading|header|h)-?([1-6])", normalized)
    return int(match.group(1)) if match else None


def slate_table_markdown(node: dict[str, Any], images: list[str]) -> str:
    rows: list[list[str]] = []
    for row in node.get("nodes") or []:
        if "row" not in str(row.get("type") or "").lower():
            continue
        cells = []
        for cell in row.get("nodes") or []:
            text = slate_cell_text(cell, images).replace("|", "\\|").strip()
            cells.append(text)
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    for row in rows:
        row.extend([""] * (width - len(row)))
    header = rows[0]
    separator = ["---"] * width
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(lines) + "\n\n"


def slate_block_markdown(node: dict[str, Any], images: list[str], depth: int = 0, ordered_index: int | None = None) -> str:
    node_type = str(node.get("type") or "").lower()
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    children = [child for child in node.get("nodes") or [] if isinstance(child, dict)]

    if node_type == "title":
        return ""
    if "image" in node_type:
        url = first_url(data)
        if url:
            images.append(url)
            alt = data.get("fileName") or data.get("name") or data.get("title") or ""
            return f"![{alt}]({url})\n\n"
    if node_type in {"file", "attachment"} or "embed" in node_type:
        url = first_url(data)
        title = data.get("fileName") or data.get("name") or data.get("title") or url
        return f"[{title}]({url})\n\n" if url else ""
    if node_type == "table":
        return slate_table_markdown(node, images)
    if node_type in {"divider", "hr", "thematic-break"}:
        return "---\n\n"
    if "code" in node_type and node_type != "code":
        language = data.get("language") or data.get("lang") or ""
        return fenced_code_markdown(language, slate_code_text(node))

    level = heading_level(node_type)
    inline = "".join(slate_inline_markdown(child, images) for child in children).strip()
    if level:
        return f"{'#' * level} {inline}\n\n" if inline else ""
    if node_type in {"blockquote", "quote"}:
        text = inline or slate_text(node)
        return "\n".join(f"> {line}" for line in text.splitlines()) + "\n\n" if text else ""
    if "bulleted-list" in node_type or "unordered-list" in node_type:
        lines = []
        for child in children:
            item = slate_cell_text(child, images).replace("\n", " ").strip()
            if item:
                lines.append(f"{'  ' * depth}- {item}")
        return "\n".join(lines) + ("\n\n" if lines else "")
    if "numbered-list" in node_type or "ordered-list" in node_type:
        lines = []
        for index, child in enumerate(children, start=1):
            item = slate_cell_text(child, images).replace("\n", " ").strip()
            if item:
                lines.append(f"{'  ' * depth}{index}. {item}")
        return "\n".join(lines) + ("\n\n" if lines else "")
    if "list-item" in node_type:
        prefix = f"{ordered_index}. " if ordered_index is not None else "- "
        text = inline or slate_cell_text(node, images)
        return f"{'  ' * depth}{prefix}{text.strip()}\n" if text.strip() else ""
    if node_type in {"paragraph", ""} or node.get("object") in {"block", "inline"}:
        if inline:
            return inline + "\n\n"
        nested = "".join(slate_block_markdown(child, images, depth) for child in children)
        if nested.strip():
            return nested
        text = slate_text(node).strip()
        return text + "\n\n" if text else ""
    return "".join(slate_block_markdown(child, images, depth) for child in children)


def compact_markdown_outside_code(markdown: str) -> str:
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output: list[str] = []
    in_fence = False
    fence_marker = ""
    blank_count = 0

    for line in lines:
        stripped = line.lstrip()
        fence_match = re.match(r"(`{3,}|~{3,})", stripped)

        if in_fence:
            output.append(line)
            if fence_match and stripped.startswith(fence_marker):
                in_fence = False
            continue

        if fence_match:
            fence_marker = fence_match.group(1)
            in_fence = True
            blank_count = 0
            output.append(line)
            continue

        if line.strip():
            blank_count = 0
            output.append(line)
            continue

        blank_count += 1
        if blank_count <= 1:
            output.append(line)

    return "\n".join(output).strip()


def slate_value_to_markdown(value: dict[str, Any], fallback_title: str) -> dict[str, Any]:
    document = value.get("document") if isinstance(value.get("document"), dict) else value
    nodes = document.get("nodes") if isinstance(document, dict) else []
    if not isinstance(nodes, list):
        raise ExportError("Aliyun Thoughts edit API returned an unsupported document shape")
    images: list[str] = []
    markdown = "".join(
        slate_block_markdown(node, images)
        for node in nodes
        if isinstance(node, dict)
    )
    markdown = compact_markdown_outside_code(markdown)
    return {
        "ok": True,
        "title": fallback_title,
        "markdown": markdown + ("\n" if markdown else ""),
        "images": images,
        "source": "api",
    }


def extract_document_api(
    api_client: AliyunThoughtsEditClient,
    workspace_id: str,
    node: Node,
    user_id: str,
    timeout: int,
    args: argparse.Namespace | None = None,
) -> dict[str, Any]:
    check_stopped(args)
    throttle_request(args)
    value = api_client.fetch_document_value(workspace_id, node.id, user_id, timeout=timeout)
    return slate_value_to_markdown(value, node.title)


EXTRACTOR_JS = r"""
(() => {
  function esc(s) {
    return (s || "").replace(/\u00a0/g, " ").replace(/\n{3,}/g, "\n\n").trim();
  }
  function compact(md) {
    const lines = (md || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    const out = [];
    let inFence = false;
    let fence = "";
    let blankCount = 0;
    for (const line of lines) {
      const trimmedLeft = line.replace(/^\s+/, "");
      const match = trimmedLeft.match(/^(`{3,}|~{3,})/);
      if (inFence) {
        out.push(line);
        if (match && trimmedLeft.startsWith(fence)) inFence = false;
        continue;
      }
      if (match) {
        fence = match[1];
        inFence = true;
        blankCount = 0;
        out.push(line);
        continue;
      }
      if (line.trim()) {
        blankCount = 0;
        out.push(line);
        continue;
      }
      blankCount += 1;
      if (blankCount <= 1) out.push(line);
    }
    return out.join("\n").trim();
  }
  function inline(n) {
    if (n.nodeType === 3) return n.nodeValue.replace(/\s+/g, " ");
    if (n.nodeType !== 1) return "";
    const tag = n.tagName.toLowerCase();
    if (tag === "br") return "\n";
    if (tag === "img") {
      const src = n.getAttribute("src") || "";
      const alt = n.getAttribute("alt") || "";
      return src ? "![" + alt + "](" + src + ")" : "";
    }
    const inner = [...n.childNodes].map(inline).join("");
    if (tag === "strong" || tag === "b") return inner.trim() ? "**" + inner + "**" : inner;
    if (tag === "em" || tag === "i") return inner.trim() ? "*" + inner + "*" : inner;
    if (tag === "code") return "`" + inner.replace(/`/g, "\\`") + "`";
    if (tag === "a") {
      const href = n.href || n.getAttribute("href") || "";
      return href && inner.trim() ? "[" + inner.trim() + "](" + href + ")" : inner;
    }
    return inner;
  }
  function block(el) {
    if (!el || el.nodeType !== 1) return "";
    const tag = el.tagName.toLowerCase();
    if (/^h[1-6]$/.test(tag)) return "#".repeat(+tag[1]) + " " + esc(inline(el)) + "\n\n";
    if (tag === "p") return esc(inline(el)) + "\n\n";
    if (tag === "pre") return "```\n" + (el.innerText || "").replace(/\n$/, "") + "\n```\n\n";
    if (tag === "blockquote") return esc(el.innerText).split("\n").map(x => "> " + x).join("\n") + "\n\n";
    if (tag === "ul" || tag === "ol") {
      return [...el.children]
        .filter(x => x.tagName && x.tagName.toLowerCase() === "li")
        .map((li, i) => (tag === "ol" ? (i + 1) + ". " : "- ") + esc(inline(li)))
        .join("\n") + "\n\n";
    }
    if (tag === "table") {
      const rows = [...el.querySelectorAll("tr")]
        .map(tr => [...tr.children].map(td => esc(td.innerText).replace(/\n+/g, "<br>")))
        .filter(r => r.length);
      if (!rows.length) return "";
      const max = Math.max(...rows.map(r => r.length));
      rows.forEach(r => { while (r.length < max) r.push(""); });
      return "| " + rows[0].join(" | ") + " |\n| " + Array(max).fill("---").join(" | ") + " |\n"
        + rows.slice(1).map(r => "| " + r.join(" | ") + " |").join("\n") + "\n\n";
    }
    if (tag === "img") return inline(el) + "\n\n";
    const own = [...el.children].map(block).join("");
    if (own.trim()) return own;
    const text = esc(el.innerText);
    return text ? text + "\n\n" : "";
  }
  const editor = document.querySelector('[data-key="slate-document"], .slate-editor');
  if (!editor) {
    return { ok: false, title: document.title, markdown: document.body.innerText.slice(0, 3000), images: [] };
  }
  const markdown = compact([...editor.children].map(block).join("")) + "\n";
  const images = [...editor.querySelectorAll("img")].map(img => img.src).filter(Boolean);
  return {
    ok: true,
    title: document.title.replace(/ · 云效 Thoughts.*/, ""),
    markdown,
    images
  };
})()
"""


def wait_for_document(cdp: CDPClient, title: str, timeout: int, args: argparse.Namespace | None = None) -> None:
    deadline = time.time() + timeout
    title_sample = title.lstrip("✅").strip()[:12]
    while time.time() < deadline:
        check_stopped(args)
        ready = cdp.evaluate(
            "!!document.querySelector('[data-key=\"slate-document\"], .slate-editor')"
            f" && document.body.innerText.includes({js_string(title_sample)})",
            timeout=10,
        )
        if ready:
            return
        time.sleep(0.5)
    raise ExportError(f"Document did not finish rendering: {title}")


def extract_document(
    cdp: CDPClient,
    workspace_id: str,
    node: Node,
    render_timeout: int,
    args: argparse.Namespace | None = None,
) -> dict[str, Any]:
    check_stopped(args)
    throttle_request(args)
    cdp.navigate(f"https://thoughts.aliyun.com/workspaces/{workspace_id}/docs/{node.id}")
    wait_for_document(cdp, node.title, render_timeout, args)
    check_stopped(args)
    value = cdp.evaluate(EXTRACTOR_JS, timeout=60)
    if not isinstance(value, dict):
        raise ExportError(f"Unexpected extractor result for {node.title}")
    return value


def guess_extension(url: str, content_type: str | None) -> str:
    path = urllib.parse.urlparse(url).path.lower()
    match = re.search(r"\.([a-z0-9]{2,5})$", path)
    if match:
        ext = match.group(1)
        return "jpg" if ext == "jpeg" else ext
    content_type = content_type or ""
    if "png" in content_type:
        return "png"
    if "jpeg" in content_type or "jpg" in content_type:
        return "jpg"
    if "gif" in content_type:
        return "gif"
    if "webp" in content_type:
        return "webp"
    return "png"


def existing_image_path(url: str, dest_dir: Path) -> Path | None:
    prefix = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    if not dest_dir.exists():
        return None
    for candidate in dest_dir.glob(f"{prefix}.*"):
        if candidate.is_file():
            return candidate
    return None


def download_image(url: str, dest_dir: Path, timeout: int) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    existing = existing_image_path(url, dest_dir)
    if existing:
        return existing

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://thoughts.aliyun.com/",
    }
    last_error: BaseException | None = None
    for attempt in range(1, 4):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
                ext = guess_extension(url, response.headers.get("Content-Type"))
            break
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            last_error = exc
            if exc.code in (408, 429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(0.8 * attempt)
                continue
            hint = ""
            if exc.code in (401, 403):
                hint = "。通常是阿里云/云效图片签名过期、账号无权访问，或图片服务禁止外部下载。请重新登录后重新导出，确保图片能在浏览器中打开"
            raise ExportError(f"图片下载失败 HTTP {exc.code}{hint}：{raw[:300]}") from exc
        except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(0.8 * attempt)
                continue
            raise ExportError(f"图片下载失败，已重试 {attempt} 次：{exc}") from exc
    else:
        raise ExportError(f"图片下载失败，已重试 3 次：{last_error}") from last_error
    name = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12] + "." + ext
    target = dest_dir / name
    if not target.exists():
        target.write_bytes(data)
    return target


def localize_images(
    markdown: str,
    images: list[str],
    md_path: Path,
    download_timeout: int,
    keep_remote_images: bool,
    args: argparse.Namespace | None = None,
) -> tuple[str, int, list[dict[str, str]]]:
    failures: list[dict[str, str]] = []
    replacements: dict[str, str] = {}
    success = 0
    urls = sorted({url for url in images if url.startswith(("http://", "https://"))})
    if not urls:
        return markdown, success, failures

    dest_dir = md_path.parent / "assets"
    workers = max(1, int(getattr(args, "image_workers", 6) or 1))

    def fetch_one(url: str) -> tuple[str, Path]:
        check_stopped(args)
        return url, download_image(url, dest_dir, download_timeout)

    def record_result(url: str, target: Path) -> None:
        nonlocal success
        replacements[url] = os.path.relpath(target, md_path.parent).replace("\\", "/")
        success += 1

    if workers == 1 or len(urls) == 1:
        iterator = ((url, None) for url in urls)
        for url, _ in iterator:
            check_stopped(args)
            try:
                _, target = fetch_one(url)
                record_result(url, target)
            except ExportStopped:
                raise
            except Exception as exc:
                error = compact_error(exc, 800)
                failures.append({"url": url, "error": error})
                emit(
                    args,
                    f"图片下载失败：{url[:160]}：{compact_error(error, 320)}",
                    event="resource.download.failed",
                    level="error",
                    step="download_image",
                    resource={"type": "image", "url": url, "host": urllib.parse.urlparse(url).netloc},
                    error={"message": error},
                )
    else:
        max_workers = min(workers, len(urls))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_url = {executor.submit(fetch_one, url): url for url in urls}
            for future in concurrent.futures.as_completed(future_to_url):
                check_stopped(args)
                url = future_to_url[future]
                try:
                    _, target = future.result()
                    record_result(url, target)
                except ExportStopped:
                    raise
                except Exception as exc:
                    error = compact_error(exc, 800)
                    failures.append({"url": url, "error": error})
                    emit(
                        args,
                        f"图片下载失败：{url[:160]}：{compact_error(error, 320)}",
                        event="resource.download.failed",
                        level="error",
                        step="download_image",
                        resource={"type": "image", "url": url, "host": urllib.parse.urlparse(url).netloc},
                        error={"message": error},
                    )

    for url, rel_path in replacements.items():
        markdown = markdown.replace(url, rel_path)
    if not keep_remote_images:
        for item in failures:
            markdown = markdown.replace(item["url"], "")
    return markdown, success, failures


def build_paths(nodes: list[Node], output: Path) -> tuple[dict[str, Path], dict[str, Path], dict[str, list[Node]], list[Node]]:
    children: dict[str, list[Node]] = {}
    for node in nodes:
        children.setdefault(node.parent_id or "ROOT", []).append(node)
    for bucket in children.values():
        bucket.sort(key=lambda n: (n.pos, n.title))

    folder_paths: dict[str, Path] = {}
    doc_paths: dict[str, Path] = {}
    node_index: dict[str, int] = {}
    for siblings in children.values():
        for index, node in enumerate(siblings, start=1):
            node_index[node.id] = index

    def container_for(parent_id: str | None) -> Path:
        if not parent_id:
            return output
        parent = next((node for node in nodes if node.id == parent_id), None)
        if not parent:
            return output
        if parent.type == "folder":
            return folder_paths[parent.id]
        # A document can have child documents. The file itself cannot be a
        # directory, so child documents are stored in a same-prefix companion
        # folder and linked from the parent document.
        parent_container = container_for(parent.parent_id)
        return folder_paths.setdefault(
            parent.id,
            parent_container / f"{pad(node_index.get(parent.id, 1))}-{sanitize_filename(parent.title)}",
        )

    def visit(parent_id: str | None) -> None:
        parent_container = container_for(parent_id)
        for node in children.get(parent_id or "ROOT", []):
            index = node_index.get(node.id, 1)
            safe_title = sanitize_filename(node.title)
            if node.type == "folder":
                folder_paths[node.id] = parent_container / f"{pad(index)}-{safe_title}"
                visit(node.id)
            elif node.type == "document":
                doc_paths[node.id] = parent_container / f"{pad(index)}-{safe_title}.md"
                if children.get(node.id):
                    folder_paths[node.id] = parent_container / f"{pad(index)}-{safe_title}"
                    visit(node.id)

    visit(None)
    top_items = children.get("ROOT", [])
    return folder_paths, doc_paths, children, top_items


def document_target_path(
    doc: Node,
    index: int,
    by_id: dict[str, Node],
    children: dict[str, list[Node]],
    folder_paths: dict[str, Path],
    planned_doc_paths: dict[str, Path],
    output: Path,
) -> Path:
    return planned_doc_paths.get(doc.id) or output / f"{pad(index)}-{sanitize_filename(doc.title)}.md"


def ensure_document_parent_dirs(docs: list[Node], planned_doc_paths: dict[str, Path], output: Path) -> None:
    for index, doc in enumerate(docs, start=1):
        target = planned_doc_paths.get(doc.id) or output / f"{pad(index)}-{sanitize_filename(doc.title)}.md"
        target.parent.mkdir(parents=True, exist_ok=True)


def rewrite_internal_links(markdown: str, md_path: Path, doc_paths: dict[str, Path]) -> str:
    pattern = re.compile(r"https://thoughts\.aliyun\.com/workspaces/([^/\s)]+)/docs/([0-9a-fA-F]+)")

    def replace(match: re.Match[str]) -> str:
        target = doc_paths.get(match.group(2))
        if not target:
            return match.group(0)
        return os.path.relpath(target, md_path.parent).replace("\\", "/")

    return pattern.sub(replace, markdown)


def append_child_doc_links(markdown: str, doc: Node, children: dict[str, list[Node]], doc_paths: dict[str, Path], md_path: Path) -> str:
    child_docs = [node for node in children.get(doc.id, []) if node.type == "document" and node.id in doc_paths]
    if not child_docs:
        return markdown
    lines = ["", "## 子文档", ""]
    for child in child_docs:
        rel_path = os.path.relpath(doc_paths[child.id], md_path.parent).replace("\\", "/")
        lines.append(f"- [{child.title}]({rel_path})")
    return markdown.rstrip() + "\n\n" + "\n".join(lines) + "\n"


def scan_exported_docs(output: Path) -> dict[str, Path]:
    exported: dict[str, Path] = {}
    if not output.exists():
        return exported
    pattern = re.compile(r"https://thoughts\.aliyun\.com/workspaces/[^/\s]+/docs/([0-9a-fA-F]+)")
    for md_file in output.rglob("*.md"):
        try:
            text = md_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for match in pattern.finditer(text):
            exported[match.group(1)] = md_file
    return exported


def write_index(
    output: Path,
    root_items: list[Node],
    children: dict[str, list[Node]],
    folder_paths: dict[str, Path],
    doc_paths: dict[str, Path] | None = None,
    selected_doc_ids: set[str] | None = None,
) -> None:
    index_path = output / "00-知识库入口.md"
    lines = ["# 阿里云 Thoughts 教学文档", "", "> 从阿里云 Thoughts 知识库导出。", ""]

    def has_selected_docs(node: Node) -> bool:
        if node.type == "document" and (not selected_doc_ids or node.id in selected_doc_ids):
            return True
        return any(has_selected_docs(child) for child in children.get(node.id, []))

    def walk(items: list[Node], depth: int = 0) -> None:
        for node in items:
            if not has_selected_docs(node):
                continue
            indent = "  " * depth
            if node.type == "document":
                doc_path = (doc_paths or {}).get(node.id)
                if not doc_path:
                    continue
                rel_path = os.path.relpath(doc_path, index_path.parent).replace("\\", "/")
                lines.append(f"{indent}- [{node.title}]({rel_path})")
                walk(children.get(node.id, []), depth + 1)
            else:
                lines.append(f"{indent}- **{node.title}**")
                walk(children.get(node.id, []), depth + 1)

    walk(root_items)
    index_path.write_text("\n".join(lines), encoding="utf-8")


def node_to_dict(node: Node) -> dict[str, Any]:
    return {
        "id": node.id,
        "title": node.title,
        "type": node.type,
        "parent_id": node.parent_id,
        "pos": node.pos,
    }


def scan_workspace_tree(args: argparse.Namespace) -> dict[str, Any]:
    workspace_url = args.workspace_url
    workspace_id = args.workspace_id or extract_workspace_id(workspace_url)
    auth_file = auth_path_from_args(args)
    if auth_file.exists() and not args.skip_auth_load:
        try:
            emit(args, "开始通过接口读取阿里云 Thoughts 目录。")
            nodes = load_tree_rest(AliyunThoughtsRestClient(auth_file), workspace_id, args)
            return {
                "workspaceId": workspace_id,
                "workspaceUrl": workspace_url,
                "nodes": [node_to_dict(node) for node in nodes],
                "totalDocs": sum(1 for node in nodes if node.type == "document"),
            }
        except Exception as exc:
            emit(args, f"接口读取目录失败，回退浏览器读取：{exc}")

    cdp, chrome_proc = connect_workspace_browser(args, workspace_url, workspace_id, workspace_url)
    try:
        if auth_file.exists() and not args.skip_auth_load:
            cookie_count = load_auth_state(cdp, auth_file)
            emit(args, f"Loaded {cookie_count} auth cookies from {auth_file}")
            cdp.navigate(workspace_url)
            time.sleep(2)
        emit(args, "开始读取阿里云 Thoughts 目录。")
        nodes = load_tree(cdp, workspace_id, args)
        return {
            "workspaceId": workspace_id,
            "workspaceUrl": workspace_url,
            "nodes": [node_to_dict(node) for node in nodes],
            "totalDocs": sum(1 for node in nodes if node.type == "document"),
        }
    finally:
        cdp.close()
        if chrome_proc and args.close_started_chrome:
            chrome_proc.terminate()


def export_workspace(args: argparse.Namespace) -> dict[str, Any]:
    workspace_url = args.workspace_url
    workspace_id = args.workspace_id or extract_workspace_id(workspace_url)
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = open_checkpoint_from_args(args, "aliyun-thoughts", "export")

    auth_file = auth_path_from_args(args)
    cdp: CDPClient | None = None
    chrome_proc: subprocess.Popen[Any] | None = None
    rest_client: AliyunThoughtsRestClient | None = None

    def ensure_cdp() -> CDPClient:
        nonlocal cdp, chrome_proc
        if cdp is not None:
            return cdp
        cdp, chrome_proc = connect_workspace_browser(args, workspace_url, workspace_id, workspace_url)
        if auth_file.exists() and not args.skip_auth_load:
            cookie_count = load_auth_state(cdp, auth_file)
            emit(args, f"Loaded {cookie_count} auth cookies from {auth_file}")
            cdp.navigate(workspace_url)
            time.sleep(2)
        emit(args, "Chrome page is ready. If login is required, finish login in Chrome.")
        if args.wait_login:
            input("Press Enter after the workspace page is logged in and visible...")
        return cdp

    try:
        nodes: list[Node] = []
        if auth_file.exists() and not args.skip_auth_load:
            try:
                rest_client = AliyunThoughtsRestClient(auth_file)
                emit(args, "开始通过接口读取阿里云 Thoughts 目录。")
                nodes = load_tree_rest(rest_client, workspace_id, args)
            except Exception as exc:
                emit(args, f"接口读取目录失败，回退浏览器读取：{exc}")

        if not nodes:
            nodes = load_tree(ensure_cdp(), workspace_id, args)
        elif args.wait_login:
            ensure_cdp()

        by_id = {node.id: node for node in nodes}
        folder_paths, planned_doc_paths, children, root_items = build_paths(nodes, output)

        api_client: AliyunThoughtsEditClient | None = None
        api_user_id = ""
        if getattr(args, "api_export", True):
            try:
                if auth_file.exists() and not args.skip_auth_load:
                    if rest_client is None:
                        rest_client = AliyunThoughtsRestClient(auth_file)
                    try:
                        api_user_id = fetch_current_user_id_rest(rest_client, args)
                    except Exception as exc:
                        emit(args, f"接口读取用户信息失败，回退浏览器读取：{exc}")
                        api_user_id = fetch_current_user_id(ensure_cdp(), args)
                    api_client = AliyunThoughtsEditClient(auth_file)
                    emit(args, "已启用阿里云 Thoughts 接口导出，文档正文将优先通过 edit 接口获取。")
                else:
                    emit(args, "未找到可复用凭证文件，正文导出将使用浏览器渲染方式。")
            except Exception as exc:
                emit(args, f"接口导出初始化失败，将回退浏览器渲染方式：{exc}")

        selected_doc_ids = set(getattr(args, "selected_doc_ids", None) or [])
        docs = select_document_nodes(nodes, selected_doc_ids)
        ensure_document_parent_dirs(docs, planned_doc_paths, output)
        if checkpoint:
            checkpoint.start_task(
                {
                    "source": workspace_url,
                    "outputDir": str(output),
                    "workspaceId": workspace_id,
                    "totalDocs": len(docs),
                    "resume": bool(getattr(args, "resume", False)),
                    "retryFailed": bool(getattr(args, "retry_failed", False)),
                }
            )
            for doc in docs:
                checkpoint.upsert_item(
                    f"aliyun:doc:{doc.id}",
                    title=doc.title,
                    source_url=f"https://thoughts.aliyun.com/workspaces/{workspace_id}/docs/{doc.id}",
                    source_id=doc.id,
                    parent_key=doc.parent_id,
                    metadata={"id": doc.id, "title": doc.title, "type": doc.type, "parentId": doc.parent_id},
                )
            if getattr(args, "retry_failed", False):
                docs = [doc for doc in docs if checkpoint.item_status(f"aliyun:doc:{doc.id}") == "failed"]
        emit(
            args,
            f"开始导出阿里云 Thoughts 文档：共 {len(docs)} 篇。",
            event="task.started",
            provider="aliyun-thoughts",
            totals={"documents": len(docs), "nodes": len(nodes)},
            output=str(output),
        )
        existing_docs = scan_exported_docs(output)
        doc_paths: dict[str, Path] = {}
        failures: list[dict[str, str]] = []
        image_failures: list[dict[str, Any]] = []
        image_success = 0
        api_exported = 0
        dom_fallback = 0
        exported = 0
        skipped = 0
        stopped = False
        processed = 0
        report_path = output / "00-导出报告.json"

        def build_report(*, completed: bool = False) -> dict[str, Any]:
            return {
                "workspaceId": workspace_id,
                "workspaceUrl": workspace_url,
                "output": str(output),
                "totalNodes": len(nodes),
                "totalDocs": len(docs),
                "selectedDocs": len(docs),
                "processedDocs": processed,
                "exportedDocs": exported,
                "skippedDocs": skipped,
                "stopped": stopped,
                "completed": completed,
                "apiExportedDocs": api_exported,
                "domFallbackDocs": dom_fallback,
                "imageSuccess": image_success,
                "imageFailureCount": sum(len(item["failures"]) for item in image_failures),
                "imageWorkers": int(getattr(args, "image_workers", 6) or 1),
                "requestCount": int(getattr(args, "_request_count", 0) or 0),
                "requestDelaySeconds": float(getattr(args, "request_delay", 0.1) or 0),
                "requestJitterSeconds": float(getattr(args, "request_jitter", 0.0) or 0),
                "failures": failures,
                "imageFailures": image_failures,
                "exportedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "checkpoint": checkpoint.stats() if checkpoint else {},
            }

        def save_partial_report() -> None:
            try:
                write_json_report(report_path, build_report(completed=False))
            except Exception as exc:
                emit(args, f"写入阿里云 Thoughts 导出报告失败：{compact_error(exc, 240)}")

        for index, doc in enumerate(docs, start=1):
            processed = index
            if stop_requested(args):
                stopped = True
                emit(args, "收到停止请求，正在结束并写入已完成的导出结果。")
                break

            md_path = document_target_path(doc, index, by_id, children, folder_paths, planned_doc_paths, output)
            md_path.parent.mkdir(parents=True, exist_ok=True)
            doc_paths[doc.id] = existing_docs.get(doc.id, md_path)
            item_key = f"aliyun:doc:{doc.id}"

            if checkpoint and getattr(args, "resume", False) and not args.update_existing and checkpoint.item_status(item_key) == "completed":
                skipped += 1
                continue

            if args.incremental and doc.id in existing_docs and not args.update_existing:
                if checkpoint:
                    checkpoint.complete_item(item_key, local_path=str(existing_docs[doc.id]), metadata={"docId": doc.id, "skippedExisting": True})
                skipped += 1
                continue

            try:
                if checkpoint:
                    checkpoint.start_item(item_key, "content")
                emit(
                    args,
                    f"开始导出文档：{doc.title}",
                    event="document.export.started",
                    doc={"id": doc.id, "title": doc.title, "index": index, "path": str(md_path)},
                )
                if api_client and api_user_id:
                    try:
                        result = extract_document_api(api_client, workspace_id, doc, api_user_id, args.render_timeout, args)
                        if not (result.get("markdown") or "").strip():
                            raise ExportError("Aliyun Thoughts edit API returned empty markdown")
                        api_exported += 1
                    except Exception as api_exc:
                        dom_fallback += 1
                        emit(args, f"接口导出失败，回退浏览器渲染：{doc.title}：{api_exc}")
                        result = extract_document(ensure_cdp(), workspace_id, doc, args.render_timeout, args)
                else:
                    result = extract_document(ensure_cdp(), workspace_id, doc, args.render_timeout, args)
                markdown = result.get("markdown") or ""
                if not markdown.startswith("#"):
                    markdown = f"# {doc.title}\n\n{markdown}"
                markdown = re.sub(r"^#\s+[^\n]+", f"# {doc.title}", markdown, count=1)
                markdown = rewrite_internal_links(markdown, md_path, {**planned_doc_paths, **doc_paths})
                markdown = append_child_doc_links(markdown, doc, children, {**planned_doc_paths, **doc_paths}, md_path)
                markdown += f"\n---\n\n来源: https://thoughts.aliyun.com/workspaces/{workspace_id}/docs/{doc.id}\n"
                markdown, count, img_errors = localize_images(
                    markdown,
                    result.get("images") or [],
                    md_path,
                    args.download_timeout,
                    args.keep_remote_images,
                    args,
                )
                image_success += count
                if img_errors:
                    image_failures.append({"document": doc.title, "path": str(md_path), "failures": img_errors})
                md_path.write_text(markdown, encoding="utf-8")
                doc_paths[doc.id] = md_path
                if checkpoint:
                    if img_errors:
                        checkpoint.fail_item(item_key, f"{len(img_errors)} 个图片或附件下载失败")
                    else:
                        checkpoint.complete_item(item_key, local_path=str(md_path), metadata={"docId": doc.id, "title": doc.title})
                exported += 1
                emit(
                    args,
                    f"文档导出完成：{doc.title}",
                    event="document.export.completed",
                    doc={"id": doc.id, "title": doc.title, "index": index, "path": str(md_path)},
                    stats={"imageSuccessInDoc": count, "imageFailuresInDoc": len(img_errors)},
                )
            except ExportStopped:
                if checkpoint:
                    checkpoint.fail_item(item_key, "stopped")
                stopped = True
                emit(args, "收到停止请求，当前文档未写入，正在结束。")
                break
            except Exception as exc:
                if checkpoint:
                    checkpoint.fail_item(item_key, str(exc))
                failures.append({"id": doc.id, "title": doc.title, "error": str(exc)})
                emit(
                    args,
                    f"文档导出失败：{doc.title}：{compact_error(exc, 360)}",
                    event="document.export.failed",
                    level="error",
                    doc={"id": doc.id, "title": doc.title, "index": index, "path": str(md_path)},
                    error={"message": str(exc), "type": type(exc).__name__},
                )

            save_partial_report()
            if index % args.progress_every == 0 or index == len(docs):
                emit(
                    args,
                    f"progress {index}/{len(docs)} exported={exported} skipped={skipped} "
                    f"image_success={image_success} failures={len(failures)}",
                    event="task.progress",
                    progress={"current": index, "total": len(docs)},
                    stats={
                        "exportedDocs": exported,
                        "skippedDocs": skipped,
                        "imageSuccess": image_success,
                        "failureCount": len(failures),
                        "imageFailureCount": sum(len(item["failures"]) for item in image_failures),
                    },
                )

        write_index(output, root_items, children, folder_paths, doc_paths, selected_doc_ids or None)
        report = build_report(completed=not stopped and processed >= len(docs))
        write_json_report(report_path, report)
        if checkpoint:
            if stopped:
                checkpoint.fail_task("stopped", status="stopped")
            elif failures or image_failures:
                checkpoint.fail_task(
                    f"{len(failures)} 个文档失败，{sum(len(item['failures']) for item in image_failures)} 个资源失败",
                    status="failed",
                )
            else:
                checkpoint.complete_task(report)
        emit(
            args,
            "阿里云 Thoughts 导出完成" if report.get("completed") else "阿里云 Thoughts 导出已停止",
            event="task.completed" if report.get("completed") else "task.stopped",
            level="success" if report.get("completed") else "warn",
            reportFile=str(report_path),
            stats={
                "exportedDocs": exported,
                "skippedDocs": skipped,
                "failureCount": len(failures),
                "imageSuccess": image_success,
                "imageFailureCount": report.get("imageFailureCount", 0),
            },
        )
        return report
    finally:
        if cdp is not None:
            cdp.close()
        if chrome_proc and args.close_started_chrome:
            chrome_proc.terminate()
        if checkpoint:
            checkpoint.close()


def login_and_save_auth(args: argparse.Namespace, wait_callback: Callable[[], None] | None = None) -> dict[str, Any]:
    workspace_url = args.workspace_url
    workspace_id = args.workspace_id or extract_workspace_id(workspace_url)
    auth_file = auth_path_from_args(args)
    cdp, chrome_proc = connect_workspace_browser(args, workspace_url, workspace_id, workspace_url)
    try:
        emit(args, "Chrome opened. Log in to Aliyun Thoughts in the browser.")
        if wait_callback:
            wait_callback()
        elif float(getattr(args, "login_wait_seconds", 0) or 0) > 0:
            wait_seconds = float(getattr(args, "login_wait_seconds", 0) or 0)
            deadline = time.time() + wait_seconds
            emit(args, f"请在浏览器中完成登录，工具将在 {int(wait_seconds)} 秒后自动保存凭证。")
            while time.time() < deadline:
                check_stopped(args)
                time.sleep(1)
        else:
            input("After login is complete and the workspace page is visible, press Enter...")
        check_stopped(args)
        cdp.navigate(workspace_url)
        time.sleep(2)
        check_stopped(args)
        result = save_auth_state(cdp, auth_file, workspace_url)
        emit(args, f"Saved {result['cookieCount']} auth cookies to {auth_file}")
        return result
    finally:
        cdp.close()
        if chrome_proc and args.close_started_chrome:
            chrome_proc.terminate()


def run_gui() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
    from gui_utils import create_scrollable_body

    root = tk.Tk()
    root.title("阿里云 Thoughts 文档导出工具")
    root.geometry("980x780")
    body = create_scrollable_body(root)

    default_workspace = ""
    workspace_var = tk.StringVar(value=default_workspace)
    output_var = tk.StringVar(value=str((PROJECT_DIR / "exports" / "aliyun-thoughts").resolve()))
    auth_var = tk.StringVar(value=str(default_auth_path()))
    profile_var = tk.StringVar(value=str(default_profile_path()))
    browser_path_var = tk.StringVar(value="")
    port_var = tk.StringVar(value=str(DEFAULT_PORT))
    request_delay_var = tk.StringVar(value="0.8")
    request_jitter_var = tk.StringVar(value="0.4")
    close_chrome_var = tk.BooleanVar(value=False)
    log_queue: queue.Queue[str] = queue.Queue()
    buttons: list[tk.Widget] = []
    current_stop_event: dict[str, threading.Event | None] = {"event": None}
    toc_status_var = tk.StringVar(value="目录：未读取")
    toc_state: dict[str, Any] = {"nodes": [], "selected": set()}

    def log(message: str) -> None:
        log_queue.put(message)

    def poll_log() -> None:
        while True:
            try:
                message = log_queue.get_nowait()
            except queue.Empty:
                break
            log_text.configure(state="normal")
            log_text.insert("end", message + "\n")
            log_text.see("end")
            log_text.configure(state="disabled")
        root.after(150, poll_log)

    def browse_output() -> None:
        selected = filedialog.askdirectory(initialdir=output_var.get() or str(Path.cwd()))
        if selected:
            output_var.set(selected)

    def browse_auth() -> None:
        selected = filedialog.asksaveasfilename(
            initialfile=Path(auth_var.get()).name or DEFAULT_AUTH_FILE,
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if selected:
            auth_var.set(selected)

    def browse_profile() -> None:
        selected = filedialog.askdirectory(initialdir=profile_var.get() or str(Path.cwd()))
        if selected:
            profile_var.set(selected)

    def browse_browser() -> None:
        selected = filedialog.askopenfilename(
            title="选择 Chrome / Edge / Chromium 浏览器程序",
            filetypes=[("Browser executable", "*.exe"), ("All files", "*.*")],
        )
        if selected:
            browser_path_var.set(selected)

    def detect_browser() -> None:
        found = find_chrome(browser_path_var.get().strip() or None)
        if found:
            browser_path_var.set(found)
            messagebox.showinfo("已找到浏览器", f"浏览器程序：\n{found}")
            return
        messagebox.showwarning(
            "未找到浏览器",
            "没有自动找到 Chrome、Edge 或 Chromium。\n\n请点击“选择”手动指定浏览器程序，或者先下载安装 Chrome/Edge。",
        )

    def open_output() -> None:
        path = Path(output_var.get())
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])

    def build_args(
        *,
        incremental: bool,
        update_existing: bool,
        wait_login: bool = False,
        selected_doc_ids: list[str] | None = None,
    ) -> argparse.Namespace:
        workspace_url = workspace_var.get().strip()
        output = output_var.get().strip()
        auth_file = auth_var.get().strip()
        profile_dir = profile_var.get().strip()
        if not workspace_url:
            raise ExportError("请填写阿里云 Thoughts workspace URL")
        if not output:
            raise ExportError("请填写输出目录")
        return argparse.Namespace(
            workspace_url=workspace_url,
            workspace_id=None,
            output=output,
            port=int(port_var.get().strip() or DEFAULT_PORT),
            profile_dir=profile_dir or None,
            browser_path=browser_path_var.get().strip() or None,
            wait_login=wait_login,
            render_timeout=20,
            download_timeout=30,
            progress_every=20,
            request_delay=max(0.0, float(request_delay_var.get().strip() or "0.8")),
            request_jitter=max(0.0, float(request_jitter_var.get().strip() or "0.4")),
            keep_remote_images=True,
            api_export=True,
            close_started_chrome=close_chrome_var.get(),
            auth_file=auth_file or str(default_auth_path()),
            skip_auth_load=False,
            incremental=incremental,
            update_existing=update_existing,
            selected_doc_ids=selected_doc_ids,
            stop_event=None,
            log_callback=log,
        )

    def wait_for_login_dialog() -> None:
        event = threading.Event()

        def ask() -> None:
            messagebox.showinfo(
                "完成登录后继续",
                "浏览器已经打开。\n\n请在浏览器里完成阿里云登录，并确认知识库页面已经能正常打开。\n完成后回到这里点击“确定”，工具会保存登录凭证。",
            )
            event.set()

        root.after(0, ask)
        event.wait()

    def stop_current_task() -> None:
        event = current_stop_event.get("event")
        if event and not event.is_set():
            event.set()
            log("已发送停止请求，工具会在当前安全点停止。")

    def set_running_state(running: bool) -> None:
        for button in buttons:
            button.configure(state="disabled" if running else "normal")
        stop_button.configure(state="normal" if running else "disabled")

    def all_doc_ids() -> list[str]:
        return [str(item.get("id")) for item in toc_state.get("nodes") or [] if item.get("type") == "document"]

    def refresh_toc_status() -> None:
        keys = all_doc_ids()
        selected = toc_state.get("selected") or set()
        if not keys:
            toc_status_var.set("目录：未读取")
        else:
            toc_status_var.set(f"目录：共 {len(keys)} 篇，已选择 {len(selected)} 篇")

    def render_toc_tree() -> None:
        toc_tree.delete(*toc_tree.get_children(""))
        nodes = toc_state.get("nodes") or []
        selected: set[str] = toc_state.get("selected") or set()
        children: dict[str, list[dict[str, Any]]] = {}
        for item in nodes:
            children.setdefault(str(item.get("parent_id") or "ROOT"), []).append(item)
        for bucket in children.values():
            bucket.sort(key=lambda item: (float(item.get("pos") or 0), item.get("title") or ""))

        def doc_descendants(node_id: str) -> list[str]:
            result: list[str] = []
            for child in children.get(node_id, []):
                if child.get("type") == "document":
                    result.append(str(child.get("id")))
                else:
                    result.extend(doc_descendants(str(child.get("id"))))
            return result

        def add_node(parent_iid: str, item: dict[str, Any]) -> None:
            node_id = str(item.get("id"))
            title = item.get("title") or "未命名"
            if item.get("type") == "document":
                mark = "☑" if node_id in selected else "☐"
                toc_tree.insert(parent_iid, "end", iid=node_id, text=f"{mark} {title}")
            else:
                docs = doc_descendants(node_id)
                selected_count = sum(1 for key in docs if key in selected)
                mark = "☑" if docs and selected_count == len(docs) else ("◩" if selected_count else "☐")
                toc_tree.insert(parent_iid, "end", iid=node_id, text=f"{mark} {title}  ({selected_count}/{len(docs)})", open=True)
                for child in children.get(node_id, []):
                    add_node(node_id, child)

        for item in children.get("ROOT", []):
            add_node("", item)
        refresh_toc_status()

    def set_all_toc_selected(selected: bool) -> None:
        keys = all_doc_ids()
        if not keys:
            messagebox.showinfo("还没有目录", "请先点击“读取目录”。")
            return
        toc_state["selected"] = set(keys) if selected else set()
        render_toc_tree()

    def invert_toc_selected() -> None:
        keys = set(all_doc_ids())
        if not keys:
            messagebox.showinfo("还没有目录", "请先点击“读取目录”。")
            return
        toc_state["selected"] = keys - set(toc_state.get("selected") or set())
        render_toc_tree()

    def selected_doc_ids_for_export() -> list[str] | None:
        if not toc_state.get("nodes"):
            return None
        selected = sorted(toc_state.get("selected") or set())
        if not selected:
            raise ExportError("目录已读取，但没有选择任何文档。")
        return selected

    def toggle_toc_selection(event: Any | None = None) -> str:
        node = toc_tree.focus()
        if not node:
            return "break"
        nodes = toc_state.get("nodes") or []
        by_id = {str(item.get("id")): item for item in nodes}
        children: dict[str, list[dict[str, Any]]] = {}
        for item in nodes:
            children.setdefault(str(item.get("parent_id") or "ROOT"), []).append(item)
        selected: set[str] = toc_state.get("selected") or set()

        def docs_under(node_id: str) -> list[str]:
            item = by_id.get(node_id)
            if item and item.get("type") == "document":
                return [node_id]
            result: list[str] = []
            for child in children.get(node_id, []):
                result.extend(docs_under(str(child.get("id"))))
            return result

        docs = docs_under(node)
        if docs and all(key in selected for key in docs):
            selected.difference_update(docs)
        else:
            selected.update(docs)
        toc_state["selected"] = selected
        render_toc_tree()
        if toc_tree.exists(node):
            toc_tree.focus(node)
        return "break"

    def run_worker(
        name: str,
        args: argparse.Namespace,
        fn: Callable[[], dict[str, Any]],
        on_success: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        def worker() -> None:
            stop_event = threading.Event()
            args.stop_event = stop_event
            current_stop_event["event"] = stop_event
            root.after(0, lambda: set_running_state(True))
            log(f"开始：{name}")
            try:
                result = fn()
                summary = {
                    k: result.get(k)
                    for k in (
                        "totalDocs",
                        "exportedDocs",
                        "skippedDocs",
                        "apiExportedDocs",
                        "domFallbackDocs",
                        "requestCount",
                        "stopped",
                        "imageSuccess",
                        "imageFailureCount",
                    )
                    if k in result
                }
                log(f"{'已停止' if result.get('stopped') else '完成'}：{name}")
                if summary:
                    log(json.dumps(summary, ensure_ascii=False, indent=2))
                if on_success:
                    root.after(0, lambda result=result: on_success(result))
            except ExportStopped as exc:
                log(f"已停止：{exc}")
            except Exception as exc:
                error_text = str(exc)
                log(f"失败：{error_text}")
                root.after(0, lambda text=error_text: messagebox.showerror("执行失败", text))
            finally:
                current_stop_event["event"] = None
                root.after(0, lambda: set_running_state(False))

        threading.Thread(target=worker, daemon=True).start()

    def do_login() -> None:
        try:
            args = build_args(incremental=True, update_existing=False)
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        run_worker("登录并保存凭证", args, lambda: login_and_save_auth(args, wait_for_login_dialog))

    def on_toc_loaded(result: dict[str, Any]) -> None:
        toc_state["nodes"] = result.get("nodes") or []
        toc_state["selected"] = set(all_doc_ids())
        render_toc_tree()

    def do_scan_toc() -> None:
        try:
            args = build_args(incremental=True, update_existing=False)
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        run_worker("读取目录", args, lambda: scan_workspace_tree(args), on_toc_loaded)

    def do_incremental() -> None:
        try:
            args = build_args(incremental=True, update_existing=False, selected_doc_ids=selected_doc_ids_for_export())
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        run_worker("增量更新缺失文档", args, lambda: export_workspace(args))

    def do_full_export() -> None:
        try:
            args = build_args(incremental=False, update_existing=True, selected_doc_ids=selected_doc_ids_for_export())
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        run_worker("全量覆盖导出", args, lambda: export_workspace(args))

    form = tk.Frame(body, padx=14, pady=12)
    form.pack(fill="x")
    form.columnconfigure(1, weight=1)

    def row(label: str, variable: tk.StringVar, row_index: int, browse: Callable[[], None] | None = None) -> None:
        tk.Label(form, text=label, anchor="w").grid(row=row_index, column=0, sticky="w", pady=5)
        tk.Entry(form, textvariable=variable).grid(row=row_index, column=1, sticky="ew", padx=8, pady=5)
        if browse:
            tk.Button(form, text="选择", command=browse).grid(row=row_index, column=2, pady=5)

    def browser_row(row_index: int) -> None:
        tk.Label(form, text="浏览器程序路径", anchor="w").grid(row=row_index, column=0, sticky="w", pady=5)
        tk.Entry(form, textvariable=browser_path_var).grid(row=row_index, column=1, sticky="ew", padx=8, pady=5)
        tk.Button(form, text="选择", command=browse_browser).grid(row=row_index, column=2, pady=5)
        tk.Button(form, text="查找", command=detect_browser).grid(row=row_index, column=3, padx=(6, 0), pady=5)

    row("知识库 URL", workspace_var, 0)
    row("输出目录", output_var, 1, browse_output)
    row("凭证文件", auth_var, 2, browse_auth)
    row("浏览器配置目录", profile_var, 3, browse_profile)
    browser_row(4)
    row("调试端口", port_var, 5)
    row("请求延迟秒", request_delay_var, 6)
    row("请求随机浮动秒", request_jitter_var, 7)
    tk.Checkbutton(form, text="导出后关闭本工具启动的浏览器", variable=close_chrome_var).grid(row=8, column=1, sticky="w", pady=5)

    actions = tk.Frame(body, padx=14, pady=4)
    actions.pack(fill="x")
    buttons.extend(
        [
            tk.Button(actions, text="1. 登录并保存凭证", command=do_login, width=18),
            tk.Button(actions, text="2. 读取目录", command=do_scan_toc, width=14),
            tk.Button(actions, text="3. 增量导出选中/全部", command=do_incremental, width=20),
            tk.Button(actions, text="4. 全量导出选中/全部", command=do_full_export, width=20),
            tk.Button(actions, text="停止当前任务", command=stop_current_task, width=14, state="disabled"),
            tk.Button(actions, text="打开输出目录", command=open_output, width=14),
        ]
    )
    stop_button = buttons[-2]
    for button in buttons:
        button.pack(side="left", padx=5, pady=6)

    toc_frame = tk.LabelFrame(body, text="目录选择", padx=10, pady=8)
    toc_frame.pack(fill="both", expand=False, padx=14, pady=8)
    toc_header = tk.Frame(toc_frame)
    toc_header.pack(fill="x")
    tk.Label(toc_header, textvariable=toc_status_var, anchor="w").pack(side="left")
    tk.Button(toc_header, text="全选", command=lambda: set_all_toc_selected(True), width=8).pack(side="right", padx=4)
    tk.Button(toc_header, text="全不选", command=lambda: set_all_toc_selected(False), width=8).pack(side="right", padx=4)
    tk.Button(toc_header, text="反选", command=invert_toc_selected, width=8).pack(side="right", padx=4)

    toc_tree_frame = tk.Frame(toc_frame)
    toc_tree_frame.pack(fill="both", expand=True, pady=(8, 0))
    toc_tree = ttk.Treeview(toc_tree_frame, show="tree", height=10, selectmode="browse")
    toc_scroll = ttk.Scrollbar(toc_tree_frame, orient="vertical", command=toc_tree.yview)
    toc_tree.configure(yscrollcommand=toc_scroll.set)
    toc_tree.pack(side="left", fill="both", expand=True)
    toc_scroll.pack(side="right", fill="y")
    toc_tree.bind("<Double-1>", toggle_toc_selection)
    toc_tree.bind("<space>", toggle_toc_selection)

    note = tk.Label(
        body,
        text="说明：先读取目录可选择导出范围；未读取目录时默认导出全部。凭证文件保存的是登录 Cookie，不保存密码。",
        anchor="w",
        padx=14,
    )
    note.pack(fill="x")

    log_text = scrolledtext.ScrolledText(body, height=12, state="disabled")
    log_text.pack(fill="both", expand=True, padx=14, pady=12)
    poll_log()
    root.mainloop()
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Aliyun Thoughts workspace to Markdown.")
    parser.add_argument("--gui", action="store_true", help="Open the graphical interface")
    parser.add_argument("--login", action="store_true", help="Open browser, let you log in, then save auth cookies")
    parser.add_argument("--login-wait-seconds", type=float, default=0.0, help="For non-interactive GUI wrappers, wait this many seconds before saving login cookies")
    parser.add_argument("--scan-toc", action="store_true", help="Read the Thoughts workspace directory and print it as JSON, without exporting")
    parser.add_argument("--workspace-url", help="Thoughts workspace URL, usually .../workspaces/<id>/overview")
    parser.add_argument("--workspace-id", help="Optional workspace id override")
    parser.add_argument("--output", help="Output directory")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Chrome remote debugging port")
    parser.add_argument("--profile-dir", help=f"Chrome profile dir. Omit to auto-use {default_profile_path()}")
    parser.add_argument("--browser-path", help="Optional Chrome/Edge/Chromium executable path")
    parser.add_argument("--auth-file", help=f"Auth cookie file. Omit to auto-use {default_auth_path()}")
    parser.add_argument("--skip-auth-load", action="store_true", help="Do not load saved auth cookies before export")
    parser.add_argument("--wait-login", action="store_true", help="Pause for manual login before exporting")
    parser.add_argument("--incremental", action="store_true", help="Only export documents missing from local Markdown")
    parser.add_argument("--update-existing", action="store_true", help="With --incremental, update existing documents too")
    add_checkpoint_args(parser)
    parser.add_argument("--doc-id", action="append", dest="selected_doc_ids", help="Export one specific document id, repeatable")
    parser.add_argument("--doc-id-file", default="", help="Read selected document ids from a JSON array/object or line-based text file")
    parser.add_argument("--render-timeout", type=int, default=20, help="Seconds to wait for each document render")
    parser.add_argument("--no-api-export", dest="api_export", action="store_false", help="Disable fast edit API export and use browser-rendered DOM extraction")
    parser.add_argument("--download-timeout", type=int, default=30, help="Seconds to wait for each image download")
    parser.add_argument("--image-workers", type=int, default=6, help="Parallel image downloads per document")
    parser.add_argument("--progress-every", type=int, default=20, help="Print progress after N documents")
    parser.add_argument("--request-delay", type=float, default=0.1, help="Fixed seconds to wait before each document/API request")
    parser.add_argument("--request-jitter", type=float, default=0.0, help="Extra random seconds added before each document/API request")
    parser.add_argument("--keep-remote-images", action="store_true", default=True, help="Keep remote image URLs when download fails")
    parser.add_argument("--drop-failed-images", dest="keep_remote_images", action="store_false", help="Remove image URL when download fails")
    parser.add_argument("--close-started-chrome", action="store_true", help="Close Chrome started by this script after export")
    args = parser.parse_args(argv)
    extend_arg_list_from_file(args, "selected_doc_ids")
    return args


def main(argv: list[str]) -> int:
    if not argv or "--gui" in argv:
        print("旧版 Python GUI 已废弃，请使用 Electron 桌面端：start-wandao.cmd 或 ./start-wandao.sh", file=sys.stderr)
        return 2

    args = parse_args(argv)
    try:
        if not args.workspace_url:
            raise ExportError("--workspace-url is required")
        if args.login:
            report = login_and_save_auth(args)
        elif args.scan_toc:
            report = scan_workspace_tree(args)
        else:
            if not args.output:
                raise ExportError("--output is required unless --login is used")
            report = export_workspace(args)
    except KeyboardInterrupt:
        emit(args, "阿里云 Thoughts 导出已停止。", event="task.stopped", level="warn")
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        emit(
            args,
            f"导出任务失败：{compact_error(exc, 500)}",
            event="task.failed",
            level="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1
    if args.scan_toc:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        keys = (
            "cookieCount",
            "authFile",
            "totalDocs",
            "exportedDocs",
            "skippedDocs",
            "apiExportedDocs",
            "domFallbackDocs",
            "requestCount",
            "stopped",
            "imageSuccess",
            "imageFailureCount",
        )
        print(json.dumps({k: report[k] for k in keys if k in report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
