#!/usr/bin/env python3
# Author: tllovesxs
"""
Standalone exporter for Aliyun Thoughts workspace documents.

It can:
  - open Chrome/Edge for manual login and save reusable auth cookies;
  - restore saved auth without Codex or browser extensions;
  - export all workspace documents to Markdown;
  - incrementally add documents that do not exist locally yet;
  - run from CLI or a small Tkinter GUI.

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

GUI:
  python export_aliyun_thoughts.py --gui

The exporter controls Chrome through Chrome DevTools Protocol. It does not need
Codex. Saved auth files contain session cookies, so keep them private.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR
DEFAULT_PORT = 9222
DEFAULT_PROFILE = ".aliyun-thoughts-chrome-profile"
DEFAULT_AUTH_FILE = ".aliyun_thoughts_auth.json"
FORBIDDEN_FILENAME_CHARS = r'<>:"/\|?*'


class ExportError(RuntimeError):
    pass


class ExportStopped(ExportError):
    pass


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
    delay = max(0.0, float(getattr(args, "request_delay", 0.8) or 0))
    jitter = max(0.0, float(getattr(args, "request_jitter", 0.4) or 0))
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
        raise ExportError(f"Timed out waiting for CDP response: {method}")

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


def http_json(url: str, timeout: int = 10) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def chrome_debug_available(port: int) -> bool:
    try:
        http_json(f"http://127.0.0.1:{port}/json/version", timeout=2)
        return True
    except Exception:
        return False


def find_chrome() -> str | None:
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    for name in ("chrome", "chrome.exe", "msedge", "msedge.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def start_chrome(port: int, profile_dir: Path, url: str) -> subprocess.Popen[Any]:
    chrome = find_chrome()
    if not chrome:
        raise ExportError("Chrome/Edge executable was not found. Start Chrome manually with --remote-debugging-port.")
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
    while time.time() < deadline:
        if chrome_debug_available(port):
            return
        time.sleep(1)
    raise ExportError(f"Chrome remote debugging port {port} is not available")


def page_for_workspace(port: int, workspace_id: str) -> dict[str, Any] | None:
    pages = http_json(f"http://127.0.0.1:{port}/json/list", timeout=5)
    for page in pages:
        url = page.get("url", "")
        if "thoughts.aliyun.com" in url and workspace_id in url and page.get("type") == "page":
            return page
    return None


def open_tab(port: int, url: str) -> None:
    encoded = urllib.parse.quote(url, safe="")
    urllib.request.urlopen(f"http://127.0.0.1:{port}/json/new?{encoded}", timeout=5).close()


def emit(args: argparse.Namespace | None, message: str) -> None:
    callback = getattr(args, "log_callback", None) if args is not None else None
    if callback:
        callback(message)
    else:
        print(message)


def default_auth_path() -> Path:
    return PROJECT_DIR / DEFAULT_AUTH_FILE


def default_profile_path() -> Path:
    env_profile = os.environ.get("ALIYUN_THOUGHTS_PROFILE_DIR")
    if env_profile:
        return Path(env_profile).expanduser().resolve()
    return PROJECT_DIR / DEFAULT_PROFILE


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
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    auth_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"cookieCount": len(cookies), "authFile": str(auth_file)}


def load_auth_state(cdp: CDPClient, auth_file: Path) -> int:
    if not auth_file.exists():
        raise ExportError(f"Auth file does not exist: {auth_file}")
    payload = json.loads(auth_file.read_text(encoding="utf-8"))
    cookies = [prepare_cookie_for_set(cookie) for cookie in payload.get("cookies", [])]
    cookies = [cookie for cookie in cookies if cookie.get("name") and cookie.get("value")]
    if not cookies:
        raise ExportError(f"No cookies found in auth file: {auth_file}")
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
        chrome_proc = start_chrome(args.port, profile, target_url)
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
    expression = f"fetch({js_string(url)}).then(r => r.json())"
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


EXTRACTOR_JS = r"""
(() => {
  function esc(s) {
    return (s || "").replace(/\u00a0/g, " ").replace(/\n{3,}/g, "\n\n").trim();
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
  const markdown = [...editor.children].map(block).join("").replace(/\n{3,}/g, "\n\n").trim() + "\n";
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


def download_image(url: str, dest_dir: Path, timeout: int) -> Path:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
        ext = guess_extension(url, response.headers.get("Content-Type"))
    dest_dir.mkdir(parents=True, exist_ok=True)
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
    success = 0
    for url in sorted(set(images)):
        check_stopped(args)
        if not url.startswith(("http://", "https://")):
            continue
        try:
            target = download_image(url, md_path.parent / "assets", download_timeout)
            markdown = markdown.replace(url, os.path.relpath(target, md_path.parent).replace("\\", "/"))
            success += 1
        except Exception as exc:
            failures.append({"url": url, "error": str(exc)})
            if not keep_remote_images:
                markdown = markdown.replace(url, "")
    return markdown, success, failures


def build_paths(nodes: list[Node], output: Path) -> tuple[dict[str, Path], dict[str, list[Node]], list[Node]]:
    children: dict[str, list[Node]] = {}
    for node in nodes:
        children.setdefault(node.parent_id or "ROOT", []).append(node)
    for bucket in children.values():
        bucket.sort(key=lambda n: (n.pos, n.title))

    top_folders = [n for n in children.get("ROOT", []) if n.type == "folder"]
    folder_paths = {
        folder.id: output / f"{pad(index)}-{sanitize_filename(folder.title)}"
        for index, folder in enumerate(top_folders, start=1)
    }
    return folder_paths, children, top_folders


def top_folder_for(node: Node, by_id: dict[str, Node], folder_paths: dict[str, Path]) -> str | None:
    current = by_id.get(node.parent_id or "")
    while current and current.parent_id:
        current = by_id.get(current.parent_id)
    if current and current.id in folder_paths:
        return current.id
    return None


def document_target_path(
    doc: Node,
    index: int,
    by_id: dict[str, Node],
    children: dict[str, list[Node]],
    folder_paths: dict[str, Path],
    output: Path,
) -> Path:
    folder_id = top_folder_for(doc, by_id, folder_paths)
    target_dir = folder_paths.get(folder_id or "", output)
    siblings = [n for n in children.get(doc.parent_id or "ROOT", []) if n.type == "document"]
    doc_index = siblings.index(doc) + 1 if doc in siblings else index
    return target_dir / f"{pad(doc_index)}-{sanitize_filename(doc.title)}.md"


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
    top_folders: list[Node],
    children: dict[str, list[Node]],
    folder_paths: dict[str, Path],
    doc_paths: dict[str, Path] | None = None,
    selected_doc_ids: set[str] | None = None,
) -> None:
    index_path = output / "00-知识库入口.md"
    lines = ["# 阿里云 Thoughts 教学文档", "", "> 从阿里云 Thoughts 知识库导出。", ""]
    for folder_index, folder in enumerate(top_folders, start=1):
        docs = [
            n
            for n in children.get(folder.id, [])
            if n.type == "document" and (not selected_doc_ids or n.id in selected_doc_ids)
        ]
        if not docs:
            continue
        lines.extend([f"## {pad(folder_index)} {folder.title}", ""])
        for doc_index, doc in enumerate(docs, start=1):
            doc_path = (doc_paths or {}).get(doc.id) or folder_paths[folder.id] / f"{pad(doc_index)}-{sanitize_filename(doc.title)}.md"
            rel_path = os.path.relpath(doc_path, index_path.parent).replace("\\", "/")
            lines.append(f"- [{doc.title}]({rel_path})")
        lines.append("")
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
    cdp, chrome_proc = connect_workspace_browser(args, workspace_url, workspace_id, workspace_url)
    try:
        auth_file = auth_path_from_args(args)
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

    cdp, chrome_proc = connect_workspace_browser(args, workspace_url, workspace_id, workspace_url)
    try:
        auth_file = auth_path_from_args(args)
        if auth_file.exists() and not args.skip_auth_load:
            cookie_count = load_auth_state(cdp, auth_file)
            emit(args, f"Loaded {cookie_count} auth cookies from {auth_file}")
            cdp.navigate(workspace_url)
            time.sleep(2)

        emit(args, "Chrome page is ready. If login is required, finish login in Chrome.")
        if args.wait_login:
            input("Press Enter after the workspace page is logged in and visible...")

        nodes = load_tree(cdp, workspace_id, args)
        by_id = {node.id: node for node in nodes}
        folder_paths, children, top_folders = build_paths(nodes, output)
        for path in folder_paths.values():
            path.mkdir(parents=True, exist_ok=True)

        selected_doc_ids = set(getattr(args, "selected_doc_ids", None) or [])
        docs = [node for node in nodes if node.type == "document" and (not selected_doc_ids or node.id in selected_doc_ids)]
        existing_docs = scan_exported_docs(output)
        doc_paths: dict[str, Path] = {}
        failures: list[dict[str, str]] = []
        image_failures: list[dict[str, Any]] = []
        image_success = 0
        exported = 0
        skipped = 0
        stopped = False

        for index, doc in enumerate(docs, start=1):
            if stop_requested(args):
                stopped = True
                emit(args, "收到停止请求，正在结束并写入已完成的导出结果。")
                break

            md_path = document_target_path(doc, index, by_id, children, folder_paths, output)
            doc_paths[doc.id] = existing_docs.get(doc.id, md_path)

            if args.incremental and doc.id in existing_docs and not args.update_existing:
                skipped += 1
                continue

            try:
                result = extract_document(cdp, workspace_id, doc, args.render_timeout, args)
                markdown = result.get("markdown") or ""
                if not markdown.startswith("#"):
                    markdown = f"# {doc.title}\n\n{markdown}"
                markdown = re.sub(r"^#\s+[^\n]+", f"# {doc.title}", markdown, count=1)
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
                exported += 1
            except ExportStopped:
                stopped = True
                emit(args, "收到停止请求，当前文档未写入，正在结束。")
                break
            except Exception as exc:
                failures.append({"id": doc.id, "title": doc.title, "error": str(exc)})

            if index % args.progress_every == 0 or index == len(docs):
                emit(
                    args,
                    f"progress {index}/{len(docs)} exported={exported} skipped={skipped} "
                    f"image_success={image_success} failures={len(failures)}",
                )

        write_index(output, top_folders, children, folder_paths, doc_paths, selected_doc_ids or None)
        report = {
            "workspaceId": workspace_id,
            "workspaceUrl": workspace_url,
            "output": str(output),
            "totalNodes": len(nodes),
            "totalDocs": len(docs),
            "selectedDocs": len(docs),
            "exportedDocs": exported,
            "skippedDocs": skipped,
            "stopped": stopped,
            "imageSuccess": image_success,
            "imageFailureCount": sum(len(item["failures"]) for item in image_failures),
            "requestCount": int(getattr(args, "_request_count", 0) or 0),
            "requestDelaySeconds": float(getattr(args, "request_delay", 0.8) or 0),
            "requestJitterSeconds": float(getattr(args, "request_jitter", 0.4) or 0),
            "failures": failures,
            "imageFailures": image_failures,
            "exportedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        (output / "00-导出报告.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    finally:
        cdp.close()
        if chrome_proc and args.close_started_chrome:
            chrome_proc.terminate()


def login_and_save_auth(args: argparse.Namespace, wait_callback: Callable[[], None] | None = None) -> dict[str, Any]:
    workspace_url = args.workspace_url
    workspace_id = args.workspace_id or extract_workspace_id(workspace_url)
    auth_file = auth_path_from_args(args)
    cdp, chrome_proc = connect_workspace_browser(args, workspace_url, workspace_id, workspace_url)
    try:
        emit(args, "Chrome opened. Log in to Aliyun Thoughts in the browser.")
        if wait_callback:
            wait_callback()
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

    root = tk.Tk()
    root.title("阿里云 Thoughts 文档导出工具")
    root.geometry("980x780")

    default_workspace = ""
    workspace_var = tk.StringVar(value=default_workspace)
    output_var = tk.StringVar(value=str((PROJECT_DIR / "exports" / "aliyun-thoughts").resolve()))
    auth_var = tk.StringVar(value=str(default_auth_path()))
    profile_var = tk.StringVar(value=str(default_profile_path()))
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
            wait_login=wait_login,
            render_timeout=20,
            download_timeout=30,
            progress_every=20,
            request_delay=max(0.0, float(request_delay_var.get().strip() or "0.8")),
            request_jitter=max(0.0, float(request_jitter_var.get().strip() or "0.4")),
            keep_remote_images=True,
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
                    for k in ("totalDocs", "exportedDocs", "skippedDocs", "requestCount", "stopped", "imageSuccess", "imageFailureCount")
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

    form = tk.Frame(root, padx=14, pady=12)
    form.pack(fill="x")
    form.columnconfigure(1, weight=1)

    def row(label: str, variable: tk.StringVar, row_index: int, browse: Callable[[], None] | None = None) -> None:
        tk.Label(form, text=label, anchor="w").grid(row=row_index, column=0, sticky="w", pady=5)
        tk.Entry(form, textvariable=variable).grid(row=row_index, column=1, sticky="ew", padx=8, pady=5)
        if browse:
            tk.Button(form, text="选择", command=browse).grid(row=row_index, column=2, pady=5)

    row("知识库 URL", workspace_var, 0)
    row("输出目录", output_var, 1, browse_output)
    row("凭证文件", auth_var, 2, browse_auth)
    row("浏览器配置目录", profile_var, 3, browse_profile)
    row("调试端口", port_var, 4)
    row("请求延迟秒", request_delay_var, 5)
    row("请求随机浮动秒", request_jitter_var, 6)
    tk.Checkbutton(form, text="导出后关闭本工具启动的浏览器", variable=close_chrome_var).grid(row=7, column=1, sticky="w", pady=5)

    actions = tk.Frame(root, padx=14, pady=4)
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

    toc_frame = tk.LabelFrame(root, text="目录选择", padx=10, pady=8)
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
        root,
        text="说明：先读取目录可选择导出范围；未读取目录时默认导出全部。凭证文件保存的是登录 Cookie，不保存密码。",
        anchor="w",
        padx=14,
    )
    note.pack(fill="x")

    log_text = scrolledtext.ScrolledText(root, height=12, state="disabled")
    log_text.pack(fill="both", expand=True, padx=14, pady=12)
    poll_log()
    root.mainloop()
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Aliyun Thoughts workspace to Markdown.")
    parser.add_argument("--gui", action="store_true", help="Open the graphical interface")
    parser.add_argument("--login", action="store_true", help="Open browser, let you log in, then save auth cookies")
    parser.add_argument("--workspace-url", help="Thoughts workspace URL, usually .../workspaces/<id>/overview")
    parser.add_argument("--workspace-id", help="Optional workspace id override")
    parser.add_argument("--output", help="Output directory")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Chrome remote debugging port")
    parser.add_argument("--profile-dir", help=f"Chrome profile dir. Omit to auto-use {default_profile_path()}")
    parser.add_argument("--auth-file", help=f"Auth cookie file. Omit to auto-use {default_auth_path()}")
    parser.add_argument("--skip-auth-load", action="store_true", help="Do not load saved auth cookies before export")
    parser.add_argument("--wait-login", action="store_true", help="Pause for manual login before exporting")
    parser.add_argument("--incremental", action="store_true", help="Only export documents missing from local Markdown")
    parser.add_argument("--update-existing", action="store_true", help="With --incremental, update existing documents too")
    parser.add_argument("--render-timeout", type=int, default=20, help="Seconds to wait for each document render")
    parser.add_argument("--download-timeout", type=int, default=30, help="Seconds to wait for each image download")
    parser.add_argument("--progress-every", type=int, default=20, help="Print progress after N documents")
    parser.add_argument("--request-delay", type=float, default=0.8, help="Fixed seconds to wait before each document/API request")
    parser.add_argument("--request-jitter", type=float, default=0.4, help="Extra random seconds added before each document/API request")
    parser.add_argument("--keep-remote-images", action="store_true", default=True, help="Keep remote image URLs when download fails")
    parser.add_argument("--drop-failed-images", dest="keep_remote_images", action="store_false", help="Remove image URL when download fails")
    parser.add_argument("--close-started-chrome", action="store_true", help="Close Chrome started by this script after export")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    if not argv or "--gui" in argv:
        return run_gui()

    args = parse_args(argv)
    try:
        if not args.workspace_url:
            raise ExportError("--workspace-url is required")
        if args.login:
            report = login_and_save_auth(args)
        else:
            if not args.output:
                raise ExportError("--output is required unless --login is used")
            report = export_workspace(args)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1
    keys = ("cookieCount", "authFile", "totalDocs", "exportedDocs", "skippedDocs", "requestCount", "stopped", "imageSuccess", "imageFailureCount")
    print(json.dumps({k: report[k] for k in keys if k in report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
