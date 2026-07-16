#!/usr/bin/env python3
"""Export authorized DingTalk Docs folders to local Markdown.

The exporter intentionally performs authenticated requests inside a dedicated
Chrome profile through CDP.  Cookies and short-lived access tokens stay in the
browser; the small local auth summary only records that the profile was checked.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from wandao_cli import extend_arg_list_from_file
from wandao_core.browser import (
    CDPClient,
    ExportError,
    ExportStopped,
    check_stopped,
    chrome_debug_available,
    default_data_dir,
    emit,
    http_json,
    open_tab,
    start_chrome,
    throttle_request,
    wait_for_debug_port,
)
from wandao_core.checkpoint import add_checkpoint_args, open_checkpoint_from_args
from wandao_core.credentials import write_private_json
from wandao_core.report import finalize_report


PLUGIN_ID = "dingtalk"
ENTRY_URL = "https://docs.dingtalk.com/"
SUPPORTED_HOSTS = {"docs.dingtalk.com", "alidocs.dingtalk.com"}
DEFAULT_PORT = 9245
DEFAULT_PROFILE = ".dingtalk-chrome-profile"
DEFAULT_AUTH_FILE = ".dingtalk_auth.json"
FORBIDDEN_FILENAME_CHARS = r'<>:"/\\|?*'
SUPPORTED_DOCUMENT_TYPES = {"alidoc"}
MAX_RESOURCE_BYTES = 25 * 1024 * 1024
DOCUMENT_REQUEST_TIMEOUT_SECONDS = 45
ASSET_REQUEST_TIMEOUT_SECONDS = 15
ASSET_DOWNLOAD_WORKERS = 6
ASSET_DIRECT_RETRIES = 3


@dataclass(frozen=True)
class DingEntry:
    uuid: str
    key: str
    doc_key: str
    title: str
    content_type: str
    parent_uuid: str
    is_folder: bool
    has_children: bool


@dataclass(frozen=True)
class ImageRef:
    source: str
    alt: str


@dataclass
class RenderResult:
    markdown: str
    images: list[ImageRef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def default_profile_path() -> Path:
    override = os.environ.get("DINGTALK_PROFILE_DIR")
    return Path(override).expanduser().resolve() if override else default_data_dir() / DEFAULT_PROFILE


def default_auth_path() -> Path:
    return default_data_dir() / DEFAULT_AUTH_FILE


def auth_path_from_args(args: argparse.Namespace) -> Path:
    return Path(args.auth_file).expanduser().resolve() if args.auth_file else default_auth_path().resolve()


def safe_path_segment(value: str, fallback: str = "未命名", max_length: int = 90) -> str:
    text = html.unescape(str(value or "")).replace("\xa0", " ")
    text = text.replace("..", "")
    cleaned = "".join("-" if char in FORBIDDEN_FILENAME_CHARS or ord(char) < 32 else char for char in text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(". ")
    return (cleaned or fallback)[:max_length]


def markdown_path(value: str) -> str:
    return value.replace("\\", "/").replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def safe_resource_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(str(value or ""))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def is_trusted_asset_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(str(value or ""))
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (
        host.endswith(".dingtalk.com")
        or host.endswith(".dingtalk.cn")
        or host.endswith(".alicdn.com")
        or host.endswith(".aliyuncs.com")
    )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def read_limited_response(response: Any, max_bytes: int = MAX_RESOURCE_BYTES) -> bytes:
    try:
        declared_size = int(str(response.headers.get("Content-Length") or "0") or "0")
    except ValueError:
        declared_size = 0
    if declared_size > max_bytes:
        raise ExportError(f"资源超过大小限制（{max_bytes // 1024 // 1024} MB）")
    raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ExportError(f"资源超过大小限制（{max_bytes // 1024 // 1024} MB）")
    return raw


def download_direct_asset(source: str, timeout: int = ASSET_REQUEST_TIMEOUT_SECONDS) -> tuple[bytes, str]:
    """Fetch a signed DingTalk/OSS asset when browser fetch is blocked by CORS."""
    current = source
    opener = urllib.request.build_opener(NoRedirect())
    for _ in range(4):
        if not is_trusted_asset_url(current):
            raise ExportError("资源跳转到了不受信任的域名")
        request = urllib.request.Request(
            current,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://alidocs.dingtalk.com/",
            },
        )
        try:
            with opener.open(request, timeout=timeout) as response:
                return read_limited_response(response), str(response.headers.get("Content-Type") or "")
        except urllib.error.HTTPError as exc:
            if exc.code not in {301, 302, 303, 307, 308}:
                raise ExportError(f"HTTP {exc.code}") from exc
            location = str(exc.headers.get("Location") or "")
            if not location:
                raise ExportError(f"HTTP {exc.code} 未提供跳转地址") from exc
            current = urllib.parse.urljoin(current, location)
    raise ExportError("资源重定向次数过多")


def download_direct_asset_with_retry(source: str) -> tuple[bytes, str]:
    """Retry only a small number of transient TLS/read failures for signed assets."""
    last_error: Exception | None = None
    for attempt in range(ASSET_DIRECT_RETRIES):
        try:
            return download_direct_asset(source)
        except Exception as exc:  # noqa: BLE001 - preserve the final reason in the resource report.
            last_error = exc
            if attempt + 1 < ASSET_DIRECT_RETRIES:
                time.sleep(0.8 * (attempt + 1))
    assert last_error is not None
    raise last_error


def js_string(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def parse_dingtalk_url(value: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(str(value or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in SUPPORTED_HOSTS:
        raise ExportError("请输入 docs.dingtalk.com 或 alidocs.dingtalk.com 的钉钉文档/文件夹链接。")
    return parsed


def parse_source_url(value: str) -> str:
    parsed = parse_dingtalk_url(value)
    match = re.search(r"/i/nodes/([^/?#]+)", parsed.path)
    if not match:
        raise ExportError("链接中未找到钉钉文档标识。请复制包含 /i/nodes/ 的文档/文件夹链接，或 /i/spaces/.../overview 的知识库根链接。")
    node_id = urllib.parse.unquote(match.group(1)).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,200}", node_id):
        raise ExportError("钉钉目录标识格式无效。")
    return node_id


def parse_space_url(value: str) -> str | None:
    parsed = parse_dingtalk_url(value)
    match = re.fullmatch(r"/i/spaces/([A-Za-z0-9_-]{4,200})/overview/?", parsed.path)
    return match.group(1) if match else None


def page_for_dingtalk(port: int, preferred_url: str = "") -> dict[str, Any] | None:
    pages = http_json(f"http://127.0.0.1:{port}/json/list", timeout=5)
    preferred = urllib.parse.urlparse(preferred_url)
    preferred_host = (preferred.hostname or "").lower()
    preferred_path = preferred.path.rstrip("/")
    if preferred_host in SUPPORTED_HOSTS:
        for page in pages:
            url = urllib.parse.urlparse(str(page.get("url") or ""))
            if page.get("type") != "page" or (url.hostname or "").lower() != preferred_host:
                continue
            if preferred_path and url.path.rstrip("/") == preferred_path:
                return page
    for page in pages:
        url = str(page.get("url") or "")
        if page.get("type") == "page" and any(host in url for host in SUPPORTED_HOSTS):
            return page
    return None


def open_dingtalk_target(cdp: CDPClient, target_url: str) -> None:
    """Navigate an existing plugin browser to the requested DingTalk page."""
    expected_host = (parse_dingtalk_url(target_url).hostname or "").lower()
    cdp.navigate(target_url)
    state_expression = "({ host: location.hostname, readyState: document.readyState })"
    for _ in range(30):
        state = cdp.evaluate(state_expression, timeout=5)
        if isinstance(state, dict) and str(state.get("host") or "").lower() == expected_host:
            if str(state.get("readyState") or "") in {"interactive", "complete"}:
                return
        time.sleep(0.5)
    raise ExportError("钉钉目标页面打开超时，请确认浏览器中能正常打开该链接后重试。")


def connect_dingtalk_browser(args: argparse.Namespace, initial_url: str = ENTRY_URL) -> tuple[CDPClient, subprocess.Popen[Any] | None]:
    process: subprocess.Popen[Any] | None = None
    if not chrome_debug_available(args.port):
        profile = Path(args.profile_dir).expanduser().resolve() if args.profile_dir else default_profile_path()
        process = start_chrome(args.port, profile, initial_url, getattr(args, "browser_path", "") or None)
        wait_for_debug_port(args.port, timeout=30)
    page = page_for_dingtalk(args.port, initial_url)
    if not page:
        open_tab(args.port, initial_url)
        time.sleep(1)
        page = page_for_dingtalk(args.port, initial_url)
    if not page:
        pages = http_json(f"http://127.0.0.1:{args.port}/json/list", timeout=5)
        page = next((item for item in pages if item.get("type") == "page"), None)
    if not page or not page.get("webSocketDebuggerUrl"):
        raise ExportError("无法找到或创建钉钉网页标签页。")
    client = CDPClient(str(page["webSocketDebuggerUrl"]))
    client.connect()
    client.send("Runtime.enable")
    client.send("Page.enable")
    # A previous login can leave several DingTalk tabs open.  Always move the
    # selected CDP page to the URL the user entered instead of querying an old
    # login/home tab and waiting for its requests to time out.
    open_dingtalk_target(client, initial_url)
    return client, process


# Kept in page memory only. It never serializes accessToken/cookies to Python logs.
DINGTALK_HELPER_JS = r"""
(() => {
  // Bump this protocol whenever the helper behavior changes.  The plugin
  // browser stays alive between actions, so keeping the old helper version
  // would silently bypass a newly installed timeout or safety fix.
  if (window.__wandaoDingTalk && window.__wandaoDingTalk.version === 4) return true;
  const base = location.origin;
  const fetchWithTimeout = async (url, options = {}, timeoutMs = 45000) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, Object.assign({}, options, { signal: controller.signal }));
    } catch (error) {
      if (error && error.name === 'AbortError') throw new Error(`请求超时（${Math.ceil(timeoutMs / 1000)} 秒）`);
      throw error;
    } finally {
      clearTimeout(timer);
    }
  };
  const readArrayBufferWithTimeout = async (response, timeoutMs = 15000) => {
    let timer = null;
    try {
      return await Promise.race([
        response.arrayBuffer(),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`图片读取超时（${Math.ceil(timeoutMs / 1000)} 秒）`)), timeoutMs); })
      ]);
    } finally {
      if (timer) clearTimeout(timer);
    }
  };
  const requestJson = async (path, options = {}, timeoutMs = 45000) => {
    const tokenResponse = await fetchWithTimeout('/portal/api/v1/token/getAccessToken', { method: 'POST', credentials: 'include' }, 30000);
    const tokenPayload = await tokenResponse.json().catch(() => ({}));
    const token = tokenPayload && tokenPayload.data && tokenPayload.data.accessToken;
    if (!token) throw new Error('钉钉登录已失效，请重新登录并保存会话。');
    const headers = Object.assign({}, options.headers || {}, { 'A-Token': token });
    let corpId = (document.cookie.match(/(?:^|;\s*)portal_corp_id=([^;]+)/) || [])[1] || '';
    if (!corpId) {
      const userResponse = await fetchWithTimeout('/api/users/getUserInfo', { method: 'POST', credentials: 'include', headers }, 30000);
      const userPayload = await userResponse.json().catch(() => ({}));
      const orgs = (userPayload.data && (userPayload.data.orgs || userPayload.data.orgDTOList)) || [];
      const main = orgs.find((item) => item && item.isMainOrg) || orgs[0] || {};
      corpId = main.corpId || main.id || '';
    }
    if (corpId) headers['corp-id'] = decodeURIComponent(corpId);
    const response = await fetchWithTimeout(base + path, Object.assign({ credentials: 'include' }, options, { headers }), timeoutMs);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.isSuccess) {
      const message = (payload && (payload.message || payload.errorMessage || payload.errorCode)) || `HTTP ${response.status}`;
      throw new Error(String(message));
    }
    return payload.data || {};
  };
  const api = {
    version: 4,
    profile: async () => {
      const data = await requestJson('/api/users/getUserInfo', { method: 'POST' });
      const user = data.user || {};
      return { displayName: user.nick || user.name || user.userName || '', loggedIn: true };
    },
    info: (uuid) => requestJson('/box/api/v2/dentry/info?dentryUuid=' + encodeURIComponent(uuid), { method: 'GET' }),
    children: (uuid, cursor) => {
      let path = '/box/api/v2/dentry/list?pageSize=100&dentryUuid=' + encodeURIComponent(uuid);
      if (cursor) path += '&loadMoreId=' + encodeURIComponent(cursor);
      return requestJson(path, { method: 'GET' });
    },
    content: (dentryKey, docKey) => requestJson('/api/document/data', {
      method: 'POST',
      headers: { 'a-dentry-key': dentryKey || '', 'a-doc-key': docKey || '', 'Content-Type': 'application/json;charset=UTF-8' },
      body: JSON.stringify({ dentryKey, pageMode: 2, fetchBody: true })
    }, 45000),
    asset: async (url) => {
      const response = await fetchWithTimeout(url, { credentials: 'include' }, 15000);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const buffer = await readArrayBufferWithTimeout(response, 15000);
      const bytes = new Uint8Array(buffer);
      let binary = '';
      for (let index = 0; index < bytes.length; index += 32768) binary += String.fromCharCode(...bytes.subarray(index, index + 32768));
      return { base64: btoa(binary), contentType: response.headers.get('content-type') || '', finalUrl: response.url || url };
    }
  };
  window.__wandaoDingTalk = api;
  return true;
})()
"""


def install_helper(cdp: CDPClient) -> None:
    cdp.evaluate(DINGTALK_HELPER_JS, timeout=30)


def call_helper(cdp: CDPClient, method: str, *args: Any, timeout: int = 60) -> Any:
    install_helper(cdp)
    expression = f"window.__wandaoDingTalk[{js_string(method)}](...{js_string(list(args))})"
    result = cdp.evaluate(expression, timeout=timeout)
    if not isinstance(result, dict):
        raise ExportError("钉钉网页没有返回有效数据，请重新登录后重试。")
    return result


def resolve_space_root_uuid(cdp: CDPClient, source_url: str) -> str:
    """Read the root dentry UUID exposed by a DingTalk space overview page."""
    cdp.evaluate("performance.clearResourceTimings()")
    cdp.navigate(source_url)
    expression = r"""
(() => {
  const resources = performance.getEntriesByType('resource');
  for (const entry of resources) {
    try {
      const url = new URL(entry.name);
      if (url.pathname !== '/box/api/v2/dentry/list') continue;
      if (!url.searchParams.has('withParentAncestors')) continue;
      const root = url.searchParams.get('dentryUuid') || '';
      if (/^[A-Za-z0-9_-]{4,200}$/.test(root)) return root;
    } catch (_) { /* Ignore unrelated resource entries. */ }
  }
  return '';
})()
"""
    for _ in range(20):
        root = str(cdp.evaluate(expression, timeout=10) or "")
        if root:
            return root
        time.sleep(0.5)
    raise ExportError("未能从钉钉知识库根页面读取目录。请确认已登录并能在浏览器中看到左侧目录后重试。")


def entry_from_raw(raw: dict[str, Any], parent_uuid: str = "") -> DingEntry:
    uuid = str(raw.get("dentryUuid") or raw.get("dentryId") or "")
    if not uuid:
        raise ExportError("钉钉目录返回了缺少 dentryUuid 的项目。")
    content_type = str(raw.get("contentType") or "").lower()
    dentry_type = str(raw.get("dentryType") or "").lower()
    return DingEntry(
        uuid=uuid,
        key=str(raw.get("dentryKey") or ""),
        doc_key=str(raw.get("docKey") or ""),
        title=str(raw.get("name") or raw.get("title") or uuid),
        content_type=content_type,
        parent_uuid=str(raw.get("parentDentryUuid") or parent_uuid or ""),
        is_folder=dentry_type == "folder" or content_type == "folder",
        has_children=bool(raw.get("hasChildren")) or dentry_type == "folder",
    )


def root_entry(cdp: CDPClient, source_url: str, args: argparse.Namespace) -> DingEntry:
    """Resolve the export root without silently widening a document export.

    A document URL normally means "only this document".  Some DingTalk
    knowledge-base views, however, only expose document URLs even when the
    user wants a folder or the whole knowledge base.  The non-default scopes
    are explicit opt-ins so that scanning/exporting never widens by surprise.
    """
    selected_id = resolve_space_root_uuid(cdp, source_url) if parse_space_url(source_url) else parse_source_url(source_url)
    selected = entry_from_raw(call_helper(cdp, "info", selected_id))
    scope = str(getattr(args, "document_scope", "selected") or "selected")
    if scope in {"parent-folder", "library-root"} and not selected.is_folder and selected.parent_uuid:
        root = entry_from_raw(call_helper(cdp, "info", selected.parent_uuid))
        if root.is_folder:
            if scope == "library-root":
                visited = {selected.uuid, root.uuid}
                while root.parent_uuid and root.parent_uuid not in visited:
                    parent = entry_from_raw(call_helper(cdp, "info", root.parent_uuid))
                    if not parent.is_folder:
                        break
                    visited.add(parent.uuid)
                    root = parent
            return replace(root, parent_uuid="")
    # The selected URL is the export root, so do not leave it orphaned in the TOC.
    return replace(selected, parent_uuid="")


def children_of(cdp: CDPClient, entry: DingEntry, args: argparse.Namespace) -> list[DingEntry]:
    cursor = ""
    seen_cursors: set[str] = set()
    page_number = 0
    children: list[DingEntry] = []
    while True:
        check_stopped(args)
        page_number += 1
        emit(args, f"正在读取钉钉目录第 {page_number} 页…", event="toc.page")
        throttle_request(args)
        payload = call_helper(cdp, "children", entry.uuid, cursor)
        raw_children = payload.get("children") or []
        if not isinstance(raw_children, list):
            raise ExportError("钉钉目录接口返回的 children 不是列表。")
        children.extend(entry_from_raw(item, entry.uuid) for item in raw_children if isinstance(item, dict))
        next_cursor = str(payload.get("loadMoreId") or payload.get("nextLoadMoreId") or "")
        if not next_cursor:
            return children
        if next_cursor in seen_cursors:
            emit(args, "钉钉目录分页返回了重复游标，已停止重复读取。", event="toc.pagination.repeated", level="warn")
            return children
        seen_cursors.add(next_cursor)
        cursor = next_cursor


def collect_tree(cdp: CDPClient, source_url: str, args: argparse.Namespace) -> list[DingEntry]:
    emit(args, "正在读取钉钉目录根节点…", event="toc.root")
    root = root_entry(cdp, source_url, args)
    result: list[DingEntry] = [root]
    visited = {root.uuid}
    pending = [root]
    processed_folders = 0
    while pending:
        parent = pending.pop(0)
        if not parent.has_children:
            continue
        processed_folders += 1
        for child in children_of(cdp, parent, args):
            if child.uuid in visited:
                continue
            visited.add(child.uuid)
            result.append(child)
            if child.has_children:
                pending.append(child)
        if processed_folders == 1 or processed_folders % 10 == 0:
            emit(args, f"钉钉目录读取中：已检查 {processed_folders} 个目录节点，已发现 {len(result)} 个节点。", event="toc.progress")
    return result


def toc_json(entries: list[DingEntry]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    for entry in entries:
        selectable = not entry.is_folder and entry.content_type in SUPPORTED_DOCUMENT_TYPES
        nodes.append(
            {
                "nodeId": f"dingtalk:{entry.uuid}",
                "exportId": entry.uuid if selectable else "",
                "title": entry.title,
                "parentNodeId": f"dingtalk:{entry.parent_uuid}" if entry.parent_uuid else "",
                "selectable": selectable,
                "contentType": entry.content_type,
            }
        )
    return {"provider": "dingtalk-export", "nodes": nodes}


def escape_markdown_text(value: Any) -> str:
    return str(value or "").replace("\r", "").replace("\u00a0", " ")


class DingMarkdownRenderer:
    def __init__(self, source_url: str) -> None:
        self.source_url = source_url
        self.images: list[ImageRef] = []
        self.warnings: list[str] = []
        self._list_counters: dict[tuple[str, int], int] = {}

    def render_document(self, document: dict[str, Any], title: str) -> str:
        parts = document.get("parts") or {}
        word_part = next(
            (
                item for item in parts.values()
                if isinstance(item, dict) and item.get("type") == "application/x-alidocs-word"
            ),
            None,
        )
        body = ((word_part or {}).get("data") or {}).get("body")
        if not isinstance(body, list):
            raise ExportError("当前项目不是可转换的普通钉钉文档，或钉钉返回的正文结构已变化。")
        content = self.render_frame(body).strip()
        heading = f"# {escape_markdown_text(title).strip() or '未命名文档'}"
        return f"{heading}\n\n{content}\n" if content else f"{heading}\n"

    def render_frame(self, frame: Any) -> str:
        if isinstance(frame, str):
            return escape_markdown_text(frame)
        if not isinstance(frame, list) or not frame:
            return ""
        tag = str(frame[0] or "")
        attrs = frame[1] if len(frame) > 1 and isinstance(frame[1], dict) else {}
        children = frame[2:] if len(frame) > 2 else []
        content = "".join(self.render_frame(item) for item in children)
        if tag in {"root", "span", "tc", "container"}:
            return self.apply_inline_style(content, attrs)
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            # DingTalk can attach list metadata to a heading (for example the
            # visually prominent bullet items in a knowledge-base guide).  A
            # previous renderer checked headings before list metadata and
            # silently discarded those bullets.
            prefix = self.list_prefix(attrs)
            return f"{prefix}{'#' * int(tag[1:])} {self.apply_inline_style(content.strip(), attrs)}\n\n"
        if tag == "p":
            prefix = self.list_prefix(attrs)
            return f"{prefix}{self.apply_inline_style(content.strip(), attrs)}\n\n" if content.strip() else ""
        if tag == "br":
            return "<br>"
        if tag in {"inlineCode", "cangjie-textinline"}:
            escaped = content.replace("`", "\\`")
            return f"`{escaped}`"
        if tag == "code":
            language = str(attrs.get("syntax") or "")
            return f"```{language}\n{content.strip()}\n```\n\n"
        if tag == "a":
            href = str(attrs.get("href") or "").strip()
            return f"[{content or href}]({href})" if href else content
        if tag == "img":
            source = str(attrs.get("src") or "").strip()
            alt = str(attrs.get("name") or attrs.get("alt") or "图片")
            if source:
                self.images.append(ImageRef(source=source, alt=alt))
                return f"![{alt}]({source})"
            return ""
        if tag == "hr":
            return "---\n\n"
        if tag == "table":
            return self.render_table(children)
        if tag in {"card", "embed", "attachment", "video"}:
            self.warn_once(f"发现暂不支持的钉钉结构：{tag}")
            return f"[钉钉 {tag} 内容请查看原文]({self.source_url})\n\n"
        if tag in {"tr", "td", "th"}:
            return content
        if tag:
            self.warn_once(f"发现暂不支持的钉钉结构：{tag}")
        return content

    def apply_inline_style(self, content: str, attrs: dict[str, Any]) -> str:
        if not content:
            return ""
        value = content
        if attrs.get("bold"):
            value = f"**{value}**"
        if attrs.get("italic"):
            value = f"*{value}*"
        if attrs.get("strike"):
            value = f"~~{value}~~"
        return value

    def list_prefix(self, attrs: dict[str, Any]) -> str:
        details = attrs.get("list")
        if not isinstance(details, dict):
            return ""
        level = max(0, int(details.get("level") or 0))
        indent = "    " * level
        if details.get("isTaskList"):
            return f"{indent}- [{'x' if details.get('isChecked') else ' '}] "
        if details.get("isOrdered"):
            key = (str(details.get("listId") or "default"), level)
            number = self._list_counters.get(key, 0) + 1
            self._list_counters[key] = number
            return f"{indent}{number}. "
        return f"{indent}- "

    def render_table(self, rows: list[Any]) -> str:
        rendered_rows: list[list[str]] = []
        for row in rows:
            if not isinstance(row, list) or not row or row[0] != "tr":
                continue
            cells = []
            for cell in row[2:]:
                if isinstance(cell, list):
                    text = self.render_frame(cell).replace("\n", " ").replace("|", "\\|").strip()
                    cells.append(text)
            if cells:
                rendered_rows.append(cells)
        if not rendered_rows:
            return ""
        width = max(len(row) for row in rendered_rows)
        normalized = [row + [""] * (width - len(row)) for row in rendered_rows]
        lines = [f"| {' | '.join(normalized[0])} |", f"| {' | '.join(['---'] * width)} |"]
        lines.extend(f"| {' | '.join(row)} |" for row in normalized[1:])
        return "\n".join(lines) + "\n\n"

    def warn_once(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)


def document_payload(cdp: CDPClient, entry: DingEntry, args: argparse.Namespace) -> dict[str, Any]:
    if not entry.key or not entry.doc_key:
        fresh = entry_from_raw(call_helper(cdp, "info", entry.uuid))
        entry = fresh
    if not entry.key:
        raise ExportError("钉钉文档缺少 dentryKey，无法读取正文。")
    throttle_request(args)
    return call_helper(cdp, "content", entry.key, entry.doc_key, timeout=DOCUMENT_REQUEST_TIMEOUT_SECONDS + 10)


def asset_extension(content_type: str, source: str) -> str:
    clean_type = content_type.split(";", 1)[0].strip().lower()
    guessed = mimetypes.guess_extension(clean_type) or ""
    if guessed == ".jpe":
        guessed = ".jpg"
    if guessed:
        return guessed
    suffix = Path(urllib.parse.urlparse(source).path).suffix.lower()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else ".bin"


def save_image(
    raw: bytes,
    content_type: str,
    source: str,
    assets_dir: Path,
    index: int,
    asset_prefix: str = "",
) -> str:
    extension = asset_extension(content_type, source)
    prefix = f"{safe_path_segment(asset_prefix, 'document', 32)}-" if asset_prefix else ""
    filename = f"{prefix}image-{index:03d}{extension}"
    assets_dir.mkdir(parents=True, exist_ok=True)
    target = assets_dir / filename
    target.write_bytes(raw)
    return f"assets/{markdown_path(filename)}"


def download_image_in_browser(cdp: CDPClient, source: str) -> tuple[bytes, str]:
    """Load a private/cross-origin image in Chrome, then read its CDP response body.

    ``fetch`` is subject to the image host's CORS policy. An ``Image`` element
    uses the same authenticated browser request path as the normal document
    renderer; DevTools can then read the completed response without weakening
    the URL allowlist.
    """
    cdp.send("Network.enable")
    cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
    try:
        expression = (
            "new Promise((resolve, reject) => {"
            "const image = new Image();"
            "image.onload = () => { image.remove(); resolve(true); };"
            "image.onerror = () => { image.remove(); reject(new Error('image load failed')); };"
            f"image.src = {js_string(source)};"
            "})"
        )
        cdp.evaluate(expression, timeout=ASSET_REQUEST_TIMEOUT_SECONDS + 5)
        event = cdp.wait_for_event(
            "Network.responseReceived",
            timeout=10,
            predicate=lambda message: str(message.get("params", {}).get("response", {}).get("url") or "") == source,
        )
        request_id = str(event.get("params", {}).get("requestId") or "")
        if not request_id:
            raise ExportError("浏览器图片响应缺少请求标识。")
        response = event.get("params", {}).get("response", {})
        headers = response.get("headers") if isinstance(response, dict) else {}
        content_type = ""
        if isinstance(headers, dict):
            content_type = next((str(value) for key, value in headers.items() if str(key).lower() == "content-type"), "")
        body_response = cdp.send("Network.getResponseBody", {"requestId": request_id}, timeout=ASSET_REQUEST_TIMEOUT_SECONDS + 10)
        result = body_response.get("result", {})
        body = result.get("body") if isinstance(result, dict) else ""
        if not isinstance(body, str) or not body:
            raise ExportError("浏览器图片响应为空")
        raw = base64.b64decode(body, validate=True) if result.get("base64Encoded") else body.encode("utf-8")
        if not raw:
            raise ExportError("图片响应为空")
        return raw, content_type
    finally:
        cdp.send("Network.setCacheDisabled", {"cacheDisabled": False})


def rewrite_images(
    cdp: CDPClient,
    rendered: RenderResult,
    markdown: str,
    md_path: Path,
    args: argparse.Namespace,
    asset_prefix: str = "",
) -> tuple[str, list[dict[str, str]], int]:
    failures: list[dict[str, str]] = []
    saved = 0
    assets_dir = md_path.parent / "assets"
    replacements: dict[str, str] = {}
    unique_images: list[ImageRef] = []
    seen_sources: set[str] = set()
    for image in rendered.images:
        if image.source not in seen_sources:
            seen_sources.add(image.source)
            unique_images.append(image)
    direct_jobs: dict[int, tuple[ImageRef, Any]] = {}
    with ThreadPoolExecutor(max_workers=ASSET_DOWNLOAD_WORKERS, thread_name_prefix="wandao-dingtalk-asset") as pool:
        for index, image in enumerate(unique_images, start=1):
            if not is_trusted_asset_url(image.source):
                continue
            check_stopped(args)
            throttle_request(args)
            direct_jobs[index] = (image, pool.submit(download_direct_asset_with_retry, image.source))
        for index, image in enumerate(unique_images, start=1):
            emit(args, f"正在下载钉钉图片：{index}/{len(unique_images)}", event="asset.download.started", progress={"current": index, "total": len(unique_images)})
            if not is_trusted_asset_url(image.source):
                replacements[image.source] = image.source
                failures.append({"url": safe_resource_url(image.source), "error": "图片地址不在受信任的钉钉资源域名中"})
                continue
            _, job = direct_jobs[index]
            try:
                raw, content_type = job.result()
            except Exception as direct_exc:
                try:
                    raw, content_type = download_image_in_browser(cdp, image.source)
                except Exception as browser_exc:  # noqa: BLE001 - a resource failure should not discard its document.
                    replacements[image.source] = image.source
                    failures.append({
                        "url": safe_resource_url(image.source),
                        "error": f"受限直连下载失败：{direct_exc}；浏览器读取失败：{browser_exc}",
                    })
                    continue
            replacements[image.source] = save_image(raw, content_type, image.source, assets_dir, index, asset_prefix)
            saved += 1
    for source, local_path in replacements.items():
        markdown = markdown.replace(f"]({source})", f"]({local_path})")
    return markdown, failures, saved


def relative_document_path(entries: dict[str, DingEntry], entry: DingEntry, output: Path) -> Path:
    segments = [safe_path_segment(entry.title) + ".md"]
    parent_uuid = entry.parent_uuid
    seen: set[str] = {entry.uuid}
    while parent_uuid and parent_uuid not in seen:
        seen.add(parent_uuid)
        parent = entries.get(parent_uuid)
        if not parent:
            break
        if parent.is_folder:
            segments.append(safe_path_segment(parent.title))
        parent_uuid = parent.parent_uuid
    return output.joinpath(*reversed(segments))


def select_entries(entries: list[DingEntry], selected_ids: set[str]) -> list[DingEntry]:
    documents = [entry for entry in entries if not entry.is_folder and entry.content_type in SUPPORTED_DOCUMENT_TYPES]
    if not selected_ids:
        return documents
    selected = [entry for entry in documents if entry.uuid in selected_ids]
    if documents and not selected:
        preview = ", ".join(sorted(selected_ids)[:5])
        raise ExportError(f"所选钉钉文档不在当前目录中，请重新读取目录后再试。未匹配 ID：{preview}")
    return selected


def save_auth_summary(args: argparse.Namespace, cdp: CDPClient) -> dict[str, Any]:
    profile = call_helper(cdp, "profile")
    auth_file = auth_path_from_args(args)
    write_private_json(
        auth_file,
        {
            "version": 1,
            "savedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "profileDir": str(Path(args.profile_dir).expanduser().resolve() if args.profile_dir else default_profile_path()),
            "displayName": str(profile.get("displayName") or ""),
            "loggedIn": True,
        },
    )
    return {"authFile": str(auth_file), "displayName": str(profile.get("displayName") or "")}


def run_login(args: argparse.Namespace) -> dict[str, Any]:
    cdp, process = connect_dingtalk_browser(args)
    try:
        cdp.navigate(ENTRY_URL)
        emit(args, "请在浏览器中完成钉钉登录；登录成功后回到万能导确认保存会话。")
        wait_seconds = max(0, int(getattr(args, "login_wait_seconds", 0) or 0))
        if wait_seconds:
            deadline = time.time() + wait_seconds
            last_error = ""
            while time.time() < deadline:
                try:
                    return save_auth_summary(args, cdp)
                except Exception as exc:  # noqa: BLE001 - user may still be completing login.
                    last_error = str(exc)
                    time.sleep(1)
            raise ExportError(last_error or "未检测到钉钉登录状态。")
        # Do not pass a prompt to input(). In the desktop app stdout also
        # carries structured logs and the final JSON result; Python prints an
        # input prompt without a trailing newline, which would join the prompt
        # to that result and make the host reject a successful login.
        input()
        check_stopped(args)
        summary = save_auth_summary(args, cdp)
        return finalize_report(
            {"platform": "dingtalk", "loggedIn": True, **summary},
            provider="dingtalk-export",
            mode="login",
        )
    finally:
        cdp.close()
        if process and args.close_started_chrome:
            process.terminate()


def scan_dingtalk(args: argparse.Namespace) -> dict[str, Any]:
    emit(args, "正在打开钉钉目标页面并读取目录…", event="toc.started")
    cdp, process = connect_dingtalk_browser(args, args.source_url or ENTRY_URL)
    try:
        result = toc_json(collect_tree(cdp, args.source_url, args))
        emit(args, f"钉钉目录读取完成：共 {len(result['nodes'])} 个节点。", event="toc.completed")
        return result
    finally:
        cdp.close()
        if process and args.close_started_chrome:
            process.terminate()


def export_dingtalk(args: argparse.Namespace) -> dict[str, Any]:
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    started = time.time()
    checkpoint = open_checkpoint_from_args(args, "dingtalk-export", "export")
    cdp, process = connect_dingtalk_browser(args, args.source_url or ENTRY_URL)
    try:
        entries = collect_tree(cdp, args.source_url, args)
        by_uuid = {entry.uuid: entry for entry in entries}
        documents = select_entries(entries, set(args.selected_doc_ids or []))
        paths = {entry.uuid: relative_document_path(by_uuid, entry, output) for entry in documents}
        if checkpoint:
            checkpoint.start_task({"source": args.source_url, "outputDir": str(output), "totalDocs": len(documents), "resume": bool(args.resume)})
            for entry in documents:
                checkpoint.upsert_item(
                    f"dingtalk:doc:{entry.uuid}",
                    title=entry.title,
                    source_url=args.source_url,
                    source_id=entry.uuid,
                    parent_key=entry.parent_uuid,
                    metadata={"dentryUuid": entry.uuid, "contentType": entry.content_type},
                )
            if args.retry_failed:
                documents = [entry for entry in documents if checkpoint.item_status(f"dingtalk:doc:{entry.uuid}") == "failed"]
        total = len(documents)
        exported = skipped = image_success = 0
        failures: list[dict[str, str]] = []
        image_failures: list[dict[str, str]] = []
        emit(args, f"开始导出钉钉文档：共 {total} 篇。", event="task.started", totals={"documents": total}, output=str(output))
        for index, entry in enumerate(documents, start=1):
            check_stopped(args)
            item_key = f"dingtalk:doc:{entry.uuid}"
            md_path = paths[entry.uuid]
            try:
                if checkpoint and args.resume and checkpoint.item_status(item_key) == "completed":
                    skipped += 1
                    continue
                if args.incremental and md_path.exists() and not args.retry_failed:
                    if checkpoint:
                        checkpoint.complete_item(item_key, local_path=str(md_path), metadata={"skippedExisting": True})
                    skipped += 1
                    continue
                if checkpoint:
                    checkpoint.start_item(item_key, "content")
                emit(args, f"开始导出钉钉文档：{entry.title}", event="document.export.started", doc={"id": entry.uuid, "title": entry.title, "index": index, "path": str(md_path)})
                emit(args, f"正在读取钉钉正文：{entry.title}", event="document.content.started", doc={"id": entry.uuid, "title": entry.title, "index": index})
                payload = document_payload(cdp, entry, args)
                raw_content = ((payload.get("documentContent") or {}).get("checkpoint") or {}).get("content")
                if not raw_content:
                    raise ExportError("钉钉没有返回正文内容。")
                document = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
                renderer = DingMarkdownRenderer(args.source_url)
                markdown = renderer.render_document(document, entry.title)
                rendered = RenderResult(markdown=markdown, images=renderer.images, warnings=renderer.warnings)
                markdown, resource_errors, saved = rewrite_images(
                    cdp,
                    rendered,
                    markdown,
                    md_path,
                    args,
                    asset_prefix=safe_path_segment(entry.uuid, "document", 32),
                )
                md_path.parent.mkdir(parents=True, exist_ok=True)
                md_path.write_text(markdown, encoding="utf-8")
                image_success += saved
                for failure in resource_errors:
                    image_failures.append({"docUuid": entry.uuid, "title": entry.title, **failure})
                if checkpoint:
                    if resource_errors:
                        checkpoint.fail_item(item_key, f"{len(resource_errors)} 个图片下载失败")
                    else:
                        checkpoint.complete_item(item_key, local_path=str(md_path), metadata={"images": saved, "warnings": renderer.warnings})
                exported += 1
                emit(args, f"钉钉文档导出完成：{entry.title}", event="document.export.completed", doc={"id": entry.uuid, "title": entry.title, "index": index, "path": str(md_path)}, stats={"imageSuccessInDoc": saved, "imageFailuresInDoc": len(resource_errors)})
            except ExportStopped:
                if checkpoint:
                    checkpoint.fail_item(item_key, "stopped")
                raise
            except Exception as exc:  # noqa: BLE001 - continue so the report includes every failed document.
                if checkpoint:
                    checkpoint.fail_item(item_key, str(exc))
                failures.append({"docUuid": entry.uuid, "title": entry.title, "error": str(exc)})
                emit(args, f"钉钉文档导出失败：{entry.title}：{exc}", event="document.export.failed", level="error", doc={"id": entry.uuid, "title": entry.title, "index": index}, error={"type": type(exc).__name__, "message": str(exc)})
            if index % max(1, args.progress_every) == 0 or index == total:
                emit(args, f"progress {index}/{total} exported={exported} skipped={skipped} image_success={image_success} failures={len(failures)}", event="task.progress", progress={"current": index, "total": total}, stats={"exportedDocs": exported, "skippedDocs": skipped, "imageSuccess": image_success, "failureCount": len(failures), "imageFailureCount": len(image_failures)})
        report_path = output / "00-导出报告.json"
        report = finalize_report(
            {
                "platform": "dingtalk",
                "total": total,
                "exported": exported,
                "skipped": skipped,
                "imageSuccess": image_success,
                "failures": failures,
                "imageFailures": image_failures,
                "elapsedSeconds": round(time.time() - started, 2),
                "checkpoint": checkpoint.stats() if checkpoint else {},
            },
            provider="dingtalk-export",
            mode="export",
            report_file=report_path,
            output=output,
        )
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if checkpoint:
            if failures or image_failures:
                checkpoint.fail_task(f"{len(failures)} 个文档失败，{len(image_failures)} 个图片失败", status="failed")
            else:
                checkpoint.complete_task(report)
        emit(args, "钉钉文档导出完成" if not failures else f"钉钉文档导出完成，但有 {len(failures)} 个失败项", event="task.completed", level="success" if not failures and not image_failures else "warn", reportFile=str(report_path), stats={"exportedDocs": exported, "skippedDocs": skipped, "imageSuccess": image_success, "failureCount": len(failures), "imageFailureCount": len(image_failures)})
        return report
    finally:
        cdp.close()
        if process and args.close_started_chrome:
            process.terminate()
        if checkpoint:
            checkpoint.close()


def load_doc_id_file(args: argparse.Namespace) -> None:
    try:
        extend_arg_list_from_file(args, "selected_doc_ids")
    except (FileNotFoundError, ValueError) as exc:
        raise ExportError(str(exc)) from exc


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出钉钉普通文档为 Markdown")
    parser.add_argument("--gui", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--login", action="store_true", help="打开钉钉网页并检查登录会话")
    parser.add_argument("--login-wait-seconds", type=int, default=0, help="非交互登录时等待保存会话的秒数")
    parser.add_argument("--scan-toc", action="store_true", help="读取目录树并输出 JSON")
    parser.add_argument("--source-url", default="", help="钉钉文档或文件夹链接")
    parser.add_argument(
        "--document-scope",
        choices=("selected", "parent-folder", "library-root"),
        default="selected",
        help="文档链接的目录范围：selected 只导出当前文档，parent-folder 导出所在文件夹，library-root 导出最上级文件夹",
    )
    parser.add_argument("--output", default=str(default_data_dir() / "exports" / "dingtalk"), help="输出目录")
    parser.add_argument("--doc-id", action="append", dest="selected_doc_ids", default=[], help="只导出指定 dentryUuid，可重复")
    parser.add_argument("--doc-id-file", default="", help="从 JSON 数组或逐行文件读取文档 ID")
    parser.add_argument("--incremental", action="store_true", help="目标 Markdown 已存在时跳过")
    add_checkpoint_args(parser)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Chrome 调试端口")
    parser.add_argument("--profile-dir", default=str(default_profile_path()), help="钉钉专用浏览器配置目录")
    parser.add_argument("--browser-path", default="", help="Chrome/Edge 可执行文件路径")
    parser.add_argument("--auth-file", default=str(default_auth_path()), help="登录会话摘要文件（不含 Cookie/Token）")
    parser.add_argument("--progress-every", type=int, default=1, help="每处理多少篇输出一次进度")
    parser.add_argument("--request-delay", type=float, default=0.3, help="请求延迟秒")
    parser.add_argument("--request-jitter", type=float, default=0.2, help="请求随机浮动秒")
    parser.add_argument("--close-started-chrome", action="store_true", help="任务结束后关闭本插件启动的浏览器")
    args = parser.parse_args(argv)
    load_doc_id_file(args)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.login:
            print(json.dumps(run_login(args), ensure_ascii=False, indent=2))
            return 0
        if args.scan_toc:
            if not args.source_url:
                raise ExportError("读取目录前请先填写钉钉文档或文件夹链接。")
            print(json.dumps(scan_dingtalk(args), ensure_ascii=False, indent=2))
            return 0
        if not args.source_url:
            raise ExportError("导出前请先填写钉钉文档或文件夹链接。")
        result = export_dingtalk(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not result.get("failures") else 1
    except ExportStopped as exc:
        emit(args, f"钉钉文档导出已停止：{exc}", event="task.stopped", level="warn")
        print(str(exc), file=sys.stderr, flush=True)
        return 130
    except ExportError as exc:
        emit(args, f"钉钉文档导出失败：{exc}", event="task.failed", level="error", error={"type": type(exc).__name__, "message": str(exc)})
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    except Exception as exc:  # noqa: BLE001 - final error boundary for task UI.
        emit(args, f"钉钉文档导出失败：{exc}", event="task.failed", level="error", error={"type": type(exc).__name__, "message": str(exc)})
        print(f"钉钉文档导出失败：{exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
