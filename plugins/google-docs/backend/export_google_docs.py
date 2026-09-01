#!/usr/bin/env python3
"""Export one authorized Google Docs document to Markdown with local resources."""
from __future__ import annotations

import argparse
import base64
import binascii
import html
import io
import json
import mimetypes
import os
import re
import socket
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from wandao_core.browser import (
    CDPClient, ExportError, ExportStopped, check_stopped,
    chrome_debug_available, default_data_dir, emit, http_json,
    open_tab, sanitize_filename, start_chrome, wait_for_debug_port,
)
from wandao_core.report import finalize_report

PLUGIN_ID = "google-docs"
PROVIDER_ID = "google-docs-export"
DOCS_HOST = "docs.google.com"
DRIVE_HOSTS = {"drive.google.com", "drive.usercontent.google.com"}
ENTRY_URL = "https://docs.google.com/"
LOGIN_URL = "https://accounts.google.com/"
DEFAULT_PORT = 9265
DEFAULT_PROFILE = ".google-docs-chrome-profile"
MAX_RESOURCE_BYTES = 32 * 1024 * 1024
MIME_EXTENSIONS = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
    "image/webp": ".webp", "image/svg+xml": ".svg", "application/pdf": ".pdf",
    "text/plain": ".txt", "text/markdown": ".md", "application/zip": ".zip",
}


class GoogleDocsError(ExportError):
    """A user-facing Google Docs plugin error."""


@dataclass(frozen=True)
class GoogleDocsSource:
    document_id: str
    canonical_url: str


@dataclass
class DocumentNode:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["DocumentNode | str"] = field(default_factory=list)


@dataclass(frozen=True)
class ImageRef:
    source: str
    alt: str
    index: int


@dataclass(frozen=True)
class AttachmentRef:
    source: str
    name: str
    index: int


@dataclass(frozen=True)
class ExportedDocument:
    root: DocumentNode
    title: str


class DocumentHtmlParser(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = DocumentNode("root")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = DocumentNode(tag.lower(), {str(k).lower(): str(v or "") for k, v in attrs})
        self.stack[-1].children.append(node)
        if node.tag not in self.VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in self.VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == name:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].children.append(data)


def parse_google_docs_url(value: str) -> GoogleDocsSource:
    raw = str(value or "").strip()
    try:
        parsed = urllib.parse.urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise GoogleDocsError("Google Docs 链接端口格式无效。") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or host != DOCS_HOST or parsed.username or parsed.password or port not in {None, 443}:
        raise GoogleDocsError("请输入 HTTPS 的 docs.google.com 单篇文档链接。")
    path = urllib.parse.unquote(parsed.path or "").rstrip("/")
    match = re.fullmatch(r"/document/d/([A-Za-z0-9_-]+)(?:/(?:edit|view|preview))?", path)
    if not match:
        raise GoogleDocsError("当前仅支持 docs.google.com/document/d/<文档ID>/edit 形式的单篇文档链接。")
    document_id = match.group(1)
    return GoogleDocsSource(document_id, f"https://{DOCS_HOST}/document/d/{document_id}/edit")


def default_profile_path() -> Path:
    override = os.environ.get("GOOGLE_DOCS_PROFILE_DIR", "").strip()
    return Path(override).expanduser().resolve() if override else default_data_dir() / DEFAULT_PROFILE


def build_document_nodes(source: GoogleDocsSource, title: str) -> list[dict[str, Any]]:
    return [
        {"nodeId": "root", "exportId": "root", "title": title, "parentNodeId": "", "selectable": False, "type": "root"},
        {"nodeId": source.document_id, "exportId": source.document_id, "title": title, "parentNodeId": "root", "selectable": True, "type": "document"},
    ]


def walk(node: DocumentNode) -> Iterable[DocumentNode]:
    for child in node.children:
        if isinstance(child, DocumentNode):
            yield child
            yield from walk(child)


def text_content(node: DocumentNode) -> str:
    return "".join(text_content(child) if isinstance(child, DocumentNode) else child for child in node.children)


def find_title(root: DocumentNode) -> str:
    for node in walk(root):
        if node.tag == "title" and text_content(node).strip():
            return text_content(node).strip()
    for node in walk(root):
        if node.tag in {"h1", "h2", "h3"} and text_content(node).strip():
            return text_content(node).strip()
    return ""


def parse_exported_html(source_html: str, title: str | None = None) -> ExportedDocument:
    parser = DocumentHtmlParser()
    parser.feed(source_html or "")
    parser.close()
    resolved = (title or find_title(parser.root) or "Google Docs 文档").strip()
    return ExportedDocument(parser.root, resolved)


def normalize_text(value: str) -> str:
    value = html.unescape(value).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[ \t]+", " ", value)


def is_safe_markdown_link(value: str) -> bool:
    parsed = urllib.parse.urlsplit(value)
    return parsed.scheme.lower() in {"http", "https", "mailto"} and not parsed.username and not parsed.password


def is_data_image(value: str) -> bool:
    return value.lower().startswith("data:image/")


def decode_data_uri(value: str) -> tuple[bytes, str]:
    match = re.fullmatch(r"data:([^;,]+)(;base64)?,(.*)", value, re.I | re.S)
    if not match:
        raise GoogleDocsError("图片 data URI 格式无效。")
    mime, encoded, payload = match.groups()
    try:
        data = base64.b64decode(payload, validate=True) if encoded else urllib.parse.unquote_to_bytes(payload)
    except (binascii.Error, ValueError) as exc:
        raise GoogleDocsError("图片 data URI 无法解码。") from exc
    if len(data) > MAX_RESOURCE_BYTES:
        raise GoogleDocsError("图片超过单个资源大小限制。")
    return data, mime.lower()


def is_allowed_resource_url(value: str) -> bool:
    parsed = urllib.parse.urlsplit(str(value or "").strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    return (
        parsed.scheme.lower() == "https" and not parsed.username and not parsed.password
        and parsed.port in {None, 443} and host in ({DOCS_HOST} | DRIVE_HOSTS) and bool(parsed.path)
    )


def is_attachment_node(node: DocumentNode) -> bool:
    return node.attrs.get("data-wandao-attachment", "").lower() == "true" or node.attrs.get("data-attachment", "").lower() == "true"


def resource_extension(name: str, mime: str = "") -> str:
    suffix = Path(name).suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        return suffix
    return MIME_EXTENSIONS.get(mime.lower(), ".bin")
def inline_markdown(node: DocumentNode, images: list[ImageRef], attachments: list[AttachmentRef]) -> str:
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(normalize_text(child))
            continue
        tag = child.tag
        if tag in {"strong", "b"}:
            value = inline_markdown(child, images, attachments).strip()
            if value:
                parts.append(f"**{value}**")
        elif tag in {"em", "i"}:
            value = inline_markdown(child, images, attachments).strip()
            if value:
                parts.append(f"*{value}*")
        elif tag == "a":
            label = inline_markdown(child, images, attachments).strip() or normalize_text(child.attrs.get("title", "链接"))
            href = child.attrs.get("href", "").strip()
            if is_attachment_node(child):
                index = len(attachments) + 1
                attachments.append(AttachmentRef(href, label or f"attachment-{index:03d}", index))
                parts.append(f"__ATTACHMENT_{index}__")
            elif href and is_safe_markdown_link(href):
                parts.append(f"[{label}]({href})")
            else:
                parts.append(label)
        elif tag == "img":
            index = len(images) + 1
            images.append(ImageRef(child.attrs.get("src", "").strip(), normalize_text(child.attrs.get("alt", "image")) or "image", index))
            parts.append(f"__IMAGE_{index}__")
        elif tag == "br":
            parts.append("\n")
        elif tag not in {"script", "style"}:
            parts.append(inline_markdown(child, images, attachments))
    return "".join(parts)


def render_block(node: DocumentNode, images: list[ImageRef], attachments: list[AttachmentRef], list_depth: int = 0) -> list[str]:
    output: list[str] = []
    for child in node.children:
        if isinstance(child, str):
            value = normalize_text(child).strip()
            if value:
                output.extend([value, ""])
            continue
        tag = child.tag
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            value = inline_markdown(child, images, attachments).strip()
            if value:
                output.extend([f"{'#' * int(tag[1])} {value}", ""])
        elif tag in {"p", "div", "section", "article", "blockquote"}:
            value = inline_markdown(child, images, attachments).strip()
            if value:
                output.extend([value, ""])
            else:
                output.extend(render_block(child, images, attachments, list_depth))
        elif tag in {"ul", "ol"}:
            ordered = tag == "ol"
            counter = 1
            for item in child.children:
                if isinstance(item, DocumentNode) and item.tag == "li":
                    value = inline_markdown(item, images, attachments).strip()
                    marker = f"{counter}." if ordered else "-"
                    output.append(f"{'  ' * list_depth}{marker} {value}".rstrip())
                    counter += 1
            output.append("")
        elif tag == "li":
            value = inline_markdown(child, images, attachments).strip()
            if value:
                output.extend([value, ""])
        elif tag == "img":
            output.extend([inline_markdown(child, images, attachments), ""])
        elif tag == "a" and is_attachment_node(child):
            output.extend([inline_markdown(child, images, attachments), ""])
        elif tag not in {"head", "title", "meta", "script", "style"}:
            output.extend(render_block(child, images, attachments, list_depth))
    return output


def render_markdown(document: ExportedDocument) -> tuple[str, list[ImageRef], list[AttachmentRef]]:
    images: list[ImageRef] = []
    attachments: list[AttachmentRef] = []
    body_root = DocumentNode("body")
    body_children = [child for child in document.root.children if not (isinstance(child, str) and not child.strip())]
    if len(body_children) == 1 and isinstance(body_children[0], DocumentNode) and body_children[0].tag == "html":
        body_children = [child for child in body_children[0].children if not (isinstance(child, str) and not child.strip())]
    body_node = next((child for child in body_children if isinstance(child, DocumentNode) and child.tag == "body"), None)
    if body_node is not None:
        body_root.children = [child for child in body_node.children if not (isinstance(child, str) and not child.strip())]
    else:
        for child in body_children:
            if isinstance(child, DocumentNode) and child.tag in {"head", "title", "meta", "script", "style"}:
                continue
            body_root.children.append(child)
    body = render_block(body_root, images, attachments)
    title_line = f"# {normalize_text(document.title).strip()}"
    if body and body[0] == title_line:
        body = body[1:]
        if body and not body[0].strip():
            body = body[1:]
    while body and not body[-1].strip():
        body.pop()
    rendered = title_line + "\n\n"
    rendered += "\n".join(body).strip() + "\n"
    for ref in images:
        extension = ".png" if is_data_image(ref.source) and ref.source.lower().startswith("data:image/png") else resource_extension(ref.source)
        rendered = rendered.replace(f"__IMAGE_{ref.index}__", f"![{ref.alt}](assets/image-{ref.index:03d}{extension})")
    for ref in attachments:
        rendered = rendered.replace(f"__ATTACHMENT_{ref.index}__", f"[{ref.name}](attachments/attachment-{ref.index:03d}{resource_extension(ref.source)})")
    return rendered, images, attachments

def make_progress_payload(current: int, total: int, images_found: int, images_saved: int, attachments_found: int, attachments_saved: int, message: str) -> dict[str, Any]:
    return {
        "event": "task.progress", "level": "info", "provider": PROVIDER_ID,
        "message": message, "progress": {"current": current, "total": total},
        "stats": {"documents": total, "imagesFound": images_found, "imagesSaved": images_saved, "attachmentsFound": attachments_found, "attachmentsSaved": attachments_saved},
    }


def emit_progress(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    emit(args, payload.get("message", "任务处理中"), event="task.progress", level=payload.get("level", "info"), progress=payload.get("progress", {}), stats=payload.get("stats", {}))


def build_download_request(url: str, referer: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"Accept": "*/*", "Referer": referer, "User-Agent": "Wandao Google Docs Plugin"})


def fetch_resource(url: str, referer: str) -> tuple[bytes, str, str]:
    if not is_allowed_resource_url(url):
        raise GoogleDocsError("资源地址不在 Google Docs/Drive 允许范围内。")
    with urllib.request.urlopen(build_download_request(url, referer), timeout=30) as response:
        data = response.read(MAX_RESOURCE_BYTES + 1)
        if len(data) > MAX_RESOURCE_BYTES:
            raise GoogleDocsError("资源超过单个文件大小限制。")
        return data, str(response.headers.get_content_type() or "application/octet-stream"), str(response.geturl() or url)


def save_image(ref: ImageRef, target_dir: Path, source_url: str, resource_files: dict[str, bytes] | None = None) -> str:
    if is_data_image(ref.source):
        data, mime = decode_data_uri(ref.source)
    elif resource_files is not None and ref.source in resource_files:
        data = resource_files[ref.source]
        mime = mimetypes.guess_type(ref.source)[0] or "application/octet-stream"
    else:
        data, mime, _ = fetch_resource(ref.source, source_url)
    relative = f"assets/image-{ref.index:03d}{resource_extension(ref.source, mime)}"
    path = target_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return relative


def save_attachment(ref: AttachmentRef, target_dir: Path, source_url: str, resource_files: dict[str, bytes] | None = None) -> str:
    if not ref.source:
        raise GoogleDocsError("附件缺少目标地址。")
    if resource_files is not None and ref.source in resource_files:
        data = resource_files[ref.source]
        mime = mimetypes.guess_type(ref.source)[0] or "application/octet-stream"
    else:
        data, mime, _ = fetch_resource(ref.source, source_url)
    suffix = resource_extension(ref.name, mime)
    relative = f"attachments/attachment-{ref.index:03d}{suffix}"
    path = target_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return relative


def export_document_model(document: ExportedDocument, output: Path, title: str, *, source_url: str = ENTRY_URL, resource_files: dict[str, bytes] | None = None, progress_callback: Any | None = None) -> dict[str, Any]:
    markdown, images, attachments = render_markdown(document)
    document_dir = output / sanitize_filename(title, fallback="Google Docs 文档")
    document_dir.mkdir(parents=True, exist_ok=True)
    image_paths: dict[int, str] = {}
    attachment_paths: dict[int, str] = {}
    failures: list[dict[str, Any]] = []
    images_saved = attachments_saved = 0
    for ref in images:
        try:
            image_paths[ref.index] = save_image(ref, document_dir, source_url, resource_files)
            images_saved += 1
        except Exception as exc:  # noqa: BLE001
            failures.append({"kind": "image", "index": ref.index, "source": ref.source, "error": str(exc)})
        if progress_callback:
            progress_callback(make_progress_payload(1, 1, len(images), images_saved, len(attachments), attachments_saved, f"正在保存图片 {ref.index}/{len(images)}"))
    for ref in attachments:
        try:
            attachment_paths[ref.index] = save_attachment(ref, document_dir, source_url, resource_files)
            attachments_saved += 1
        except Exception as exc:  # noqa: BLE001
            failures.append({"kind": "attachment", "index": ref.index, "name": ref.name, "source": ref.source, "error": str(exc)})
        if progress_callback:
            progress_callback(make_progress_payload(1, 1, len(images), images_saved, len(attachments), attachments_saved, f"正在保存附件 {ref.index}/{len(attachments)}"))
    for ref in images:
        path = image_paths.get(ref.index)
        placeholder = f"![{ref.alt}](assets/image-{ref.index:03d}{resource_extension(ref.source)})"
        if path:
            markdown = markdown.replace(placeholder, f"![{ref.alt}]({path})")
            markdown = markdown.replace(f"![{ref.alt}](assets/image-{ref.index:03d}.bin)", f"![{ref.alt}]({path})")
        else:
            markdown = markdown.replace(placeholder, "")
            markdown = markdown.replace(f"![{ref.alt}](assets/image-{ref.index:03d}.bin)", "")
    for ref in attachments:
        path = attachment_paths.get(ref.index)
        placeholder = f"[{ref.name}](attachments/attachment-{ref.index:03d}{resource_extension(ref.source)})"
        if path:
            markdown = markdown.replace(placeholder, f"[{ref.name}]({path})")
            markdown = markdown.replace(f"[{ref.name}](attachments/attachment-{ref.index:03d}.bin)", f"[{ref.name}]({path})")
        else:
            fallback = f"[{ref.name}]({ref.source})" if is_safe_markdown_link(ref.source) else ref.name
            markdown = markdown.replace(placeholder, fallback)
            markdown = markdown.replace(f"[{ref.name}](attachments/attachment-{ref.index:03d}.bin)", fallback)
    markdown_path = document_dir / f"{sanitize_filename(title, fallback='Google Docs 文档')}.md"
    markdown_path.write_text(markdown, encoding="utf-8", newline="\n")
    return {
        "output": str(markdown_path), "documentDir": str(document_dir), "markdown": markdown,
        "imageCount": len(images), "imageSuccessCount": images_saved,
        "attachmentCount": len(attachments), "attachmentSuccessCount": attachments_saved,
        "resourceFailures": failures,
    }

def page_for_google_docs(port: int, preferred_url: str = "") -> dict[str, Any] | None:
    pages = http_json(f"http://127.0.0.1:{port}/json/list", timeout=5)
    preferred = urllib.parse.urlsplit(preferred_url)
    preferred_host = (preferred.hostname or "").lower().rstrip(".")
    preferred_path = preferred.path.rstrip("/")
    allowed_hosts = {DOCS_HOST, "accounts.google.com", "myaccount.google.com"}
    candidates = []
    for page in pages:
        if page.get("type") != "page":
            continue
        page_url = urllib.parse.urlsplit(str(page.get("url") or ""))
        host = (page_url.hostname or "").lower().rstrip(".")
        if host not in allowed_hosts:
            continue
        if preferred_host and host == preferred_host and page_url.path.rstrip("/") == preferred_path:
            return page
        candidates.append(page)
    if preferred_host:
        preferred_candidates = [page for page in candidates if (urllib.parse.urlsplit(str(page.get("url") or "")).hostname or "").lower().rstrip(".") == preferred_host]
        if preferred_candidates:
            return preferred_candidates[0]
    return candidates[0] if candidates else None


def find_available_debug_port(start: int) -> int:
    for port in range(max(1024, start), max(1024, start) + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise GoogleDocsError("没有可用的浏览器调试端口。")


def connect_google_docs_browser(args: argparse.Namespace, initial_url: str = ENTRY_URL) -> tuple[CDPClient, Any | None]:
    process = None
    port = int(args.port)
    page = page_for_google_docs(port, initial_url) if chrome_debug_available(port) else None
    if not page and chrome_debug_available(port):
        port = find_available_debug_port(port + 1)
        args.port = port
    if not chrome_debug_available(port):
        profile = Path(args.profile_dir).expanduser().resolve() if args.profile_dir else default_profile_path()
        process = start_chrome(port, profile, initial_url, getattr(args, "browser_path", "") or None, breakaway=True)
        wait_for_debug_port(port, timeout=30)
    page = page_for_google_docs(port, initial_url)
    if not page:
        open_tab(port, initial_url)
        time.sleep(1)
        page = page_for_google_docs(port, initial_url)
    if not page or not page.get("webSocketDebuggerUrl"):
        raise GoogleDocsError("无法找到或创建 Google Docs 浏览器标签页。")
    client = CDPClient(str(page["webSocketDebuggerUrl"]))
    client.connect()
    client.send("Runtime.enable")
    client.send("Page.enable")
    return client, process


def navigate_to_source(cdp: CDPClient, source: GoogleDocsSource, args: argparse.Namespace) -> None:
    cdp.navigate(source.canonical_url)
    deadline = time.time() + max(30, int(args.wait_seconds))
    while time.time() < deadline:
        check_stopped(args)
        state = cdp.evaluate("({url: location.href, host: location.hostname, readyState: document.readyState})", timeout=10) or {}
        current_host = str(state.get("host") or "").lower().rstrip(".") if isinstance(state, dict) else ""
        current_url = str(state.get("url") or "") if isinstance(state, dict) else ""
        if current_host == DOCS_HOST and "/document/d/" in urllib.parse.urlsplit(current_url).path and str(state.get("readyState")) in {"interactive", "complete"}:
            return
        time.sleep(0.5)
    raise GoogleDocsError("Google Docs 文档页面打开超时，请确认浏览器中可以访问该文档。")


def page_payload_expression() -> str:
    return r"""
(() => {
  const text = (node) => String(node?.innerText || node?.textContent || '').trim();
  const bodyText = text(document.body);
  const title = document.title || text(document.querySelector('title')) || 'Google Docs 文档';
  const loginRequired = /Sign in|登录|登入|Choose an account|选择账号/i.test(bodyText);
  const accessDenied = /You need access|Request access|需要访问权限|请求访问/i.test(bodyText);
  return { title, bodyText, loginRequired, accessDenied, url: location.href };
})()
"""


def read_page_state(cdp: CDPClient, args: argparse.Namespace) -> dict[str, Any]:
    deadline = time.time() + max(30, int(args.wait_seconds))
    while time.time() < deadline:
        check_stopped(args)
        state = cdp.evaluate(page_payload_expression(), timeout=20) or {}
        if isinstance(state, dict):
            if state.get("accessDenied"):
                raise GoogleDocsError("当前账号没有权限访问这篇 Google Docs 文档。")
            if not state.get("loginRequired"):
                return state
            emit(args, "请在插件打开的浏览器中完成 Google 登录。", event="auth.login.required", level="warn")
        time.sleep(1)
    raise GoogleDocsError("Google Docs 页面加载或登录等待超时。")


def download_exported_html(cdp: CDPClient, source: GoogleDocsSource, args: argparse.Namespace) -> tuple[str, str]:
    """Use the browser's authenticated Google Docs HTML export path.

    This deliberately avoids editor-DOM selectors. Chrome downloads the HTML
    export into the plugin-owned data directory; the ZIP is parsed locally.
    """
    state = read_page_state(cdp, args)
    download_dir = Path(args.download_dir).expanduser().resolve()
    download_dir.mkdir(parents=True, exist_ok=True)
    before = {path.name for path in download_dir.iterdir() if path.is_file()}
    export_url = f"https://{DOCS_HOST}/document/d/{source.document_id}/export?format=html"
    cdp.send("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(download_dir)})
    cdp.navigate(export_url)
    deadline = time.time() + max(30, int(args.wait_seconds))
    downloaded: Path | None = None
    while time.time() < deadline:
        check_stopped(args)
        candidates = [path for path in download_dir.iterdir() if path.is_file() and path.name not in before and not path.name.endswith(".crdownload")]
        if candidates:
            downloaded = max(candidates, key=lambda path: path.stat().st_mtime)
            break
        time.sleep(0.5)
    if downloaded is None:
        raise GoogleDocsError("Google Docs HTML 导出下载超时或被拒绝。")
    try:
        with zipfile.ZipFile(downloaded) as archive:
            html_names = [name for name in archive.namelist() if name.lower().endswith((".html", ".htm"))]
            if not html_names:
                raise GoogleDocsError("Google Docs HTML 导出包中没有 HTML 正文。")
            html_name = next((name for name in html_names if Path(name).name.lower() == "index.html"), html_names[0])
            return archive.read(html_name).decode("utf-8", errors="replace"), str(state.get("title") or "Google Docs 文档")
    except zipfile.BadZipFile as exc:
        raise GoogleDocsError("Google Docs 导出结果不是有效的 HTML 压缩包。") from exc
    finally:
        try:
            downloaded.unlink()
        except OSError:
            pass

def login_state_is_complete(state: dict[str, Any] | None) -> bool:
    if not isinstance(state, dict):
        return False
    parsed = urllib.parse.urlsplit(str(state.get("url") or ""))
    host = (parsed.hostname or "").lower().rstrip(".")
    ready_state = str(state.get("readyState") or "").lower()
    title = str(state.get("title") or "")
    body_text = str(state.get("bodyText") or state.get("text") or "")
    if parsed.scheme.lower() != "https" or host not in {DOCS_HOST, "accounts.google.com", "myaccount.google.com"}:
        return False
    if ready_state not in {"interactive", "complete"}:
        return False
    if re.search(r"Sign in|登录|登入|Choose an account|选择账号|You need access|Request access|需要访问权限|请求访问", body_text, re.I):
        return False
    return bool(title.strip() or body_text.strip())


def wait_for_login_confirmation(cdp: CDPClient, args: argparse.Namespace, confirmation_reader: Any | None = None) -> bool:
    """Keep the login task alive until the UI explicitly confirms completion."""
    reader = confirmation_reader
    if reader is None:
        # The renderer sends a newline when the user clicks its confirmation
        # button.  input() is intentionally prompt-free so stdout stays JSON/log safe.
        def reader() -> str:
            return input()

    check_stopped(args)
    try:
        confirmation = reader()
    except (EOFError, OSError) as exc:
        raise GoogleDocsError("登录确认通道已关闭，请重新点击“登录并保存会话”。") from exc
    if confirmation is None:
        raise GoogleDocsError("尚未收到登录确认。")
    return True


def verify_login_session(cdp: CDPClient, args: argparse.Namespace) -> dict[str, Any]:
    """Navigate to the Docs origin and verify that the confirmed session works."""
    cdp.navigate(ENTRY_URL)
    deadline = time.time() + max(30, int(args.wait_seconds))
    last_state: dict[str, Any] = {}
    while time.time() < deadline:
        check_stopped(args)
        try:
            state = cdp.evaluate("({url: location.href, readyState: document.readyState, title: document.title, bodyText: document.body ? document.body.innerText : ''})", timeout=10) or {}
        except ExportError as exc:
            raise GoogleDocsError("登录浏览器已关闭或连接中断，请重新点击“登录并保存会话”。") from exc
        if isinstance(state, dict):
            last_state = state
            if login_state_is_complete(state):
                return state
        time.sleep(0.5)
    if last_state and re.search(r"Sign in|登录|登入|Choose an account|选择账号|You need access|Request access|需要访问权限|请求访问", str(last_state.get("bodyText") or ""), re.I):
        raise GoogleDocsError("未检测到可用的 Google 登录态，请先完成登录后再确认。")
    raise GoogleDocsError("登录确认后无法验证 Google Docs 页面，请重试。")


def should_close_started_browser_after_login() -> bool:
    return False

def run_login(args: argparse.Namespace) -> dict[str, Any]:
    cdp = None
    process = None
    try:
        cdp, process = connect_google_docs_browser(args, LOGIN_URL)
        cdp.navigate(LOGIN_URL)
        emit(args, "请在插件打开的浏览器中完成 Google 登录；完成后点击“我已完成登录，保存凭证”。", event="auth.login.started")
        wait_for_login_confirmation(cdp, args)
        verify_login_session(cdp, args)
        result = finalize_report({"platform": PLUGIN_ID, "totalDocs": 0, "successCount": 0, "failures": [], "resourceFailures": []}, provider=PROVIDER_ID, mode="login")
        emit(args, "Google 登录会话已准备完成。", event="auth.login.completed", level="success")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    finally:
        if cdp:
            cdp.close()
        if process and args.close_started_chrome:
            process.terminate()


def scan_document(args: argparse.Namespace) -> dict[str, Any]:
    source = parse_google_docs_url(args.source_url)
    cdp = None
    process = None
    try:
        cdp, process = connect_google_docs_browser(args, source.canonical_url)
        navigate_to_source(cdp, source, args)
        emit(args, "正在读取 Google Docs HTML 导出", event="document.scan.started")
        html_content, title = download_exported_html(cdp, source, args)
        document = parse_exported_html(html_content, title)
        _, images, attachments = render_markdown(document)
        result = finalize_report({
            "platform": PLUGIN_ID, "totalDocs": 1, "successCount": 1,
            "failures": [], "resourceFailures": [], "title": document.title,
            "nodes": build_document_nodes(source, document.title),
            "imagesFound": len(images), "attachmentsFound": len(attachments),
        }, provider=PROVIDER_ID, mode="scan")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    finally:
        if cdp:
            cdp.close()
        if process and args.close_started_chrome:
            process.terminate()


def export_document(args: argparse.Namespace) -> dict[str, Any]:
    source = parse_google_docs_url(args.source_url)
    output = Path(args.output).expanduser().resolve()
    cdp = None
    process = None
    started = time.time()
    try:
        emit_progress(args, make_progress_payload(0, 1, 0, 0, 0, 0, "正在打开 Google Docs 文档"))
        cdp, process = connect_google_docs_browser(args, source.canonical_url)
        navigate_to_source(cdp, source, args)
        emit_progress(args, make_progress_payload(0, 1, 0, 0, 0, 0, "正在读取文档结构"))
        html_content, title = download_exported_html(cdp, source, args)
        document = parse_exported_html(html_content, title)
        emit_progress(args, make_progress_payload(0, 1, 0, 0, 0, 0, "正在读取正文"))
        result_data = export_document_model(document, output, document.title, source_url=source.canonical_url, progress_callback=lambda payload: emit_progress(args, payload))
        emit_progress(args, make_progress_payload(1, 1, result_data["imageCount"], result_data["imageSuccessCount"], result_data["attachmentCount"], result_data["attachmentSuccessCount"], "正在生成 Markdown"))
        report_path = Path(result_data["documentDir"]) / "00-导出报告.json"
        report = finalize_report({
            "platform": PLUGIN_ID, "totalDocs": 1, "successCount": 1, "exportedDocs": 1,
            "failures": [], "resourceFailures": result_data["resourceFailures"],
            "output": result_data["output"], "title": document.title,
            "nodes": build_document_nodes(source, document.title),
            "imageCount": result_data["imageCount"], "imageSuccessCount": result_data["imageSuccessCount"],
            "attachmentCount": result_data["attachmentCount"], "attachmentSuccessCount": result_data["attachmentSuccessCount"],
            "elapsedSeconds": round(time.time() - started, 2),
        }, provider=PROVIDER_ID, mode="export", report_file=report_path, output=result_data["output"])
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        emit_progress(args, make_progress_payload(1, 1, result_data["imageCount"], result_data["imageSuccessCount"], result_data["attachmentCount"], result_data["attachmentSuccessCount"], "正在写入任务报告"))
        emit(args, "Google Docs 导出完成。" if not report["resourceFailures"] else "Google Docs 正文已导出，但有资源未保存。", event="task.completed", level="success" if not report["resourceFailures"] else "warn", reportFile=str(report_path), stats=report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report
    finally:
        if cdp:
            cdp.close()
        if process and args.close_started_chrome:
            process.terminate()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export one Google Docs document to Markdown")
    parser.add_argument("--login", action="store_true", help="Open the plugin browser for manual Google login")
    parser.add_argument("--scan-toc", action="store_true", help="Scan the single document node and resources")
    parser.add_argument("--source-url", default="", help="Google Docs document URL")
    parser.add_argument("--output", default=str(default_data_dir() / "exports" / "google-docs"), help="Output directory")
    parser.add_argument("--download-dir", default=str(default_data_dir() / "downloads" / "google-docs"), help="Plugin-owned temporary browser download directory")
    parser.add_argument("--node-id", default="", help="Optional single document node id")
    parser.add_argument("--progress-every", type=int, default=1, help="Progress message interval")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Dedicated browser debugging port")
    parser.add_argument("--profile-dir", default=str(default_profile_path()), help="Dedicated browser profile directory")
    parser.add_argument("--browser-path", default="", help="Chrome/Edge/Chromium executable path")
    parser.add_argument("--wait-seconds", type=int, default=300, help="Seconds to wait for login or page loading")
    parser.add_argument("--close-started-chrome", action="store_true", help="Close a browser started by this action")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.login:
            result = run_login(args)
        elif args.scan_toc:
            if not args.source_url:
                raise GoogleDocsError("扫描前请先填写 Google Docs 文档链接。")
            result = scan_document(args)
        else:
            if not args.source_url:
                raise GoogleDocsError("导出前请先填写 Google Docs 文档链接。")
            result = export_document(args)
        return 0 if not result.get("failures") else 1
    except ExportStopped as exc:
        emit(args, f"Google Docs 任务已停止：{exc}", event="task.stopped", level="warn")
        print(str(exc), file=sys.stderr, flush=True)
        return 130
    except ExportError as exc:
        emit(args, f"Google Docs 任务失败：{exc}", event="task.failed", level="error", error={"type": type(exc).__name__, "message": str(exc)})
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    except Exception as exc:  # noqa: BLE001
        emit(args, f"Google Docs 任务失败：{exc}", event="task.failed", level="error", error={"type": type(exc).__name__, "message": str(exc)})
        print(f"Google Docs 任务失败：{exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
