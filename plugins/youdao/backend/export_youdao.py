#!/usr/bin/env python3
# Author: tllovesxs
"""
Youdao Cloud Note exporter for Wandao.

This exporter uses the normal web login session from Chrome/Edge and calls the
same web endpoints that the Youdao Cloud Note page uses. It stores cookies only,
not passwords.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import mimetypes
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wandao_core.checkpoint import add_checkpoint_args, open_checkpoint_from_args
from wandao_cli import extend_arg_list_from_file
from wandao_core.credentials import write_private_json
from wandao_core.logging import emit_legacy
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
    js_string,
    prepare_cookie_for_set,
    sanitize_filename,
    wait_for_debug_port,
)


PROJECT_DIR = Path(__file__).resolve().parent
YOUDAO_WEB_URL = "https://note.youdao.com/web/"
DEFAULT_PROFILE = ".youdao-chrome-profile"
DEFAULT_AUTH_FILE = ".youdao_auth.json"
FORBIDDEN_FILENAME_CHARS = r'<>:"/\|?*'
NOTE_SUFFIXES = {".md", ".markdown", ".note", ".clip", ""}

ROOT_ID_URL = "https://note.youdao.com/yws/api/personal/file?method=getByPath&keyfrom=web&cstk={cstk}"
DIR_LIST_URL = (
    "https://note.youdao.com/yws/api/personal/file/{dir_id}"
    "?all=true&f=true&len=1000&sort=1&isReverse=false&method=listPageByParentId"
    "&keyfrom=web&cstk={cstk}"
)
FILE_DOWNLOAD_URL = (
    "https://note.youdao.com/yws/api/personal/sync?method=download&_system=macos"
    "&_systemVersion=&_screenWidth=1280&_screenHeight=800&_appName=ynote"
    "&_appuser=0123456789abcdeffedcba9876543210&_vendor=official-website"
    "&_launch=16&_firstTime=&_deviceId=0123456789abcdef&_platform=web"
    "&_cityCode=110000&_cityName=&sev=j1&keyfrom=web&cstk={cstk}"
)


class YoudaoError(RuntimeError):
    pass


@dataclass
class RemoteNode:
    id: str
    name: str
    is_dir: bool
    parent_id: str = ""
    parent_node_id: str = ""
    node_id: str = ""
    path_parts: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def suffix(self) -> str:
        return Path(self.name or "").suffix.lower()

    @property
    def title(self) -> str:
        stem = Path(self.name or "").stem if self.suffix else self.name
        return stem or self.name or "未命名"

    @property
    def is_note_like(self) -> bool:
        return self.suffix in NOTE_SUFFIXES

    @property
    def modified_time(self) -> float | None:
        value = self.raw.get("modifyTimeForSort") or self.raw.get("modifyTime")
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @property
    def created_time(self) -> float | None:
        value = self.raw.get("createTimeForSort") or self.raw.get("createTime")
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


def select_export_documents(nodes: list[RemoteNode], selected_doc_ids: list[str] | set[str] | None) -> list[RemoteNode]:
    documents = [node for node in nodes if not node.is_dir]
    selected = set(selected_doc_ids or [])
    if not selected:
        return documents
    matched = [node for node in documents if node.id in selected]
    if documents and not matched:
        preview = ", ".join(sorted(selected)[:5])
        raise YoudaoError(
            "选择的有道云笔记文档未匹配当前目录，"
            "请重新读取目录后再试。未匹配 ID：" + preview
        )
    return matched


@dataclass
class DownloadedContent:
    content: bytes
    content_type: str
    final_url: str


def emit(message: str, *, event: str = "log.message", level: str = "info", **fields: Any) -> None:
    emit_legacy("youdao-export", message, event=event, level=level, **fields)


def default_auth_path() -> Path:
    return default_state_path(DEFAULT_AUTH_FILE)


def default_profile_path() -> Path:
    env_profile = os.environ.get("YOUDAO_PROFILE_DIR")
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
        raise YoudaoError(
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


def page_for_youdao(port: int) -> dict[str, Any] | None:
    pages = http_json(f"http://127.0.0.1:{port}/json/list", timeout=5)
    for page in pages:
        if page.get("type") != "page":
            continue
        page_url = urllib.parse.urlparse(page.get("url", ""))
        if page_url.hostname == "note.youdao.com":
            return page
    return None


def connect_youdao_browser(args: argparse.Namespace) -> tuple[CDPClient, subprocess.Popen[Any] | None]:
    chrome_proc: subprocess.Popen[Any] | None = None
    if not chrome_debug_available(args.port):
        chrome_proc = start_chrome(args.port, profile_path_from_args(args), YOUDAO_WEB_URL, args.browser_path)
        wait_for_debug_port(args.port, timeout=30)

    page = page_for_youdao(args.port)
    if not page:
        open_tab(args.port, YOUDAO_WEB_URL)
        time.sleep(2)
        page = page_for_youdao(args.port)
    if not page:
        pages = http_json(f"http://127.0.0.1:{args.port}/json/list", timeout=5)
        page = next((item for item in pages if item.get("type") == "page"), None)
    if not page:
        raise YoudaoError("无法打开有道云笔记页面，请确认浏览器可以正常启动。")

    cdp = CDPClient(page["webSocketDebuggerUrl"])
    cdp.connect()
    cdp.send("Runtime.enable")
    cdp.send("Page.enable")
    cdp.send("Network.enable")
    return cdp, chrome_proc


def is_youdao_cookie(cookie: dict[str, Any]) -> bool:
    domain = (cookie.get("domain") or "").lower().lstrip(".")
    name = cookie.get("name") or ""
    is_youdao_domain = domain == "youdao.com" or domain.endswith(".youdao.com")
    is_static_domain = domain == "ydstatic.com" or domain.endswith(".ydstatic.com")
    return (is_youdao_domain or is_static_domain) and (
        name.startswith("YNOTE") or name in {"OUTFOX_SEARCH_USER_ID", "DICT_SESS"}
    )


def save_auth_state(cdp: CDPClient, auth_file: Path) -> dict[str, Any]:
    data = cdp.send("Network.getAllCookies", timeout=20).get("result", {})
    cookies = [cookie for cookie in data.get("cookies", []) if is_youdao_cookie(cookie)]
    names = {cookie.get("name") for cookie in cookies}
    if "YNOTE_CSTK" not in names or "YNOTE_SESS" not in names:
        raise YoudaoError("没有读取到完整的有道云登录 Cookie。请确认浏览器里已经登录并能看到笔记列表。")
    payload = {
        "version": 1,
        "savedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "cookies": cookies,
    }
    write_private_json(auth_file, payload)
    return {"provider": "youdao", "cookieCount": len(cookies), "authFile": str(auth_file)}


def login_and_save_auth(args: argparse.Namespace) -> dict[str, Any]:
    auth_file = auth_path_from_args(args)
    cdp, chrome_proc = connect_youdao_browser(args)
    try:
        cdp.navigate(YOUDAO_WEB_URL)
        emit("Chrome opened. Log in to Youdao Cloud Note in the browser.")
        wait_seconds = float(getattr(args, "login_wait_seconds", 0) or 0)
        if wait_seconds > 0:
            emit(f"请在浏览器中完成登录，工具将在 {int(wait_seconds)} 秒后自动保存凭证。")
            time.sleep(wait_seconds)
        else:
            input("After login is complete and the Youdao note list is visible, press Enter...")
        time.sleep(1)
        result = save_auth_state(cdp, auth_file)
        emit(f"Saved {result['cookieCount']} auth cookies to {auth_file}")
        return result
    finally:
        cdp.close()
        if chrome_proc and args.close_started_chrome:
            chrome_proc.terminate()


def read_auth_payload(auth_file: Path) -> dict[str, Any]:
    if not auth_file.exists():
        raise YoudaoError(f"有道云登录凭证不存在：{auth_file}。请先点击“登录并保存凭证”。")
    try:
        payload = json.loads(auth_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise YoudaoError(f"读取有道云登录凭证失败：{exc}") from exc
    cookies = payload.get("cookies") or []
    if not isinstance(cookies, list) or not cookies:
        raise YoudaoError(f"有道云登录凭证里没有 Cookie：{auth_file}")
    return payload


def cookie_header(cookies: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for cookie in cookies:
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if not name or name in seen:
            continue
        parts.append(f"{name}={value}")
        seen.add(name)
    return "; ".join(parts)


def extract_cstk(cookies: list[dict[str, Any]]) -> str:
    for cookie in cookies:
        if cookie.get("name") == "YNOTE_CSTK" and cookie.get("value"):
            return str(cookie["value"])
    raise YoudaoError("Cookie 中缺少 YNOTE_CSTK，请重新登录并保存凭证。")


def throttle_request(args: argparse.Namespace | None) -> None:
    if args is None:
        return
    delay = max(0.0, float(getattr(args, "request_delay", 0) or 0))
    jitter = max(0.0, float(getattr(args, "request_jitter", 0) or 0))
    total = delay + (random.random() * jitter if jitter else 0)
    if total > 0:
        time.sleep(total)


def decode_response_body(headers: Any, data: bytes) -> bytes:
    encoding = ""
    try:
        encoding = headers.get("Content-Encoding", "")
    except Exception:
        pass
    if "gzip" in encoding.lower():
        return gzip.decompress(data)
    return data


def safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(parsed.path, safe="/:%@%")
    query = urllib.parse.quote_plus(parsed.query, safe="=&:%/@,+%")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, query, parsed.fragment))


class YoudaoClient:
    def __init__(self, auth_file: Path, args: argparse.Namespace | None = None) -> None:
        payload = read_auth_payload(auth_file)
        cookies = [prepare_cookie_for_set(cookie) for cookie in payload.get("cookies", [])]
        self.cookies = [cookie for cookie in cookies if cookie.get("name") and cookie.get("value")]
        self.cstk = extract_cstk(self.cookies)
        self.cookie_header = cookie_header(self.cookies)
        self.args = args
        self.request_count = 0

    def request(
        self,
        method: str,
        url: str,
        data: dict[str, Any] | None = None,
        timeout: int = 60,
        extra_headers: dict[str, str] | None = None,
    ) -> DownloadedContent:
        throttle_request(self.args)
        self.request_count += 1
        body = None
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "identity",
            "Origin": "https://note.youdao.com",
            "Referer": YOUDAO_WEB_URL,
            "Cookie": self.cookie_header,
        }
        if extra_headers:
            headers.update(extra_headers)
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        request = urllib.request.Request(safe_url(url), data=body, headers=headers, method=method.upper())
        attempts = max(1, int(getattr(self.args, "retry", 2) or 1))
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    raw = decode_response_body(response.headers, response.read())
                    return DownloadedContent(
                        content=raw,
                        content_type=response.headers.get("Content-Type", ""),
                        final_url=response.geturl(),
                    )
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code == 401:
                    raise YoudaoError(f"有道云登录已失效，请重新点击“登录并保存凭证”后重试。HTTP 401：{detail[:500]}") from exc
                if exc.code not in {429, 500, 502, 503, 504} or attempt >= attempts:
                    raise YoudaoError(f"有道云接口 HTTP {exc.code}：{detail[:500]}") from exc
                last_error = exc
            except urllib.error.URLError as exc:
                if attempt >= attempts:
                    raise YoudaoError(f"请求有道云失败：{exc}") from exc
                last_error = exc
            time.sleep(min(5.0, 0.8 * attempt))
        raise YoudaoError(f"请求有道云失败：{last_error}")

    def get_json(self, url: str) -> dict[str, Any]:
        content = self.request("GET", url).content
        return json.loads(content.decode("utf-8", errors="replace"))

    def post_json(self, url: str, data: dict[str, Any]) -> dict[str, Any]:
        content = self.request("POST", url, data=data).content
        return json.loads(content.decode("utf-8", errors="replace"))

    def root_info(self) -> dict[str, Any]:
        data = {"path": "/", "entire": "true", "purge": "false", "cstk": self.cstk}
        return self.post_json(ROOT_ID_URL.format(cstk=urllib.parse.quote(self.cstk)), data)

    def dir_info(self, dir_id: str) -> dict[str, Any]:
        return self.get_json(DIR_LIST_URL.format(dir_id=urllib.parse.quote(dir_id, safe=""), cstk=urllib.parse.quote(self.cstk)))

    def download_file(self, file_id: str) -> DownloadedContent:
        data = {
            "fileId": file_id,
            "version": "-1",
            "convert": "true",
            "editorType": "1",
            "cstk": self.cstk,
        }
        return self.request("POST", FILE_DOWNLOAD_URL.format(cstk=urllib.parse.quote(self.cstk)), data=data)

    def download_url(self, url: str, timeout: int = 60) -> DownloadedContent:
        return self.request("GET", url, timeout=timeout)


def file_entry(item: dict[str, Any]) -> dict[str, Any]:
    entry = item.get("fileEntry")
    return entry if isinstance(entry, dict) else item


def entry_id(entry: dict[str, Any]) -> str:
    for key in ("id", "fileId", "fid", "resourceId"):
        value = entry.get(key)
        if value:
            return str(value)
    return ""


def entry_name(entry: dict[str, Any]) -> str:
    for key in ("name", "title", "fileName"):
        value = entry.get(key)
        if value:
            return str(value)
    return "未命名"


def entry_is_dir(entry: dict[str, Any]) -> bool:
    value = entry.get("dir")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def entries_from_dir_info(data: dict[str, Any]) -> list[dict[str, Any]]:
    entries = data.get("entries") or data.get("list") or data.get("files") or []
    return entries if isinstance(entries, list) else []


def build_remote_tree(client: YoudaoClient) -> tuple[list[RemoteNode], str]:
    root = client.root_info()
    root_entry = file_entry(root)
    root_id = entry_id(root_entry)
    if not root_id:
        raise YoudaoError("没有读取到有道云根目录 ID，可能是登录已失效。")

    nodes: list[RemoteNode] = []

    def walk(parent_id: str, parent_node_id: str, path_parts: list[str]) -> None:
        data = client.dir_info(parent_id)
        for item in entries_from_dir_info(data):
            entry = file_entry(item)
            node_id = entry_id(entry)
            if not node_id:
                continue
            name = entry_name(entry)
            is_dir = entry_is_dir(entry)
            display_name = Path(name).stem if (not is_dir and Path(name).suffix.lower() in NOTE_SUFFIXES) else name
            current_path = [*path_parts, display_name]
            node = RemoteNode(
                id=node_id,
                name=name,
                is_dir=is_dir,
                parent_id=parent_id,
                parent_node_id=parent_node_id,
                node_id=f"youdao:{node_id}",
                path_parts=current_path,
                raw=entry,
            )
            nodes.append(node)
            if is_dir:
                walk(node_id, node.node_id, current_path)

    walk(root_id, "", [])
    return nodes, root_id


def toc_json(args: argparse.Namespace) -> dict[str, Any]:
    client = YoudaoClient(auth_path_from_args(args), args)
    nodes, root_id = build_remote_tree(client)
    payload_nodes = []
    for node in nodes:
        payload_nodes.append(
            {
                "nodeId": node.node_id,
                "exportId": "" if node.is_dir else node.id,
                "title": node.title if not node.is_dir else node.name,
                "parentNodeId": node.parent_node_id,
                "selectable": not node.is_dir,
                "type": "folder" if node.is_dir else "document",
                "path": "/".join(node.path_parts),
                "id": node.id,
                "parent_id": node.parent_id,
            }
        )
    return {
        "provider": "youdao",
        "rootId": root_id,
        "nodes": payload_nodes,
        "totalDocs": sum(1 for node in nodes if not node.is_dir),
        "requestCount": client.request_count,
    }


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def md_escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", "<br>")


def json_span_text(span: dict[str, Any]) -> str:
    text = str(span.get("8") or "")
    attrs = span.get("9") or []
    if isinstance(attrs, list):
        for attr in attrs:
            if not isinstance(attr, dict):
                continue
            kind = attr.get("2")
            if kind == "b":
                text = f"**{text}**"
            elif kind == "i":
                text = f"*{text}*"
            elif kind == "u":
                text = f"<u>{text}</u>"
            elif kind == "d":
                text = f"~~{text}~~"
            elif kind == "c":
                color = str(attr.get("0") or "").strip()
                if color:
                    text = f'<font color="{color}">{text}</font>'
    return text


def json_common_text(block: dict[str, Any]) -> str:
    chunks: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            spans = value.get("7")
            if isinstance(spans, list):
                chunks.extend(json_span_text(span) for span in spans if isinstance(span, dict))
            for child in value.get("5") or []:
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(block)
    return "".join(chunks).strip()


def json_link_text(block: dict[str, Any]) -> str:
    text = json_common_text(block)
    href = ""
    attrs = block.get("4")
    if isinstance(attrs, dict):
        href = str(attrs.get("hf") or attrs.get("url") or "")
    return f"[{text}]({href})" if text and href else text


def convert_json_block(block: dict[str, Any]) -> str:
    kind = block.get("6")
    attrs = block.get("4") if isinstance(block.get("4"), dict) else {}

    if kind == "h":
        level_text = str(attrs.get("l") or "h2")
        match = re.search(r"(\d+)", level_text)
        level = min(6, max(1, int(match.group(1)) if match else 2))
        text = json_common_text(block)
        return f"{'#' * level} {text}" if text else ""

    if kind == "im":
        url = str(attrs.get("u") or attrs.get("src") or "")
        return f"![]({url})" if url else ""

    if kind == "a":
        name = str(attrs.get("fn") or attrs.get("name") or "附件")
        url = str(attrs.get("re") or attrs.get("u") or attrs.get("url") or "")
        return f"[{name}]({url})" if url else name

    if kind == "cd":
        language = str(attrs.get("la") or "")
        code_lines = [json_common_text(child) for child in block.get("5") or [] if isinstance(child, dict)]
        return f"```{language}\n" + "\n".join(code_lines) + "\n```"

    if kind == "la":
        lines = [json_common_text(child) for child in block.get("5") or [] if isinstance(child, dict)]
        return "\n".join(f"<mark>{line}</mark>" for line in lines if line)

    if kind == "q":
        lines = [json_common_text(child) for child in block.get("5") or [] if isinstance(child, dict)]
        return "\n".join(f"> {line}" for line in lines if line)

    if kind == "l":
        text = json_common_text(block)
        list_type = str(attrs.get("lt") or "unordered")
        level = int(attrs.get("ll") or 1)
        indent = "  " * max(0, level - 1)
        marker = "1." if list_type == "ordered" else "-"
        return f"{indent}{marker} {text}" if text else ""

    if kind == "t":
        rows: list[list[str]] = []
        for row in block.get("5") or []:
            if not isinstance(row, dict):
                continue
            cells = []
            for cell in row.get("5") or []:
                cells.append(md_escape_cell(json_common_text(cell) if isinstance(cell, dict) else ""))
            if cells:
                rows.append(cells)
        if not rows:
            return ""
        width = max(len(row) for row in rows)
        rows = [row + [""] * (width - len(row)) for row in rows]
        if len(rows) == 1:
            rows.insert(0, [""] * width)
        lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
        lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
        return "\n".join(lines)

    if kind == "hr":
        return "---"

    if kind in {"drawio_ynote", "excalidraw", "mindmap"}:
        url = str(attrs.get("u") or attrs.get("src") or "")
        return f"![]({url})" if url else ""

    if kind == "media":
        label = str(attrs.get("sr") or attrs.get("name") or "媒体文件")
        url = str(attrs.get("hf") or attrs.get("u") or attrs.get("url") or "")
        return f"[{label}]({url})" if url else label

    text = json_link_text(block)
    return text


def convert_json_note(content: bytes) -> str:
    data = json.loads(decode_text(content))
    blocks = data.get("5") if isinstance(data, dict) else []
    lines = [convert_json_block(block) for block in blocks if isinstance(block, dict)]
    return "\n\n".join(line for line in lines if line).strip()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].replace("-", "_")


def child_text(element: ET.Element, name: str) -> str:
    for child in list(element):
        if local_name(child.tag) == name.replace("-", "_"):
            return "".join(child.itertext()).strip()
    return ""


def element_text(element: ET.Element) -> str:
    text = child_text(element, "text")
    if text:
        return text
    return "".join(element.itertext()).strip()


def convert_table_content(content: str) -> str:
    try:
        table = json.loads(content)
    except Exception:
        return content
    widths = table.get("widths") or []
    cells = table.get("cells") or []
    width = max(1, len(widths))
    rows: list[list[str]] = []
    row: list[str] = []
    for cell in cells:
        row.append(md_escape_cell(str(cell.get("value") or "")))
        if len(row) == width:
            rows.append(row)
            row = []
    if row:
        rows.append(row + [""] * (width - len(row)))
    if not rows:
        return ""
    if len(rows) == 1:
        rows.insert(0, [""] * width)
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(lines)


def convert_xml_element(element: ET.Element, list_types: dict[str, str]) -> str:
    name = local_name(element.tag)
    text = element_text(element)
    if name == "heading":
        level_raw = element.attrib.get("level") or "2"
        try:
            level = 1 if level_raw in {"a", "b"} else min(6, max(1, int(level_raw or 2)))
        except ValueError:
            level = 2
        return f"{'#' * level} {text}" if text else ""
    if name == "image":
        url = child_text(element, "source")
        return f"![{text}]({url})" if url else text
    if name == "attach":
        filename = child_text(element, "filename") or "附件"
        url = child_text(element, "resource")
        return f"[{filename}]({url})" if url else filename
    if name == "code":
        language = child_text(element, "language")
        return f"```{language}\n{text}\n```"
    if name == "todo":
        return f"- [ ] {text}"
    if name == "quote":
        return "\n".join(f"> {line}" for line in text.splitlines() if line)
    if name == "horizontal_line":
        return "---"
    if name == "list_item":
        list_id = element.attrib.get("list-id", "")
        marker = "1." if list_types.get(list_id) == "ordered" else "-"
        return f"{marker} {text}" if text else ""
    if name == "table":
        return convert_table_content(child_text(element, "content"))
    return text


def convert_xml_note(content: bytes) -> str:
    root = ET.fromstring(content)
    list_types: dict[str, str] = {}
    body: ET.Element | None = None
    for child in list(root):
        if local_name(child.tag) == "body":
            body = child
        for maybe_list in child.iter():
            if local_name(maybe_list.tag) == "list" and maybe_list.attrib.get("id"):
                list_types[maybe_list.attrib["id"]] = maybe_list.attrib.get("type", "unordered")
    body = body or root
    lines = [convert_xml_element(element, list_types) for element in list(body)]
    return "\n\n".join(line for line in lines if line).strip()


def convert_html_note(content: bytes) -> str:
    text = decode_text(content)
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", text)
    text = re.sub(r"(?is)<img[^>]+src=[\"']([^\"']+)[\"'][^>]*>", lambda m: f"\n![]({html.unescape(m.group(1))})\n", text)
    text = re.sub(
        r"(?is)<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        lambda m: f"[{html_to_plain(m.group(2))}]({html.unescape(m.group(1))})",
        text,
    )
    for level in range(6, 0, -1):
        text = re.sub(
            rf"(?is)<h{level}[^>]*>(.*?)</h{level}>",
            lambda m, level=level: "\n" + "#" * level + " " + html_to_plain(m.group(1)) + "\n",
            text,
        )
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</(p|div|section|article|li|tr)>", "\n", text)
    text = re.sub(r"(?is)<li[^>]*>", "- ", text)
    return html_to_plain(text)


def html_to_plain(text: str) -> str:
    text = re.sub(r"(?is)<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [line.rstrip() for line in text.splitlines()]
    compact: list[str] = []
    blank = False
    for line in lines:
        if not line.strip():
            if not blank:
                compact.append("")
            blank = True
        else:
            compact.append(line)
            blank = False
    return "\n".join(compact).strip()


def convert_note_to_markdown(node: RemoteNode, downloaded: DownloadedContent) -> tuple[str, bool]:
    suffix = node.suffix
    head = downloaded.content[:80].lstrip()
    content_type = downloaded.content_type.lower()
    if suffix in {".md", ".markdown"} or "markdown" in content_type:
        return decode_text(downloaded.content).strip(), True
    if head.startswith(b"{"):
        return convert_json_note(downloaded.content), True
    if head.startswith(b"<?xml") or head.startswith(b"<note"):
        try:
            return convert_xml_note(downloaded.content), True
        except ET.ParseError:
            return convert_html_note(downloaded.content), True
    if head.startswith(b"<"):
        return convert_html_note(downloaded.content), True
    if suffix in {".note", ".clip", ""}:
        return decode_text(downloaded.content).strip(), True
    return "", False


def content_disposition_filename(value: str) -> str:
    match = re.search(r"filename\*=UTF-8''([^;]+)", value or "", flags=re.I)
    if match:
        return urllib.parse.unquote(match.group(1))
    match = re.search(r'filename="?([^";]+)"?', value or "", flags=re.I)
    return urllib.parse.unquote(match.group(1)) if match else ""


def extension_from_type(content_type: str, fallback: str = "") -> str:
    main = (content_type or "").split(";", 1)[0].strip().lower()
    if main == "image/jpeg":
        return ".jpg"
    if main == "text/markdown":
        return ".md"
    return mimetypes.guess_extension(main) or fallback


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 2
    while True:
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def relative_link(from_file: Path, target: Path) -> str:
    rel = os.path.relpath(target, from_file.parent).replace("\\", "/")
    return urllib.parse.quote(rel, safe="/#%._-")


def filename_from_url(url: str, suggested: str, content_type: str, fallback_stem: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    for key in ("filename", "download", "name"):
        if query.get(key):
            suggested = query[key][0]
            break
    basename = urllib.parse.unquote(Path(parsed.path).name or "")
    raw_name = suggested or basename or fallback_stem
    name = sanitize_filename(raw_name, fallback=fallback_stem, max_len=120)
    suffix = Path(name).suffix
    if not suffix:
        name += extension_from_type(content_type, ".bin")
    return name


def write_resource_file(target: Path, content: bytes) -> Path:
    if target.exists():
        try:
            if hashlib.sha1(target.read_bytes()).digest() == hashlib.sha1(content).digest():
                return target
        except OSError:
            pass
        target = unique_path(target)
    target.write_bytes(content)
    return target


def is_image_content(content_type: str, path: Path) -> bool:
    main_type = (content_type or "").split(";", 1)[0].strip().lower()
    if main_type.startswith("image/"):
        return True
    return path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}


def download_resource(
    client: YoudaoClient,
    url: str,
    md_path: Path,
    suggested_name: str,
    is_image: bool,
    timeout: int,
) -> tuple[str, bool]:
    downloaded = client.download_url(url, timeout=timeout)
    filename = filename_from_url(
        downloaded.final_url or url,
        suggested_name,
        downloaded.content_type,
        "image" if is_image else "attachment",
    )
    image_file = is_image_content(downloaded.content_type, Path(filename))
    folder = md_path.parent / ("images" if is_image and image_file else "attachments")
    folder.mkdir(parents=True, exist_ok=True)
    target = write_resource_file(folder / filename, downloaded.content)
    return relative_link(md_path, target), image_file


IMAGE_LINK_RE = re.compile(r"!\[([^\]]*)\]\((https?://[^)\s]*note\.youdao\.com[^)]*)\)")
ANY_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\((https?://[^)\s]*note\.youdao\.com[^)]*)\)")


def localize_links(client: YoudaoClient, markdown: str, md_path: Path, args: argparse.Namespace, stats: dict[str, int]) -> str:
    def image_replacer(match: re.Match[str]) -> str:
        alt, url = match.group(1), match.group(2)
        try:
            local, image_file = download_resource(client, html.unescape(url), md_path, alt or "", True, args.download_timeout)
            if image_file:
                stats["imageSuccess"] += 1
                return f"![{alt}]({local})"
            stats["attachmentSuccess"] += 1
            label = alt or Path(urllib.parse.unquote(local)).name or "附件"
            return f"[{label}]({local})"
        except Exception as exc:
            stats["imageFailureCount"] += 1
            emit(
                f"有道云图片下载失败：{url}：{exc}",
                event="resource.download.failed",
                level="error",
                resource={"type": "image", "url": url, "host": urllib.parse.urlparse(url).netloc},
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            return match.group(0) if args.keep_remote_images else f"![{alt}]()"

    def attachment_replacer(match: re.Match[str]) -> str:
        label, url = match.group(1), match.group(2)
        try:
            local, _ = download_resource(client, html.unescape(url), md_path, label or "", False, args.download_timeout)
            stats["attachmentSuccess"] += 1
            return f"[{label}]({local})"
        except Exception as exc:
            stats["attachmentFailureCount"] += 1
            emit(
                f"有道云附件下载失败：{url}：{exc}",
                event="resource.download.failed",
                level="error",
                resource={"type": "attachment", "url": url, "host": urllib.parse.urlparse(url).netloc},
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            return match.group(0)

    markdown = IMAGE_LINK_RE.sub(image_replacer, markdown)
    markdown = ANY_LINK_RE.sub(attachment_replacer, markdown)
    return markdown


def clean_markdown(markdown: str, title: str) -> str:
    markdown = markdown.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not markdown:
        markdown = f"# {title}\n"
    return markdown + "\n"


def apply_remote_file_time(path: Path, node: RemoteNode) -> None:
    modified = node.modified_time
    created = node.created_time or modified
    if modified is None:
        return
    try:
        os.utime(path, (created or modified, modified))
    except OSError:
        pass


def should_skip_existing(path: Path, node: RemoteNode, args: argparse.Namespace) -> bool:
    if not args.incremental or args.update_existing or not path.exists():
        return False
    modified = node.modified_time
    if modified is None:
        return True
    return path.stat().st_mtime >= modified


def path_for_node(output: Path, node: RemoteNode, counters: dict[str, int], create_dirs: bool = True) -> Path:
    parts = node.path_parts[:-1]
    current = output
    folder_key = ""
    for part in parts:
        folder_key = f"{folder_key}/{part}" if folder_key else part
        folder = sanitize_filename(part, fallback="未命名目录", max_len=90)
        current = current / folder
    if create_dirs:
        current.mkdir(parents=True, exist_ok=True)

    key = "/".join(parts)
    counters[key] = counters.get(key, 0) + 1
    prefix = f"{counters[key]:02d}-"
    if node.is_note_like:
        filename = f"{prefix}{sanitize_filename(node.title, fallback='未命名笔记', max_len=90)}.md"
    else:
        filename = f"{prefix}{sanitize_filename(node.name, fallback='未命名文件', max_len=120)}"
    return current / filename


def write_index(output: Path, exported_rows: list[dict[str, Any]]) -> None:
    lines = [
        "# 有道云笔记导出目录",
        "",
        "本文档由万能导根据有道云笔记目录生成。",
        "",
    ]
    for row in sorted(exported_rows, key=lambda item: item.get("path", "")):
        rel = Path(row["file"]).relative_to(output).as_posix()
        indent = "  " * max(0, int(row.get("level", 1)) - 1)
        lines.append(f"{indent}- [{row['title']}]({urllib.parse.quote(rel, safe='/#%._-')})")
    (output / "00-有道云笔记入口.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def emit_progress(
    index: int,
    total: int,
    exported: int,
    skipped: int,
    failures: int,
    resource_stats: dict[str, int] | None = None,
) -> None:
    stats = {
        "exportedDocs": exported,
        "skippedDocs": skipped,
        "failureCount": failures,
    }
    if resource_stats:
        stats.update(resource_stats)
    emit(
        "progress "
        f"done={index} queued={max(0, total - index)} exported={exported} "
        f"skipped={skipped} failures={failures}",
        event="task.progress",
        progress={"current": index, "total": total},
        stats=stats,
    )


def export_youdao(args: argparse.Namespace) -> dict[str, Any]:
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = open_checkpoint_from_args(args, "youdao", "export")
    client: YoudaoClient | None = None
    try:
        client = YoudaoClient(auth_path_from_args(args), args)
        nodes, root_id = build_remote_tree(client)
    except ExportStopped as exc:
        report = {
            "provider": "youdao",
            "rootId": "",
            "output": str(output),
            "totalDocs": 0,
            "exportedDocs": 0,
            "skippedDocs": 0,
            "failureCount": 0,
            "failures": [],
            "stopped": True,
            "requestCount": client.request_count if client else 0,
            "imageSuccess": 0,
            "imageFailureCount": 0,
            "attachmentSuccess": 0,
            "attachmentFailureCount": 0,
        }
        if checkpoint:
            checkpoint.start_task(
                {
                    "source": YOUDAO_WEB_URL,
                    "outputDir": str(output),
                    "stage": "scanning",
                    "resume": bool(getattr(args, "resume", False)),
                    "retryFailed": bool(getattr(args, "retry_failed", False)),
                }
            )
            checkpoint.fail_task("stopped", status="stopped")
            report["checkpoint"] = checkpoint.stats()
            checkpoint.close()
        report_path = output / "00-导出报告.json"
        report = finalize_report(report, provider="youdao", mode="export", report_file=report_path, output=output)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        emit("有道云笔记导出已停止", event="task.stopped", level="warn", reportFile=str(report_path))
        return report
    counters: dict[str, int] = {}
    local_paths = {
        node.id: path_for_node(output, node, counters, create_dirs=False)
        for node in nodes
        if not node.is_dir
    }
    docs = select_export_documents(nodes, args.selected_doc_ids)
    if checkpoint:
        checkpoint.start_task(
            {
                "source": YOUDAO_WEB_URL,
                "outputDir": str(output),
                "rootId": root_id,
                "totalDocs": len(docs),
                "resume": bool(getattr(args, "resume", False)),
                "retryFailed": bool(getattr(args, "retry_failed", False)),
            }
        )
        for node in docs:
            checkpoint.upsert_item(
                f"youdao:node:{node.id}",
                title=node.title,
                source_url="/".join(node.path_parts),
                source_id=node.id,
                parent_key=node.parent_id,
                metadata={"id": node.id, "path": node.path_parts, "isNoteLike": node.is_note_like},
            )
        if getattr(args, "retry_failed", False):
            docs = [node for node in docs if checkpoint.item_status(f"youdao:node:{node.id}") == "failed"]
    total = len(docs)
    exported = 0
    skipped = 0
    failures: list[dict[str, Any]] = []
    stopped = False
    rows: list[dict[str, Any]] = []
    stats = {
        "imageSuccess": 0,
        "imageFailureCount": 0,
        "attachmentSuccess": 0,
        "attachmentFailureCount": 0,
    }
    emit(
        f"开始导出有道云笔记：共 {total} 篇。",
        event="task.started",
        totals={"documents": total, "nodes": len(nodes)},
        output=str(output),
    )

    for index, node in enumerate(docs, start=1):
        md_or_file_path = local_paths[node.id]
        item_key = f"youdao:node:{node.id}"
        try:
            check_stopped(args)
            if checkpoint and getattr(args, "resume", False) and not args.update_existing and checkpoint.item_status(item_key) == "completed":
                row_file = md_or_file_path.with_suffix(".md") if node.is_note_like else md_or_file_path
                skipped += 1
                if row_file.exists():
                    rows.append(
                        {
                            "title": node.title,
                            "file": str(row_file),
                            "path": "/".join(node.path_parts),
                            "level": len(node.path_parts),
                        }
                    )
                emit_progress(index, total, exported, skipped, len(failures), stats)
                continue
            if should_skip_existing(md_or_file_path, node, args):
                if checkpoint:
                    checkpoint.complete_item(item_key, local_path=str(md_or_file_path), metadata={"id": node.id, "skippedExisting": True})
                skipped += 1
                rows.append(
                    {
                        "title": node.title,
                        "file": str(md_or_file_path),
                        "path": "/".join(node.path_parts),
                        "level": len(node.path_parts),
                    }
                )
                emit_progress(index, total, exported, skipped, len(failures), stats)
                continue

            if checkpoint:
                checkpoint.start_item(item_key, "content")
            emit(
                f"开始导出有道云笔记：{node.title}",
                event="document.export.started",
                doc={"id": node.id, "title": node.title, "index": index, "path": "/".join(node.path_parts)},
            )
            downloaded = client.download_file(node.id)
            check_stopped(args)
            markdown, is_markdown = convert_note_to_markdown(node, downloaded)
            resource_failures_before = stats["imageFailureCount"] + stats["attachmentFailureCount"]
            if is_markdown:
                md_path = md_or_file_path.with_suffix(".md")
                md_path.parent.mkdir(parents=True, exist_ok=True)
                markdown = localize_links(client, markdown, md_path, args, stats)
                check_stopped(args)
                md_path.write_text(clean_markdown(markdown, node.title), encoding="utf-8")
                apply_remote_file_time(md_path, node)
                row_file = md_path
            else:
                file_path = md_or_file_path
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(downloaded.content)
                apply_remote_file_time(file_path, node)
                row_file = file_path

            exported += 1
            if checkpoint:
                resource_failures_in_doc = (
                    stats["imageFailureCount"] + stats["attachmentFailureCount"] - resource_failures_before
                )
                if resource_failures_in_doc:
                    checkpoint.fail_item(item_key, f"{resource_failures_in_doc} 个图片或附件下载失败")
                else:
                    checkpoint.complete_item(item_key, local_path=str(row_file), metadata={"id": node.id, "path": node.path_parts})
            rows.append(
                {
                    "title": node.title,
                    "file": str(row_file),
                    "path": "/".join(node.path_parts),
                    "level": len(node.path_parts),
                }
            )
            emit(
                f"有道云笔记导出完成：{node.title}",
                event="document.export.completed",
                doc={"id": node.id, "title": node.title, "index": index, "path": str(row_file)},
            )
        except ExportStopped:
            stopped = True
            if checkpoint:
                checkpoint.fail_item(item_key, "stopped")
            emit(f"有道云笔记导出已停止：{node.title}", event="task.stopped", level="warn")
            break
        except Exception as exc:
            if checkpoint:
                checkpoint.fail_item(item_key, str(exc))
            failures.append({"id": node.id, "title": node.title, "path": "/".join(node.path_parts), "error": str(exc)})
            emit(
                f"有道云笔记导出失败：{node.title}：{exc}",
                event="document.export.failed",
                level="error",
                doc={"id": node.id, "title": node.title, "index": index, "path": "/".join(node.path_parts)},
                error={"type": type(exc).__name__, "message": str(exc)},
            )
        if index % max(1, args.progress_every) == 0 or index == total:
            emit_progress(index, total, exported, skipped, len(failures), stats)

    write_index(output, rows)
    report = {
        "provider": "youdao",
        "rootId": root_id,
        "output": str(output),
        "totalDocs": total,
        "exportedDocs": exported,
        "skippedDocs": skipped,
        "failureCount": len(failures),
        "failures": failures,
        "stopped": stopped,
        "requestCount": client.request_count,
        **stats,
    }
    if checkpoint:
        report["checkpoint"] = checkpoint.stats()
    report_path = output / "00-导出报告.json"
    report = finalize_report(report, provider="youdao", mode="export", report_file=report_path, output=output)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if checkpoint:
        resource_failure_count = stats["imageFailureCount"] + stats["attachmentFailureCount"]
        if stopped:
            checkpoint.fail_task("stopped", status="stopped")
        elif failures or resource_failure_count:
            checkpoint.fail_task(
                f"{len(failures)} 个文档失败，{resource_failure_count} 个资源失败",
                status="failed",
            )
        else:
            checkpoint.complete_task(report)
        checkpoint.close()
    emit(
        "有道云笔记导出已停止" if stopped else ("有道云笔记导出完成" if not failures else f"有道云笔记导出完成，但有 {len(failures)} 个失败项"),
        event="task.stopped" if stopped else "task.completed",
        level="warn" if stopped or failures or (stats["imageFailureCount"] + stats["attachmentFailureCount"]) else "success",
        reportFile=str(report_path),
        stats={"exportedDocs": exported, "skippedDocs": skipped, "failureCount": len(failures), **stats},
    )
    return report


def run_gui() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext

    root = tk.Tk()
    root.title("万能导 - 有道云笔记导出")
    root.geometry("820x620")

    output_var = tk.StringVar(value=str((PROJECT_DIR / "exports" / "youdao").resolve()))
    incremental_var = tk.BooleanVar(value=True)
    log_box = scrolledtext.ScrolledText(root, height=24)

    frame = tk.Frame(root)
    frame.pack(fill="both", expand=True, padx=16, pady=16)
    tk.Label(frame, text="输出目录").pack(anchor="w")
    row = tk.Frame(frame)
    row.pack(fill="x", pady=(0, 8))
    tk.Entry(row, textvariable=output_var).pack(side="left", fill="x", expand=True)
    tk.Button(row, text="浏览", command=lambda: output_var.set(filedialog.askdirectory() or output_var.get())).pack(side="left", padx=(8, 0))
    tk.Checkbutton(frame, text="增量导出", variable=incremental_var).pack(anchor="w")
    log_box.pack(in_=frame, fill="both", expand=True, pady=12)

    def run_action(extra: list[str]) -> None:
        command = [sys.executable, str(Path(__file__).resolve()), *extra]
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE, text=True, encoding="utf-8")
        if "--login" in extra and proc.stdin:
            messagebox.showinfo("登录提示", "请在浏览器中完成登录，完成后点击确定保存凭证。")
            proc.stdin.write("\n")
            proc.stdin.close()
        assert proc.stdout
        for line in proc.stdout:
            log_box.insert("end", line)
            log_box.see("end")
            root.update_idletasks()
        if proc.wait() != 0:
            messagebox.showerror("执行失败", "请查看日志。")

    def login() -> None:
        run_action(["--login"])

    def scan() -> None:
        run_action(["--scan-toc"])

    def export_all() -> None:
        args = ["--output", output_var.get()]
        if incremental_var.get():
            args.append("--incremental")
        run_action(args)

    actions = tk.Frame(frame)
    actions.pack(fill="x")
    tk.Button(actions, text="登录并保存凭证", command=login).pack(side="left", padx=(0, 8))
    tk.Button(actions, text="读取目录", command=scan).pack(side="left", padx=(0, 8))
    tk.Button(actions, text="导出全部", command=export_all).pack(side="left", padx=(0, 8))
    tk.Button(actions, text="退出", command=root.destroy).pack(side="right")

    root.mainloop()
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出有道云笔记为 Markdown")
    parser.add_argument("--gui", action="store_true", help="打开简易图形界面")
    parser.add_argument("--login", action="store_true", help="打开浏览器登录并保存 Cookie")
    parser.add_argument("--login-wait-seconds", type=float, default=0.0, help="等待指定秒数后自动保存登录凭证")
    parser.add_argument("--scan-toc", action="store_true", help="读取远端目录并输出 JSON")
    parser.add_argument("--output", help="Markdown 输出目录")
    parser.add_argument("--auth-file", help=f"登录 Cookie 文件，默认 {default_auth_path()}")
    parser.add_argument("--profile-dir", help=f"浏览器配置目录，默认 {default_profile_path()}")
    parser.add_argument("--browser-path", help="可选 Chrome/Edge/Chromium 可执行文件路径")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Chrome DevTools 调试端口")
    parser.add_argument("--close-started-chrome", action="store_true", help="任务结束后关闭本脚本启动的浏览器")
    parser.add_argument("--doc-id", action="append", dest="selected_doc_ids", help="只导出指定笔记 ID，可重复")
    parser.add_argument("--doc-id-file", default="", help="从文件读取要导出的笔记 ID，JSON 数组或逐行文本均可")
    parser.add_argument("--incremental", action="store_true", help="已有文件则跳过")
    parser.add_argument("--update-existing", action="store_true", help="配合 --incremental 时也更新已有文件")
    add_checkpoint_args(parser)
    parser.add_argument("--progress-every", type=int, default=1, help="每处理多少篇输出一次进度")
    parser.add_argument("--request-delay", type=float, default=0.8, help="每次请求前固定等待秒数")
    parser.add_argument("--request-jitter", type=float, default=0.4, help="每次请求额外随机等待秒数")
    parser.add_argument("--download-timeout", type=int, default=60, help="图片和附件下载超时时间")
    parser.add_argument("--retry", type=int, default=2, help="网络请求失败时的重试次数")
    parser.add_argument("--keep-remote-images", action="store_true", default=True, help="图片下载失败时保留远程链接")
    parser.add_argument("--drop-failed-images", dest="keep_remote_images", action="store_false", help="图片下载失败时移除远程链接")
    args = parser.parse_args(argv)
    extend_arg_list_from_file(args, "selected_doc_ids")
    return args


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
                raise YoudaoError("--output is required unless --login or --scan-toc is used")
            result = export_youdao(args)
    except KeyboardInterrupt:
        emit("有道云笔记导出已停止。", event="task.stopped", level="warn")
        print("Interrupted.", file=sys.stderr)
        return 130
    except (YoudaoError, ExportError) as exc:
        emit(
            f"有道云笔记导出失败：{exc}",
            event="task.failed",
            level="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        emit(
            f"有道云笔记导出失败：{exc}",
            event="task.failed",
            level="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 130 if result.get("stopped") else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
