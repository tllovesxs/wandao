#!/usr/bin/env python3
# Author: tllovesxs
"""
FlowUs (息流) exporter for Wandao.

This exporter uses the FlowUs web API to export documents to Markdown.
It stores JWT tokens and cookies for authentication.

First login:
  python export_xiliu.py --login \
    --doc-url https://flowus.cn/xxx \
    --auth-file .flowus_auth.json

Scan table of contents:
  python export_xiliu.py --scan-toc \
    --doc-url https://flowus.cn/xxx \
    --auth-file .flowus_auth.json

Export:
  python export_xiliu.py \
    --doc-url https://flowus.cn/xxx \
    --output "./exports/flowus" \
    --auth-file .flowus_auth.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wandao_core.checkpoint import add_checkpoint_args, open_checkpoint_from_args
from wandao_core.logging import emit_legacy
from wandao_core.credentials import write_private_json
from wandao_core.report import finalize_report

from wandao_core.browser import (
    CDPClient,
    DEFAULT_PORT,
    ExportError,
    ExportStopped,
    check_stopped,
    chrome_debug_available,
    default_data_dir,
    default_state_path,
    find_chrome,
    http_json,
    sanitize_filename,
    wait_for_debug_port,
)


PROJECT_DIR = Path(__file__).resolve().parent
FLOWUS_WEB_URL = "https://flowus.cn/"
DEFAULT_PROFILE = ".flowus-chrome-profile"
DEFAULT_AUTH_FILE = ".flowus_auth.json"
FORBIDDEN_FILENAME_CHARS = r'<>:"/\|?*'

# FlowUs API endpoints
FLOWUS_API_BASE = "https://flowus.cn/api"
DOCS_API_URL = f"{FLOWUS_API_BASE}/docs/{{doc_id}}"
FLOWUS_CDN_BASE = "https://cdn2.flowus.cn"
FLOWUS_FILE_URLS_API = f"{FLOWUS_API_BASE}/file/create_urls"


class FlowUsError(RuntimeError):
    """User-facing export error."""
    pass


@dataclass
class FlowUsNode:
    """Represents a FlowUs document or folder."""
    id: str
    title: str
    is_dir: bool
    parent_id: str = ""
    icon: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def safe_title(self) -> str:
        return sanitize_filename(self.title, fallback="untitled")


def emit(message: str, *, event: str = "log.message", level: str = "info", **fields: Any) -> None:
    emit_legacy("flowus-export", message, event=event, level=level, **fields)


def default_auth_path() -> Path:
    return default_state_path(DEFAULT_AUTH_FILE)


def default_profile_path() -> Path:
    env_profile = os.environ.get("FLOWUS_PROFILE_DIR")
    if env_profile:
        return Path(env_profile).expanduser().resolve()
    return default_data_dir() / DEFAULT_PROFILE


def auth_path_from_args(args: argparse.Namespace) -> Path:
    return Path(args.auth_file).expanduser().resolve() if args.auth_file else default_auth_path()


def profile_path_from_args(args: argparse.Namespace) -> Path:
    return Path(args.profile_dir).expanduser().resolve() if args.profile_dir else default_profile_path()


def start_chrome(port: int, profile_dir: Path, url: str, browser_path: str | None = None) -> subprocess.Popen[Any]:
    chrome = find_chrome(browser_path)
    if not chrome:
        raise FlowUsError(
            "未找到 Chrome/Edge/Chromium。请安装浏览器，或在高级设置里手动指定浏览器程序路径。"
        )
    profile_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--disable-popup-blocking",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


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


def page_for_flowus(port: int) -> dict[str, Any] | None:
    pages = http_json(f"http://127.0.0.1:{port}/json/list", timeout=5)
    for page in pages:
        if page.get("type") != "page":
            continue
        page_url = urllib.parse.urlparse(page.get("url", ""))
        if page_url.hostname and page_url.hostname.endswith("flowus.cn"):
            return page
    return None


def connect_flowus_browser(args: argparse.Namespace) -> tuple[CDPClient, subprocess.Popen[Any] | None]:
    chrome_proc: subprocess.Popen[Any] | None = None
    if not chrome_debug_available(args.port):
        chrome_proc = start_chrome(args.port, profile_path_from_args(args), FLOWUS_WEB_URL, args.browser_path)
        wait_for_debug_port(args.port, timeout=30)

    page = page_for_flowus(args.port)
    if not page:
        open_tab(args.port, FLOWUS_WEB_URL)
        time.sleep(2)
        page = page_for_flowus(args.port)
    if not page:
        pages = http_json(f"http://127.0.0.1:{args.port}/json/list", timeout=5)
        page = next((item for item in pages if item.get("type") == "page"), None)
    if not page:
        raise FlowUsError("无法打开 FlowUs 页面，请确认浏览器可以正常启动。")

    cdp = CDPClient(page["webSocketDebuggerUrl"])
    cdp.connect()
    cdp.send("Runtime.enable")
    cdp.send("Page.enable")
    cdp.send("Network.enable")
    return cdp, chrome_proc


def is_flowus_cookie(cookie: dict[str, Any]) -> bool:
    domain = (cookie.get("domain") or "").lower()
    name = cookie.get("name") or ""
    return ("flowus.cn" in domain or domain.endswith(".flowus.cn")) and (
        name in {"next_auth", "next_auth.sig", "next_lng", "locale"}
    )


def save_auth_state(cdp: CDPClient, auth_file: Path) -> dict[str, Any]:
    data = cdp.send("Network.getAllCookies", timeout=20).get("result", {})
    cookies = [cookie for cookie in data.get("cookies", []) if is_flowus_cookie(cookie)]

    # Extract JWT token from cookies
    token = None
    for cookie in cookies:
        if cookie.get("name") == "next_auth" and cookie.get("value"):
            token = cookie["value"]
            break

    if not token:
        raise FlowUsError("没有读取到 FlowUs 登录凭证。请确认浏览器里已经登录并能看到文档列表。")

    payload = {
        "version": 1,
        "savedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "token": token,
        "cookies": cookies,
    }
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    write_private_json(auth_file, payload)
    return {"provider": "flowus", "hasToken": bool(token), "authFile": str(auth_file)}


def login_and_save_auth(args: argparse.Namespace) -> dict[str, Any]:
    auth_file = auth_path_from_args(args)
    cdp, chrome_proc = connect_flowus_browser(args)
    try:
        cdp.navigate(FLOWUS_WEB_URL)
        emit("Chrome opened. Log in to FlowUs in the browser.")
        wait_seconds = float(getattr(args, "login_wait_seconds", 0) or 0)
        if wait_seconds > 0:
            emit(f"请在浏览器中完成登录，工具将在 {int(wait_seconds)} 秒后自动保存凭证。")
            time.sleep(wait_seconds)
        else:
            input("After login is complete and the FlowUs workspace is visible, press Enter...")
        time.sleep(1)
        result = save_auth_state(cdp, auth_file)
        emit(f"Saved FlowUs auth token to {auth_file}")
        return result
    finally:
        cdp.close()
        if chrome_proc and args.close_started_chrome:
            chrome_proc.terminate()


def read_auth_payload(auth_file: Path) -> dict[str, Any]:
    if not auth_file.exists():
        raise FlowUsError(f"FlowUs 登录凭证不存在：{auth_file}。请先点击“登录并保存凭证”。")
    try:
        payload = json.loads(auth_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise FlowUsError(f"读取 FlowUs 登录凭证失败：{exc}") from exc
    token = payload.get("token")
    if not token:
        raise FlowUsError(f"FlowUs 登录凭证里没有 Token：{auth_file}")
    return payload


def throttle_request(args: argparse.Namespace | None) -> None:
    if args is None:
        return
    delay = max(0.0, float(getattr(args, "request_delay", 0) or 0))
    jitter = max(0.0, float(getattr(args, "request_jitter", 0) or 0))
    total = delay + (random.random() * jitter if jitter else 0)
    if total > 0:
        time.sleep(total)


def _friendly_network_error(exc: Exception) -> str:
    """将底层网络异常转为用户友好的错误信息。"""
    msg = str(exc)
    if "getaddrinfo failed" in msg or "Name or service not known" in msg:
        return "DNS 解析失败，无法连接到 FlowUs 服务器，请检查网络连接"
    if "Connection refused" in msg:
        return "连接被拒绝，FlowUs 服务器可能暂时不可用"
    if "timed out" in msg or "Timeout" in msg:
        return "请求超时，请检查网络连接或稍后重试"
    if "Connection reset" in msg or "Connection aborted" in msg:
        return "连接被重置，请检查网络状况后重试"
    if "SSL" in msg or "certificate" in msg:
        return "SSL 证书验证失败，请检查系统时间或网络代理设置"
    return f"网络请求失败：{exc}"


class FlowUsClient:
    """FlowUs API client."""

    def __init__(self, auth_file: Path, args: argparse.Namespace | None = None) -> None:
        payload = read_auth_payload(auth_file)
        self.token = payload.get("token", "")
        self.cookies = payload.get("cookies", [])
        self.args = args
        self.request_count = 0

    def _build_headers(self, referer: str = "https://flowus.cn/") -> dict[str, str]:
        cookie_parts = []
        for cookie in self.cookies:
            name = cookie.get("name", "")
            value = cookie.get("value", "")
            if name and value:
                cookie_parts.append(f"{name}={value}")

        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "identity",
            "Authorization": f"Bearer {self.token}",
            "Origin": "https://flowus.cn",
            "Referer": referer,
            "Cookie": "; ".join(cookie_parts),
            "x-app-origin": "web",
            "x-platform": "web-cookie",
            "x-product": "flowus",
        }

    def request(
        self,
        method: str,
        url: str,
        data: dict[str, Any] | None = None,
        timeout: int = 60,
        referer: str = "https://flowus.cn/",
    ) -> bytes:
        throttle_request(self.args)
        self.request_count += 1

        headers = self._build_headers(referer)
        body = None
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
        attempts = max(1, int(getattr(self.args, "retry", 2) or 1))
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    raw = response.read()
                    encoding = response.headers.get("Content-Encoding", "")
                    if "gzip" in encoding.lower():
                        raw = gzip.decompress(raw)
                    return raw
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code == 401:
                    raise FlowUsError(f"FlowUs 登录已失效，请重新点击“登录并保存凭证”后重试。HTTP 401：{detail[:500]}") from exc
                if exc.code not in {429, 500, 502, 503, 504} or attempt >= attempts:
                    raise FlowUsError(f"FlowUs 接口 HTTP {exc.code}：{detail[:500]}") from exc
                last_error = exc
            except urllib.error.URLError as exc:
                if attempt >= attempts:
                    raise FlowUsError(_friendly_network_error(exc.reason)) from exc
                last_error = exc
            time.sleep(min(5.0, 0.8 * attempt))

        raise FlowUsError(_friendly_network_error(last_error))

    def get_json(self, url: str, referer: str = "https://flowus.cn/") -> dict[str, Any]:
        content = self.request("GET", url, referer=referer)
        return json.loads(content.decode("utf-8", errors="replace"))

    def get_doc(self, doc_id: str) -> dict[str, Any]:
        url = DOCS_API_URL.format(doc_id=doc_id)
        referer = f"https://flowus.cn/{doc_id}"
        return self.get_json(url, referer=referer)


def safe_filename(name: str, fallback: str = "untitled") -> str:
    """Sanitize filename, removing forbidden characters."""
    cleaned = re.sub(r'[<>:"/\\|?*]', '_', name or "").strip()
    return cleaned or fallback


def build_image_url(oss_name: str) -> str:
    """Build full image URL from OSS name."""
    if oss_name.startswith("http"):
        return oss_name
    # FlowUs CDN URL pattern
    return f"{FLOWUS_CDN_BASE}/{oss_name}"


def get_signed_url(client: FlowUsClient, block_id: str, oss_name: str) -> str:
    """Get signed URL for file download using create_urls API."""
    try:
        # Build request payload
        payload = {
            "batch": [
                {
                    "blockId": block_id,
                    "ossName": oss_name,
                }
            ]
        }

        # Call create_urls API
        response_data = client.request("POST", FLOWUS_FILE_URLS_API, data=payload, timeout=30)
        response = json.loads(response_data.decode("utf-8", errors="replace"))

        if response.get("code") == 200:
            data = response.get("data", [])
            if isinstance(data, list) and len(data) > 0:
                signed_url = data[0].get("url", "")
                if signed_url:
                    return signed_url
    except Exception as exc:
        emit(f"获取签名URL失败: {_friendly_network_error(exc)}", level="warn")

    # Fallback to direct CDN URL
    return build_image_url(oss_name)


def download_image_data(client: FlowUsClient, block_id: str, oss_name: str) -> bytes | None:
    """Download image data, handling both base64 and binary responses."""
    # Try to get signed URL first
    url = get_signed_url(client, block_id, oss_name)

    try:
        response = client.request("GET", url, timeout=30)
        if not response:
            return None

        # Check if response is base64 encoded
        try:
            text = response.decode("utf-8", errors="ignore")
            # Try to parse as JSON with base64 data
            import base64
            data = json.loads(text)
            if isinstance(data, dict) and "data" in data:
                base64_str = data["data"]
                if isinstance(base64_str, str) and len(base64_str) > 100:
                    return base64.b64decode(base64_str)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        # Return raw binary data
        return response
    except Exception:
        return None


@dataclass
class ImageSaver:
    """Downloads and saves images from FlowUs documents."""
    client: FlowUsClient
    output_dir: Path
    doc_id: str
    image_count: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)
    saved: dict[str, str] = field(default_factory=dict)

    @property
    def asset_dir(self) -> Path:
        return self.output_dir / "assets"

    def save_image(self, block_id: str, oss_name: str, alt: str = "") -> str:
        """Download and save an image, return relative path."""
        if not oss_name:
            return ""

        # Check if already saved
        if oss_name in self.saved:
            return self.saved[oss_name]

        self.image_count += 1

        # Determine filename
        ext = Path(oss_name).suffix or ".png"
        base_name = Path(oss_name).stem or f"image{self.image_count:03d}"
        filename = f"{self.image_count:03d}-{safe_filename(base_name)}{ext}"

        # Download image data
        data = download_image_data(self.client, block_id, oss_name)
        if not data:
            self.failures.append({"url": oss_name, "error": "图片下载失败"})
            return ""

        # Save to file
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        target = self.asset_dir / filename
        target.write_bytes(data)

        # Return relative path
        rel = os.path.relpath(target, self.output_dir).replace("\\", "/")
        self.saved[oss_name] = rel
        return rel


def parse_flowus_url(url: str) -> str:
    """Extract document ID from FlowUs URL."""
    parsed = urllib.parse.urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        raise FlowUsError("请填写完整的 FlowUs URL")

    # Extract the last path component as document ID
    path_parts = [p for p in parsed.path.split("/") if p]
    if not path_parts:
        raise FlowUsError("无法从 URL 中提取文档 ID")

    doc_id = path_parts[-1]
    # Remove any query parameters or fragments
    doc_id = doc_id.split("?")[0].split("#")[0]

    return doc_id


def build_toc_tree(
    client: FlowUsClient,
    root_doc_id: str,
    *,
    failures: list[dict[str, Any]] | None = None,
    args: argparse.Namespace | None = None,
) -> list[FlowUsNode]:
    """Build the complete FlowUs page tree and report unreadable child subtrees."""
    visited: set[str] = set()
    nodes: list[FlowUsNode] = []
    subtree_failures = failures if failures is not None else []

    def fetch_doc(doc_id: str, *, is_root: bool = False, parent_id: str = "") -> dict[str, Any]:
        check_stopped(args)
        if doc_id in visited:
            return {}
        visited.add(doc_id)
        try:
            response = client.get_doc(doc_id)
        except FlowUsError as exc:
            if is_root:
                raise FlowUsError(f"读取根目录失败：{exc}") from exc
            subtree_failures.append({
                "type": "subtree", "documentId": doc_id, "parentId": parent_id,
                "stage": "scan", "error": str(exc),
            })
            return {}
        if response.get("code") != 200:
            error = f"API code={response.get('code', '?')}"
            if is_root:
                raise FlowUsError(f"读取根目录失败：{error}")
            subtree_failures.append({
                "type": "subtree", "documentId": doc_id, "parentId": parent_id,
                "stage": "scan", "error": error,
            })
            return {}
        blocks = response.get("data", {}).get("blocks", {})
        if not blocks or doc_id not in blocks:
            error = "响应中没有 blocks" if not blocks else "响应中缺少目标页面 block"
            if is_root:
                raise FlowUsError(f"读取根目录失败：{error}")
            subtree_failures.append({
                "type": "subtree", "documentId": doc_id, "parentId": parent_id,
                "stage": "scan", "error": error,
            })
            return {}
        return blocks

    def extract_title(block: dict[str, Any]) -> str:
        title = block.get("title", "")
        if title:
            return title
        segments = block.get("data", {}).get("segments", [])
        return segments[0].get("text", "") if segments else "未命名"

    def process_page(block_id: str, blocks: dict[str, Any], parent_id: str = "") -> None:
        check_stopped(args)
        block = blocks.get(block_id)
        if not block or block.get("type", 0) != 0:
            return
        page_children = [
            child_id for child_id in block.get("subNodes", [])
            if isinstance(child_id, str) and blocks.get(child_id, {}).get("type") == 0
        ]
        nodes.append(FlowUsNode(
            id=block_id,
            title=extract_title(block),
            is_dir=bool(page_children),
            parent_id=parent_id,
            raw=block,
        ))
        for child_id in page_children:
            child_blocks = fetch_doc(child_id, parent_id=block_id)
            if child_blocks:
                process_page(child_id, child_blocks, parent_id=block_id)

    root_blocks = fetch_doc(root_doc_id, is_root=True)
    process_page(root_doc_id, root_blocks)
    return nodes

def toc_json(args: argparse.Namespace) -> dict[str, Any]:
    """Output table of contents as JSON."""
    auth_file = auth_path_from_args(args)
    client = FlowUsClient(auth_file, args)

    url = args.doc_url
    if not url:
        raise FlowUsError("--doc-url is required for --scan-toc")

    doc_id = parse_flowus_url(url)
    emit(f"正在获取文档目录")

    failures: list[dict[str, Any]] = []
    nodes = build_toc_tree(client, doc_id, failures=failures, args=args)

    # Build parent -> children mapping to calculate per-level index
    children_map: dict[str, list[str]] = {}
    node_map: dict[str, FlowUsNode] = {}
    for node in nodes:
        node_map[node.id] = node
        parent = node.parent_id or ""
        if parent not in children_map:
            children_map[parent] = []
        children_map[parent].append(node.id)

    # Calculate per-level index for each node
    per_level_index: dict[str, int] = {}
    for node in nodes:
        parent = node.parent_id or ""
        siblings = children_map.get(parent, [])
        # Find position of this node among siblings
        idx = siblings.index(node.id) + 1 if node.id in siblings else 1
        per_level_index[node.id] = idx

    toc = []
    for node in nodes:
        toc.append({
            "index": per_level_index[node.id],
            "id": node.id,
            "title": node.title,
            "parentId": node.parent_id,
            "selectable": True,
            "icon": node.icon,
        })

    return {
        "provider": "flowus",
        "rootId": doc_id,
        "nodeCount": len(toc),
        "nodes": toc,
        "failures": failures,
    }


def convert_flowus_blocks_to_markdown(
    response: dict[str, Any],
    doc_id: str = "",
    image_saver: ImageSaver | None = None,
) -> tuple[str, int, list[dict[str, str]]]:
    """Convert FlowUs document blocks to Markdown.

    Expected input: API response { "code": 200, "data": { "blocks": { ... } } }

    Block types:
    - 0: Page (root container)
    - 1: Normal text paragraph
    - 5: Bold/title text (displayed as heading)
    - 7: Heading (with level in data.level)
    - 9: Divider
    - 14: Image

    Args:
        doc_id: The document ID to use as root. If empty, auto-detect.
        image_saver: Optional ImageSaver for downloading images.

    Returns:
        Tuple of (markdown_text, image_count, image_failures)
    """
    lines: list[str] = []

    if response.get("code") != 200:
        return "", 0, []

    blocks = response.get("data", {}).get("blocks", {})
    if not blocks:
        return "", 0, []

    # Find the root block
    root_id = None

    # If doc_id is provided, use it directly
    if doc_id and doc_id in blocks:
        root_id = doc_id
    else:
        # Fallback: find by parentId == spaceId
        for block_id, block in blocks.items():
            if block.get("type") == 0:  # Page type
                # Check if this is the main page (parentId points to space)
                parent_id = block.get("parentId", "")
                space_id = block.get("spaceId", "")
                if parent_id == space_id:
                    root_id = block_id
                    break

    if not root_id:
        # Fallback: just use first type 0 block
        for block_id, block in blocks.items():
            if block.get("type") == 0:
                root_id = block_id
                break

    if not root_id:
        return "", 0, []

    def extract_text(segments: list[dict[str, Any]], is_heading: bool = False) -> str:
        """Extract text from segments, handling inline formatting.

        Args:
            segments: List of text segments
            is_heading: If True, skip bold markers (for headings that are already bold by type)
        """
        parts = []
        for seg in segments:
            text = seg.get("text", "")
            enhancer = seg.get("enhancer", {})

            if not text:
                continue

            # Handle inline formatting (skip bold for headings)
            if not is_heading and enhancer.get("bold"):
                text = f"**{text}**"
            if enhancer.get("code"):
                text = f"`{text}`"
            if enhancer.get("italic"):
                text = f"*{text}*"
            if enhancer.get("strikethrough"):
                text = f"~~{text}~~"

            parts.append(text)

        return "".join(parts)

    def process_block(block_id: str, depth: int = 0) -> None:
        block = blocks.get(block_id)
        if not block:
            return

        block_type = block.get("type", 0)
        data = block.get("data", {})
        segments = data.get("segments", [])
        sub_nodes = block.get("subNodes", [])

        # Extract text from segments
        text = extract_text(segments)

        # Convert based on block type
        if block_type == 0:  # Page
            # Add page title as heading if it's the root
            if text and depth == 0:
                lines.append(f"# {text}")
                lines.append("")
        elif block_type == 1:  # Normal text
            if text:
                lines.append(text)
                lines.append("")
        elif block_type == 5:  # Bold/title text - treat as heading
            # For headings, extract plain text without bold markers
            plain_text = extract_text(segments, is_heading=True)
            if plain_text:
                lines.append(f"## {plain_text}")
                lines.append("")
        elif block_type == 7:  # Heading with level
            level = data.get("level", 3)
            # For headings, extract plain text without bold markers
            plain_text = extract_text(segments, is_heading=True)
            if plain_text:
                # Clamp level between 1-6
                level = max(1, min(6, level))
                lines.append(f"{'#' * level} {plain_text}")
                lines.append("")
        elif block_type == 9:  # Divider
            lines.append("---")
            lines.append("")
        elif block_type == 14:  # Image
            oss_name = data.get("ossName", "")
            alt = text or "image"
            if image_saver and oss_name:
                rel_path = image_saver.save_image(block_id, oss_name, alt)
                if rel_path:
                    lines.append(f"![{alt}]({rel_path})")
                    lines.append("")
                else:
                    # Image download failed, add placeholder
                    lines.append(f"![{alt}](图片下载失败: {oss_name})")
                    lines.append("")
            elif oss_name:
                # No image saver, use remote URL
                url = build_image_url(oss_name)
                lines.append(f"![{alt}]({url})")
                lines.append("")
        else:
            # Unknown type, just add text if any
            if text:
                lines.append(text)
                lines.append("")

        # Process children
        for child_id in sub_nodes:
            if isinstance(child_id, str):
                process_block(child_id, depth + 1)

    process_block(root_id)

    image_count = image_saver.image_count if image_saver else 0
    image_failures = image_saver.failures if image_saver else []

    return "\n".join(lines).strip(), image_count, image_failures


def _has_markdown_body(markdown: str, page_title: str) -> bool:
    """Return whether converted Markdown contains content beyond the generated page title."""
    lines = markdown.strip().splitlines()
    if lines and lines[0].strip() == f"# {page_title}".strip():
        lines = lines[1:]
    return bool("\n".join(lines).strip())


def export_single_doc(
    client: FlowUsClient,
    node: FlowUsNode,
    output_dir: Path,
    *,
    incremental: bool = False,
    update_existing: bool = False,
    timeout: int = 60,
) -> Path | None:
    """Export a single document to Markdown file."""
    # Download document
    doc = client.get_doc(node.id)

    # Convert to Markdown
    markdown, _, _ = convert_flowus_blocks_to_markdown(doc, node.id)

    # If node is a directory (has children), create directory
    if node.is_dir:
        dir_path = output_dir / node.safe_title
        dir_path.mkdir(parents=True, exist_ok=True)

    # A generated page-title heading alone is not document content.
    if not _has_markdown_body(markdown, node.title):
        if node.is_dir:
            emit(f"页面内容为空，仅创建目录: {node.title}", level="warn")
            return dir_path
        emit(f"文档内容为空: {node.title}", level="warn")
        return None

    # Write to file
    md_path = output_dir / f"{node.safe_title}.md"

    if incremental and md_path.exists():
        if not update_existing:
            emit(f"跳过已存在文件: {md_path.name}")
            return md_path
        # Check if content changed
        try:
            existing = md_path.read_text(encoding="utf-8")
            if existing == markdown:
                return md_path
        except Exception:
            pass

    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(markdown, encoding="utf-8")
    return md_path


def _stable_output_components(nodes: list[FlowUsNode]) -> dict[str, str]:
    groups: dict[tuple[str, str], list[FlowUsNode]] = {}
    for node in nodes:
        safe = node.safe_title
        groups.setdefault((node.parent_id, safe.casefold()), []).append(node)
    components: dict[str, str] = {}
    for siblings in groups.values():
        collision = len(siblings) > 1
        for node in siblings:
            suffix = sanitize_filename(node.id, fallback="id", max_len=12)
            components[node.id] = f"{node.safe_title}-{suffix}" if collision else node.safe_title
    return components


def _export_flowus_impl(args: argparse.Namespace, checkpoint: Any) -> dict[str, Any]:
    auth_file = auth_path_from_args(args)
    client = FlowUsClient(auth_file, args)
    url = args.doc_url
    if not url:
        raise FlowUsError("--doc-url is required for export")
    output = Path(args.output).expanduser().resolve() if args.output else Path.cwd() / "exports" / "flowus"
    doc_id = parse_flowus_url(url)
    emit("开始导出 FlowUs 文档")

    tree_failures: list[dict[str, Any]] = []
    all_nodes = build_toc_tree(client, doc_id, failures=tree_failures, args=args)
    full_node_map = {node.id: node for node in all_nodes}
    components = _stable_output_components(all_nodes)
    selected_ids = set(getattr(args, "selected_doc_ids", None) or [])
    nodes = [node for node in all_nodes if not selected_ids or node.id in selected_ids]
    if selected_ids and all_nodes and not nodes:
        raise FlowUsError(
            f"选中的文档 ID 均未在目录中找到（共 {len(selected_ids)} 个）。请重新读取目录后重试。"
        )
    if selected_ids:
        emit(f"已选择 {len(nodes)} 个文档进行导出")
    emit(f"共发现 {len(nodes)} 个文档节点")

    if checkpoint:
        checkpoint.start_task({
            "source": url,
            "target": str(output),
            "outputDir": str(output),
            "docId": doc_id,
            "docUrl": url,
            "totalNodes": len(nodes),
            "resume": bool(getattr(args, "resume", False)),
            "retryFailed": bool(getattr(args, "retry_failed", False)),
        })
        for failure in tree_failures:
            failed_id = str(failure.get("documentId") or "")
            if not failed_id:
                continue
            failed_key = f"xiliu:doc:{failed_id}"
            checkpoint.upsert_item(
                failed_key,
                title=failed_id,
                source_id=failed_id,
                parent_key=f"xiliu:doc:{failure.get('parentId')}" if failure.get("parentId") else "",
                metadata={**failure, "docId": failed_id, "stage": "scan"},
            )
            checkpoint.fail_item(failed_key, str(failure.get("error") or "子树读取失败"))
        for node in nodes:
            checkpoint.upsert_item(
                f"xiliu:doc:{node.id}", title=node.title, source_id=node.id,
                parent_key=f"xiliu:doc:{node.parent_id}" if node.parent_id else "",
                metadata={"docId": node.id, "parentId": node.parent_id, "stage": "listed"},
            )

    def get_output_path(node: FlowUsNode) -> Path:
        ancestors: list[FlowUsNode] = []
        current = node
        seen: set[str] = set()
        while current.parent_id and current.parent_id in full_node_map and current.parent_id not in seen:
            seen.add(current.parent_id)
            current = full_node_map[current.parent_id]
            ancestors.append(current)
        path = output
        for ancestor in reversed(ancestors):
            path /= components[ancestor.id]
        component = components[node.id]
        return path / component if node.is_dir else path / f"{component}.md"

    is_resume = bool(getattr(args, "resume", False))
    is_retry_failed = bool(getattr(args, "retry_failed", False))
    if checkpoint and is_retry_failed:
        nodes = [node for node in nodes if checkpoint.item_status(f"xiliu:doc:{node.id}") == "failed"]
        emit(f"只重试失败项：剩余 {len(nodes)} 个文档")
    elif checkpoint and is_resume:
        nodes = [node for node in nodes if checkpoint.item_status(f"xiliu:doc:{node.id}") != "completed"]
        emit(f"断点续跑：跳过已完成项，剩余 {len(nodes)} 个文档")

    exported = 0
    skipped = 0
    document_failures: list[dict[str, Any]] = []
    resource_failures: list[dict[str, Any]] = []
    total_images = 0

    for index, node in enumerate(nodes, 1):
        check_stopped(args)
        item_key = f"xiliu:doc:{node.id}"
        file_path = get_output_path(node)
        if node.is_dir:
            file_path = file_path / f"{components[node.id]}.md"
        try:
            if args.incremental and file_path.exists() and not args.update_existing:
                skipped += 1
                if checkpoint:
                    checkpoint.upsert_item(item_key, title=node.title, metadata={"docId": node.id, "stage": "completed"})
                    checkpoint.complete_item(item_key, local_path=str(file_path), metadata={"docId": node.id, "skippedExisting": True})
                continue
            if checkpoint and (is_resume or is_retry_failed) and checkpoint.item_status(item_key) == "completed" and file_path.exists():
                skipped += 1
                continue
            if checkpoint:
                checkpoint.upsert_item(item_key, title=node.title, metadata={"docId": node.id, "stage": "downloading"})
                checkpoint.start_item(item_key, "downloading")

            check_stopped(args)
            doc = client.get_doc(node.id)
            md_root = get_output_path(node)
            image_saver = ImageSaver(client=client, output_dir=md_root if node.is_dir else md_root.parent, doc_id=node.id)
            markdown, image_count, image_errors = convert_flowus_blocks_to_markdown(doc, node.id, image_saver)
            total_images += image_count
            node_resource_failures = [
                {
                    "itemKey": item_key,
                    "documentId": node.id,
                    "title": node.title,
                    "type": "image",
                    "source": failure.get("url", ""),
                    "error": failure.get("error", "图片下载失败"),
                }
                for failure in image_errors
            ]
            resource_failures.extend(node_resource_failures)

            if node.is_dir:
                md_root.mkdir(parents=True, exist_ok=True)
            if _has_markdown_body(markdown, node.title):
                if args.incremental and args.update_existing and file_path.exists():
                    try:
                        if file_path.read_text(encoding="utf-8") == markdown:
                            skipped += 1
                            if checkpoint and not node_resource_failures:
                                checkpoint.upsert_item(item_key, title=node.title, metadata={"docId": node.id, "stage": "completed"})
                                checkpoint.complete_item(item_key, local_path=str(file_path), metadata={"docId": node.id, "skippedExisting": True})
                            continue
                    except OSError:
                        pass
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(markdown, encoding="utf-8")
                exported += 1
            elif not node.is_dir:
                skipped += 1

            if node_resource_failures:
                failure = {
                    "itemKey": item_key,
                    "documentId": node.id,
                    "title": node.title,
                    "stage": "resources",
                    "error": f"{len(node_resource_failures)} 个图片资源下载失败",
                }
                document_failures.append(failure)
                if checkpoint:
                    checkpoint.upsert_item(item_key, title=node.title, metadata={"docId": node.id, "stage": "resources", "localPath": str(file_path)})
                    checkpoint.fail_item(item_key, failure["error"])
            elif checkpoint:
                checkpoint.upsert_item(item_key, title=node.title, metadata={"docId": node.id, "stage": "completed"})
                checkpoint.complete_item(item_key, local_path=str(file_path), metadata={"docId": node.id, "images": image_count})
            emit(f"[{index}/{len(nodes)}] 导出成功: {node.title}" + (f" (含 {image_count} 张图片)" if image_count else ""))
        except ExportStopped:
            if checkpoint:
                checkpoint.upsert_item(item_key, title=node.title, metadata={"docId": node.id, "stage": "stopped"})
                checkpoint.fail_item(item_key, "用户已停止当前任务")
            raise
        except Exception as exc:
            failure = {
                "itemKey": item_key, "documentId": node.id, "title": node.title,
                "stage": "content", "error": str(exc),
            }
            document_failures.append(failure)
            if checkpoint:
                checkpoint.upsert_item(item_key, title=node.title, metadata={"docId": node.id, "stage": "failed"})
                checkpoint.fail_item(item_key, str(exc))
            emit(f"[{index}/{len(nodes)}] 导出失败 {node.title}: {exc}", level="error")

    failures = tree_failures + document_failures
    result = finalize_report({
        "provider": "flowus",
        "mode": "export",
        "rootId": doc_id,
        "totalNodes": len(nodes),
        "totalDocs": len(nodes),
        "exported": exported,
        "exportedDocs": exported,
        "skipped": skipped,
        "skippedDocs": skipped,
        "failed": len(failures),
        "failureCount": len(failures),
        "failures": failures,
        "totalImages": total_images,
        "imageFailures": resource_failures,
        "imageFailureCount": len(resource_failures),
        "resourceFailures": resource_failures,
        "resourceFailureCount": len(resource_failures),
        "outputDir": str(output),
        "stopped": False,
    }, provider="flowus-export", mode="export", output=output)
    if checkpoint:
        checkpoint.complete_task(result)
    emit(
        f"导出完成：成功 {exported}, 跳过 {skipped}, 未通过 {len(failures)}",
        event="task.completed",
        level="success" if not failures and not resource_failures else "warn",
        stats={
            "exportedDocs": exported, "skippedDocs": skipped,
            "failureCount": len(failures), "resourceFailureCount": len(resource_failures),
        },
    )
    return result


def export_flowus(args: argparse.Namespace) -> dict[str, Any]:
    checkpoint = open_checkpoint_from_args(args, "xiliu", "export")
    try:
        return _export_flowus_impl(args, checkpoint)
    except ExportStopped:
        if checkpoint and getattr(checkpoint, "_lease_claimed", False):
            checkpoint.fail_task("用户已停止当前任务", status="stopped")
        raise
    except Exception as exc:
        if checkpoint and getattr(checkpoint, "_lease_claimed", False):
            checkpoint.fail_task(str(exc), status="failed")
        raise
    finally:
        if checkpoint:
            checkpoint.close()

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出 FlowUs (息流) 文档为 Markdown")
    parser.add_argument("--gui", action="store_true", help="打开简易图形界面")
    parser.add_argument("--login", action="store_true", help="打开浏览器登录并保存 Token")
    parser.add_argument("--login-wait-seconds", type=float, default=0.0, help="等待指定秒数后自动保存登录凭证")
    parser.add_argument("--scan-toc", action="store_true", help="读取远端目录并输出 JSON")
    parser.add_argument("--doc-url", help="FlowUs 文档 URL (例如 https://flowus.cn/xxx)")
    parser.add_argument("--output", help="Markdown 输出目录")
    parser.add_argument("--auth-file", help=f"登录凭证文件，默认 {default_auth_path()}")
    parser.add_argument("--profile-dir", help=f"浏览器配置目录，默认 {default_profile_path()}")
    parser.add_argument("--browser-path", help="可选 Chrome/Edge/Chromium 可执行文件路径")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Chrome DevTools 调试端口")
    parser.add_argument("--close-started-chrome", action="store_true", help="任务结束后关闭本脚本启动的浏览器")
    parser.add_argument("--incremental", action="store_true", help="已有文件则跳过")
    parser.add_argument("--update-existing", action="store_true", help="配合 --incremental 时也更新已有文件")
    parser.add_argument("--progress-every", type=int, default=1, help="每处理多少篇输出一次进度")
    parser.add_argument("--request-delay", type=float, default=0.8, help="每次请求前固定等待秒数")
    parser.add_argument("--request-jitter", type=float, default=0.4, help="每次请求额外随机等待秒数")
    parser.add_argument("--download-timeout", type=int, default=60, help="文档下载超时时间")
    parser.add_argument("--retry", type=int, default=2, help="网络请求失败时的重试次数")
    parser.add_argument("--doc-id", action="append", dest="selected_doc_ids", help="只导出指定文档 ID，可重复")
    add_checkpoint_args(parser)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if not argv or args.gui:
        print("旧版 Python GUI 已废弃，请使用 Electron 桌面端：start-wandao.cmd 或 ./start-wandao.sh", file=sys.stderr)
        return 2
    try:
        if args.login:
            result = login_and_save_auth(args)
        elif args.scan_toc:
            result = toc_json(args)
        else:
            if not args.output:
                raise FlowUsError("--output is required unless --login or --scan-toc is used")
            result = export_flowus(args)
    except (KeyboardInterrupt, ExportStopped):
        emit("FlowUs 导出已停止。", event="task.stopped", level="warn")
        print("Interrupted.", file=sys.stderr)
        return 130
    except (FlowUsError, ExportError) as exc:
        emit(
            f"FlowUs 导出失败：{exc}",
            event="task.failed",
            level="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        emit(
            f"FlowUs 导出失败：{exc}",
            event="task.failed",
            level="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
