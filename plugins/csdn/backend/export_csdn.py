#!/usr/bin/env python3
"""Export one authorized CSDN blog article to local Markdown.

The exporter deliberately reads the rendered page through a dedicated browser
profile. It does not call CSDN private APIs, generate request signatures, or
copy browser cookies out of that profile.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import mimetypes
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

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
    sanitize_filename,
    start_chrome,
    throttle_request,
    wait_for_debug_port,
)
from wandao_core.checkpoint import add_checkpoint_args, open_checkpoint_from_args
from wandao_core.credentials import write_private_json
from wandao_core.report import finalize_report


PLUGIN_ID = "csdn"
PROVIDER_ID = "csdn-export"
ENTRY_URL = "https://blog.csdn.net/"
CSDN_BLOG_HOST = "blog.csdn.net"
DEFAULT_PORT = 9255
DEFAULT_PROFILE = ".csdn-chrome-profile"
DEFAULT_AUTH_FILE = ".csdn_auth.json"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
IMAGE_TIMEOUT_SECONDS = 15
DEFAULT_ACCESS_WAIT_SECONDS = 300
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
IMAGE_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/avif": ".avif",
}
# Regexes used while walking every HTML node — precompiled to avoid per-node cache churn.
_WHITESPACE_RE = re.compile(r"\s+")
_TRAILING_WS_BEFORE_NEWLINE_RE = re.compile(r"[ \t]+\n")
_LEADING_WS_AFTER_NEWLINE_RE = re.compile(r"\n[ \t]+")
_EXCESS_NEWLINES_RE = re.compile(r"\n{3,}")
_CODE_LANG_RE = re.compile(r"(?:language|lang)-([A-Za-z0-9_+-]+)")


@dataclass(frozen=True)
class CsdnSource:
    content_id: str
    canonical_url: str
    author_slug: str

    @property
    def item_key(self) -> str:
        return f"csdn:article:{self.content_id}"


@dataclass
class HtmlNode:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[HtmlNode | str] = field(default_factory=list)


@dataclass(frozen=True)
class ImageRef:
    token: str
    source: str
    alt: str
    fallback_sources: tuple[str, ...] = ()


class HtmlTreeParser(HTMLParser):
    """A small HTML tree builder for the subset used by rendered Csdn pages."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = HtmlNode("root")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        node = HtmlNode(name, {str(key).lower(): str(value or "") for key, value in attrs})
        self.stack[-1].children.append(node)
        if name not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
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


class CsdnMarkdownRenderer:
    """Convert the isolated article body HTML to portable Markdown."""

    def __init__(self, source_url: str) -> None:
        self.source_url = source_url
        self.images: list[ImageRef] = []
        self._image_tokens: dict[str, str] = {}

    def render(self, source_html: str) -> str:
        parser = HtmlTreeParser()
        parser.feed(source_html or "")
        parser.close()
        return self._normalize(self._render_children(parser.root))

    def _render_children(self, node: HtmlNode, *, in_pre: bool = False) -> str:
        return "".join(
            child if isinstance(child, str) and in_pre else self._render_node(child, in_pre=in_pre)
            if isinstance(child, HtmlNode)
            else self._inline_text(child)
            for child in node.children
        )

    def _render_node(self, node: HtmlNode, *, in_pre: bool = False) -> str:
        tag = node.tag
        if tag in {"script", "style", "noscript", "button", "svg", "path", "canvas", "form", "input"}:
            return ""
        if tag == "br":
            return "\n"
        if tag == "hr":
            return "\n\n---\n\n"
        if tag == "img":
            return self._render_image(node)
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = min(6, max(1, int(tag[1:])))
            value = self._inline(self._render_children(node)).strip()
            return f"\n\n{'#' * level} {value}\n\n" if value else ""
        if tag == "p":
            value = self._inline(self._render_children(node)).strip()
            return f"\n\n{value}\n\n" if value else ""
        if tag == "blockquote":
            value = self._normalize(self._render_children(node)).strip()
            if not value:
                return ""
            quoted = "\n".join(">" if not line else f"> {line}" for line in value.splitlines())
            return f"\n\n{quoted}\n\n"
        if tag == "pre":
            language = self._code_language(node)
            content = self._text(node).strip("\n")
            if not content:
                return ""
            fence = "```"
            while fence in content:
                fence += "`"
            return f"\n\n{fence}{language}\n{content}\n{fence}\n\n"
        if tag == "code":
            if in_pre:
                return self._text(node)
            value = self._text(node).strip()
            if not value:
                return ""
            fence = "``" if "`" in value else "`"
            return f"{fence}{value}{fence}"
        if tag in {"strong", "b"}:
            value = self._inline(self._render_children(node)).strip()
            return f"**{value}**" if value else ""
        if tag in {"em", "i"}:
            value = self._inline(self._render_children(node)).strip()
            return f"*{value}*" if value else ""
        if tag in {"del", "s", "strike"}:
            value = self._inline(self._render_children(node)).strip()
            return f"~~{value}~~" if value else ""
        if tag == "a":
            label = self._inline(self._render_children(node)).strip() or self._inline_text(node.attrs.get("title", ""))
            target = self._safe_link(node.attrs.get("href", ""))
            if not target:
                return label
            return f"[{label or target}]({self._markdown_url(target)})"
        if tag in {"ul", "ol"}:
            return self._render_list(node, ordered=tag == "ol")
        if tag == "li":
            return self._inline(self._render_children(node)).strip()
        if tag == "table":
            return self._render_table(node)
        if tag == "figcaption":
            value = self._inline(self._render_children(node)).strip()
            return f"\n\n*{value}*\n\n" if value else ""
        if tag in {"div", "article", "section", "main", "figure", "details", "summary"}:
            value = self._render_children(node)
            return f"\n\n{value}\n\n" if tag in {"figure", "details", "summary"} and value.strip() else value
        return self._render_children(node, in_pre=in_pre)

    def _render_list(self, node: HtmlNode, *, ordered: bool) -> str:
        items = [child for child in node.children if isinstance(child, HtmlNode) and child.tag == "li"]
        if not items:
            return ""
        try:
            start = max(1, int(node.attrs.get("start", "1") or "1"))
        except ValueError:
            start = 1
        lines: list[str] = []
        for index, item in enumerate(items):
            inline_parts: list[str] = []
            nested_parts: list[str] = []
            for child in item.children:
                if isinstance(child, HtmlNode) and child.tag in {"ul", "ol"}:
                    nested_parts.append(self._render_list(child, ordered=child.tag == "ol").strip())
                elif isinstance(child, HtmlNode):
                    inline_parts.append(self._render_node(child))
                else:
                    inline_parts.append(self._inline_text(child))
            text = self._inline("".join(inline_parts)).strip()
            marker = f"{start + index}. " if ordered else "- "
            if text:
                item_lines = text.splitlines()
                lines.append(marker + item_lines[0])
                lines.extend("  " + value for value in item_lines[1:] if value.strip())
            else:
                lines.append(marker.rstrip())
            for nested in nested_parts:
                if nested:
                    lines.extend("  " + value for value in nested.splitlines())
        return "\n\n" + "\n".join(lines) + "\n\n"

    def _render_table(self, node: HtmlNode) -> str:
        rows = self._table_rows(node)
        if not rows:
            return ""
        rendered: list[list[str]] = []
        has_header = False
        for cells, row_has_header in rows:
            values = [self._inline(self._render_children(cell)).replace("|", "\\|").strip() for cell in cells]
            if values:
                rendered.append(values)
                has_header = has_header or row_has_header
        if not rendered:
            return ""
        width = max(len(row) for row in rendered)
        normalized = [row + [""] * (width - len(row)) for row in rendered]
        header = normalized[0]
        body = normalized[1:]
        if not has_header:
            body = normalized[1:]
        lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
        lines.extend("| " + " | ".join(row) + " |" for row in body)
        return "\n\n" + "\n".join(lines) + "\n\n"

    def _table_rows(self, node: HtmlNode) -> list[tuple[list[HtmlNode], bool]]:
        result: list[tuple[list[HtmlNode], bool]] = []
        for child in node.children:
            if not isinstance(child, HtmlNode):
                continue
            if child.tag == "tr":
                cells = [item for item in child.children if isinstance(item, HtmlNode) and item.tag in {"th", "td"}]
                result.append((cells, any(cell.tag == "th" for cell in cells)))
            elif child.tag in {"thead", "tbody", "tfoot"}:
                result.extend(self._table_rows(child))
        return result

    def _render_image(self, node: HtmlNode) -> str:
        candidates: list[str] = []
        for key in ("data-actualsrc", "data-original", "data-src", "src", "srcset"):
            candidate = str(node.attrs.get(key, "") or "").strip()
            if key == "srcset" and candidate:
                candidate = candidate.split(",", 1)[0].strip().split(" ", 1)[0]
            if candidate.startswith("//"):
                candidate = f"https:{candidate}"
            if candidate:
                candidates.append(normalize_csdn_image_url(candidate))
        alt = self._inline_text(node.attrs.get("alt", "")) or "CSDN图片"
        trusted = list(dict.fromkeys(candidate for candidate in candidates if self._is_image_url(candidate)))
        if trusted:
            source = trusted[0]
            token = self._image_tokens.get(source)
            if not token:
                token = f"__WANDAO_CSDN_IMAGE_{len(self.images) + 1:03d}__"
                self._image_tokens[source] = token
                self.images.append(ImageRef(token=token, source=source, alt=alt, fallback_sources=tuple(trusted[1:])))
            return f"\n\n![{alt}]({token})\n\n"
        source = candidates[0] if candidates else ""
        remote = self._safe_link(source)
        return f"\n\n![{alt}]({self._markdown_url(remote)})\n\n" if remote else ""

    @staticmethod
    def _is_image_url(value: str) -> bool:
        parsed = urllib.parse.urlsplit(str(value or ""))
        host = (parsed.hostname or "").lower()
        return parsed.scheme == "https" and (host == "csdnimg.cn" or host.endswith(".csdnimg.cn"))

    def _safe_link(self, value: str) -> str:
        source = html.unescape(str(value or "")).strip()
        if not source or any(char in source for char in "\r\n"):
            return ""
        parsed = urllib.parse.urlsplit(source)
        if parsed.scheme.lower() in {"https", "http", "mailto"}:
            return source
        if source.startswith("/") and not source.startswith("//"):
            return urllib.parse.urljoin(self.source_url, source)
        return ""

    @staticmethod
    def _markdown_url(value: str) -> str:
        return value.replace("\\", "/").replace(" ", "%20").replace("(", "%28").replace(")", "%29")

    @staticmethod
    def _inline_text(value: str) -> str:
        return _WHITESPACE_RE.sub(" ", html.unescape(str(value or "")).replace("\xa0", " "))

    def _inline(self, value: str) -> str:
        text = _TRAILING_WS_BEFORE_NEWLINE_RE.sub("\n", value)
        text = _LEADING_WS_AFTER_NEWLINE_RE.sub("\n", text)
        text = _EXCESS_NEWLINES_RE.sub("\n\n", text)
        return text

    def _text(self, node: HtmlNode) -> str:
        values: list[str] = []
        for child in node.children:
            if isinstance(child, str):
                values.append(child)
            elif child.tag == "br":
                values.append("\n")
            else:
                values.append(self._text(child))
        return "".join(values)

    @staticmethod
    def _code_language(node: HtmlNode) -> str:
        classes = node.attrs.get("class", "").split()
        for value in classes:
            match = _CODE_LANG_RE.search(value)
            if match:
                return match.group(1).lower()
        for child in node.children:
            if isinstance(child, HtmlNode):
                for value in child.attrs.get("class", "").split():
                    match = _CODE_LANG_RE.search(value)
                    if match:
                        return match.group(1).lower()
        return ""

    @staticmethod
    def _normalize(value: str) -> str:
        text = value.replace("\r\n", "\n").replace("\r", "\n")
        text = _TRAILING_WS_BEFORE_NEWLINE_RE.sub("\n", text)
        text = _EXCESS_NEWLINES_RE.sub("\n\n", text)
        return text.strip() + "\n" if text.strip() else ""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def default_profile_path() -> Path:
    override = os.environ.get("CSDN_PROFILE_DIR", "").strip()
    return Path(override).expanduser().resolve() if override else default_data_dir() / DEFAULT_PROFILE


def default_auth_path() -> Path:
    return default_data_dir() / DEFAULT_AUTH_FILE


def auth_path_from_args(args: argparse.Namespace) -> Path:
    return Path(args.auth_file).expanduser().resolve() if args.auth_file else default_auth_path().resolve()


def parse_csdn_url(value: str) -> CsdnSource:
    raw = str(value or "").strip()
    try:
        parsed = urllib.parse.urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ExportError("CSDN链接端口格式无效。") from exc
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https" or parsed.username or parsed.password or port not in {None, 443}:
        raise ExportError("请输入 HTTPS 的 CSDN 博客文章链接。")
    path = urllib.parse.unquote(parsed.path or "")
    path_match = re.fullmatch(r"/([^/]+)/article/details/(\d+)/?", path)
    if host == CSDN_BLOG_HOST and path_match:
        author_slug, content_id = path_match.groups()
        encoded_author = urllib.parse.quote(author_slug, safe="-._~")
        return CsdnSource(content_id, f"https://{CSDN_BLOG_HOST}/{encoded_author}/article/details/{content_id}", author_slug)

    subdomain_match = re.fullmatch(r"([a-z0-9][a-z0-9-]*)\.blog\.csdn\.net", host)
    article_match = re.fullmatch(r"/article/details/(\d+)/?", path)
    if subdomain_match and article_match:
        author_slug = subdomain_match.group(1)
        content_id = article_match.group(1)
        return CsdnSource(content_id, f"https://{author_slug}.{CSDN_BLOG_HOST}/article/details/{content_id}", author_slug)

    if host == CSDN_BLOG_HOST or host.endswith(f".{CSDN_BLOG_HOST}"):
        raise ExportError("当前仅支持单篇 CSDN 博客文章（/作者/article/details/文章ID 或作者子域 /article/details/文章ID），不支持主页、专栏、搜索和评论页。")
    raise ExportError("仅支持 blog.csdn.net 的单篇博客文章链接。")


def page_matches_source(payload: dict[str, Any], source: CsdnSource) -> bool:
    """Return true only when the browser still displays the requested item."""
    try:
        current = parse_csdn_url(str(payload.get("url") or ""))
    except ExportError:
        return False
    return current.content_id == source.content_id


def is_csdn_image_url(value: str) -> bool:
    return CsdnMarkdownRenderer._is_image_url(value)


def safe_resource_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(str(value or ""))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def normalize_csdn_image_url(value: str) -> str:
    """A fragment is never part of the media request and should not enter Markdown."""
    parsed = urllib.parse.urlsplit(str(value or ""))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def is_csdn_blog_host(host: str) -> bool:
    normalized = str(host or "").lower()
    return normalized == CSDN_BLOG_HOST or normalized.endswith(f".{CSDN_BLOG_HOST}")


def page_for_csdn(port: int, preferred_url: str = "") -> dict[str, Any] | None:
    pages = http_json(f"http://127.0.0.1:{port}/json/list", timeout=5)
    preferred = urllib.parse.urlsplit(preferred_url)
    preferred_host = (preferred.hostname or "").lower()
    preferred_path = preferred.path.rstrip("/")
    candidates = []
    access_pages = []
    for page in pages:
        page_url = urllib.parse.urlsplit(str(page.get("url") or ""))
        if page.get("type") != "page":
            continue
        host = (page_url.hostname or "").lower()
        if host == "passport.csdn.net":
            access_pages.append(page)
            continue
        if not is_csdn_blog_host(host):
            continue
        if preferred_host and (page_url.hostname or "").lower() == preferred_host and page_url.path.rstrip("/") == preferred_path:
            return page
        candidates.append(page)
    return candidates[0] if candidates else (access_pages[0] if access_pages else None)


def find_available_debug_port(start: int) -> int:
    for port in range(max(1024, start), max(1024, start) + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise ExportError("没有可用的 CSDN 浏览器调试端口，请关闭占用端口的浏览器后重试。")


def open_csdn_target(cdp: CDPClient, target_url: str, args: argparse.Namespace) -> None:
    try:
        expected_source: CsdnSource | None = parse_csdn_url(target_url)
    except ExportError:
        expected_source = None
    expected_host = (urllib.parse.urlsplit(target_url).hostname or "").lower()
    throttle_request(args)
    cdp.navigate(target_url)
    for _ in range(30):
        check_stopped(args)
        state = cdp.evaluate("({url: location.href, readyState: document.readyState})", timeout=6)
        target_matches = (
            page_matches_source(state, expected_source)
            if isinstance(state, dict) and expected_source
            else isinstance(state, dict)
            and (urllib.parse.urlsplit(str(state.get("url") or "")).hostname or "").lower() == expected_host
        )
        current_host = (urllib.parse.urlsplit(str(state.get("url") or "")).hostname or "").lower() if isinstance(state, dict) else ""
        if target_matches or current_host == "passport.csdn.net":
            if str(state.get("readyState") or "") in {"interactive", "complete"}:
                return
        time.sleep(0.5)
    raise ExportError("CSDN 目标页面打开超时，请确认浏览器中能正常访问该链接后重试。")


def connect_csdn_browser(args: argparse.Namespace, initial_url: str = ENTRY_URL) -> tuple[CDPClient, Any | None]:
    process = None
    port = int(args.port)
    page = page_for_csdn(port, initial_url) if chrome_debug_available(port) else None
    if not page and chrome_debug_available(port):
        # Do not attach a stale debugging port that belongs to another provider.
        port = find_available_debug_port(port + 1)
        args.port = port
    if not chrome_debug_available(port):
        profile = Path(args.profile_dir).expanduser().resolve() if args.profile_dir else default_profile_path()
        process = start_chrome(port, profile, initial_url, getattr(args, "browser_path", "") or None)
        wait_for_debug_port(port, timeout=30)
    page = page_for_csdn(port, initial_url)
    if not page:
        open_tab(port, initial_url)
        time.sleep(1)
        page = page_for_csdn(port, initial_url)
    if not page or not page.get("webSocketDebuggerUrl"):
        raise ExportError("无法找到或创建 CSDN 网页标签页。")
    client = CDPClient(str(page["webSocketDebuggerUrl"]))
    client.connect()
    client.send("Runtime.enable")
    client.send("Page.enable")
    open_csdn_target(client, initial_url, args)
    return client, process


def page_payload_expression(source: CsdnSource) -> str:
    return f"""
(() => {{
  const trim = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const readable = (node) => trim(node?.innerText || node?.textContent || '');
  const article = document.querySelector('main article') || document.querySelector('article');
  const selectors = ['#content_views', '#article_content .markdown_views', 'main article .article_content', 'article .article_content'];
  let root = null;
  for (const selector of selectors) {{
    root = document.querySelector(selector);
    if (root && trim(root.innerText).length >= 20) break;
  }}
  let structured = {{}};
  for (const node of document.querySelectorAll('script[type="application/ld+json"]')) {{
    try {{
      const value = JSON.parse(node.textContent || '{{}}');
      const values = Array.isArray(value) ? value : [value, ...(value['@graph'] || [])];
      structured = values.find((item) => item && (item.headline || item.articleBody || item['@type'] === 'Article')) || structured;
    }} catch (_error) {{}}
  }}
  const pageTitle = readable(document.querySelector('h1.title-article')) || readable(article?.querySelector('h1')) || trim(structured.headline || document.title).replace(/\\s*[-_]?\\s*CSDN博客\\s*$/, '');
  const authorValue = structured.author?.name || structured.author || '';
  const author = typeof authorValue === 'string' ? trim(authorValue) : trim(authorValue?.name || '');
  const bodyText = trim(document.body?.innerText || '');
  const verificationRequired = !root && /安全验证|请完成(?:安全)?验证|人机验证|验证码|验证异常|滑动验证|访问频繁|异常请求/.test(bodyText);
  const loginRequired = !root && !verificationRequired && (location.hostname === 'passport.csdn.net' || /登录后(?:查看|阅读|继续)|请先登录|登录即可|请登录/.test(bodyText));
  const copyright = (bodyText.match(/本内容遵循[^。\\n]{{0,80}}版权协议/) || [''])[0];
  return {{
    url: location.href,
    title: pageTitle,
    author,
    publishedAt: structured.datePublished || '',
    updatedAt: structured.dateModified || '',
    copyright,
    html: root?.innerHTML || '',
    textLength: trim(root?.innerText).length,
    verificationRequired,
    loginRequired
  }};
}})()
"""


def wait_for_page_payload(cdp: CDPClient, source: CsdnSource, args: argparse.Namespace) -> dict[str, Any]:
    wait_seconds = max(30, int(getattr(args, "verification_wait_seconds", DEFAULT_ACCESS_WAIT_SECONDS) or DEFAULT_ACCESS_WAIT_SECONDS))
    deadline = time.time() + wait_seconds
    last_payload: dict[str, Any] = {}
    expression = page_payload_expression(source)
    waiting_state = ""
    while time.time() < deadline:
        check_stopped(args)
        try:
            payload = cdp.evaluate(expression, timeout=12)
        except ExportError as exc:
            last_payload = {"error": str(exc)}
        else:
            if isinstance(payload, dict):
                last_payload = payload
                if payload.get("verificationRequired"):
                    if waiting_state != "verification":
                        waiting_state = "verification"
                        emit(args, "CSDN 正在要求安全验证。请在本插件打开的浏览器中完成验证；完成后保持或返回该文章页，插件会自动继续。", event="auth.verification.required", level="warn")
                    time.sleep(0.5)
                    continue
                if payload.get("loginRequired"):
                    if waiting_state != "login":
                        waiting_state = "login"
                        emit(args, "CSDN 要求登录。请在本插件打开的浏览器中完成登录；完成后保持或返回该文章页，插件会自动继续。", event="auth.login.required", level="warn")
                    time.sleep(0.5)
                    continue
                if not page_matches_source(payload, source):
                    last_payload["targetMismatch"] = True
                    continue
                if len(str(payload.get("html") or "").strip()) >= 8 and int(payload.get("textLength") or 0) > 0:
                    return payload
        time.sleep(0.5)
    if waiting_state == "verification":
        raise ExportError(f"等待 CSDN 安全验证超过 {wait_seconds} 秒。请完成验证后重新导出。")
    if waiting_state == "login":
        raise ExportError(f"等待 CSDN 登录超过 {wait_seconds} 秒。请完成登录后重新导出。")
    if last_payload.get("targetMismatch"):
        raise ExportError("CSDN 页面已跳转到非目标内容，未导出该页面。请在本插件浏览器中完成登录或验证后重试。")
    raise ExportError(f"CSDN 正文没有在 {wait_seconds} 秒内加载完成。请确认页面可访问，或在本插件浏览器中完成登录或验证后重试。")


def save_auth_summary(args: argparse.Namespace) -> dict[str, Any]:
    profile = Path(args.profile_dir).expanduser().resolve() if args.profile_dir else default_profile_path().resolve()
    auth_file = auth_path_from_args(args)
    write_private_json(
        auth_file,
        {
            "version": 1,
            "savedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "profileDir": str(profile),
            "loggedIn": True,
        },
    )
    return {"authFile": str(auth_file), "loggedIn": True}


def verify_login_session(cdp: CDPClient) -> None:
    state = cdp.evaluate(
        """
(() => {
  const host = String(location.hostname || '').toLowerCase();
  const hasSessionCookie = document.cookie.split(';').some((item) => /^(UserName|UserInfo|UserToken|CSDN_USER)=/.test(item.trim()));
  const hasProfile = Boolean(document.querySelector('[class*="toolbar"] [class*="avatar"], [class*="toolbar"] [class*="user-name"], [class*="toolbar"] [class*="nickName"], [class*="toolbar"] [class*="nickname"]'));
  return { csdnHost: host === 'blog.csdn.net' || host.endsWith('.blog.csdn.net') || host === 'www.csdn.com', loggedIn: hasSessionCookie || hasProfile };
})()
""",
        timeout=10,
    )
    if not isinstance(state, dict) or not state.get("csdnHost") or not state.get("loggedIn"):
        raise ExportError("未检测到有效的CSDN登录态。请在打开的浏览器中完成登录后，再点击保存凭证。")


def run_login(args: argparse.Namespace) -> dict[str, Any]:
    cdp, process = connect_csdn_browser(args, ENTRY_URL)
    try:
        cdp.navigate(ENTRY_URL)
        emit(args, "请在浏览器中完成CSDN登录；登录成功后回到万能导确认保存会话。", event="auth.login.started")
        wait_seconds = max(0, int(getattr(args, "login_wait_seconds", 0) or 0))
        if wait_seconds:
            deadline = time.time() + wait_seconds
            while time.time() < deadline:
                check_stopped(args)
                time.sleep(1)
        else:
            # Desktop stdout is parsed for structured logs and the final JSON.
            # Keep input() prompt-free so it cannot corrupt that result payload.
            input()
        check_stopped(args)
        verify_login_session(cdp)
        summary = save_auth_summary(args)
        emit(args, "CSDN登录会话已保存。", event="auth.login.completed", level="success")
        return finalize_report({"platform": "csdn", **summary}, provider=PROVIDER_ID, mode="login")
    finally:
        cdp.close()
        if process and args.close_started_chrome:
            process.terminate()


def read_limited_response(response: Any, max_bytes: int = MAX_IMAGE_BYTES) -> bytes:
    try:
        declared_size = int(str(response.headers.get("Content-Length") or "0") or "0")
    except ValueError:
        declared_size = 0
    if declared_size > max_bytes:
        raise ExportError(f"图片超过大小限制（{max_bytes // 1024 // 1024} MB）")
    raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ExportError(f"图片超过大小限制（{max_bytes // 1024 // 1024} MB）")
    return raw


def download_image(source: str, page_url: str) -> tuple[bytes, str, str]:
    current = source
    opener = urllib.request.build_opener(NoRedirect())
    for _ in range(4):
        if not is_csdn_image_url(current):
            raise ExportError("图片跳转到了不受信任的域名")
        request = urllib.request.Request(
            current,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Referer": page_url,
            },
        )
        try:
            with opener.open(request, timeout=IMAGE_TIMEOUT_SECONDS) as response:
                content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if content_type not in IMAGE_CONTENT_TYPES:
                    raise ExportError(f"图片响应类型无效：{content_type or '未知类型'}")
                return read_limited_response(response), content_type, current
        except urllib.error.HTTPError as exc:
            if exc.code not in {301, 302, 303, 307, 308}:
                raise ExportError(f"图片下载返回 HTTP {exc.code}") from exc
            location = str(exc.headers.get("Location") or "")
            if not location:
                raise ExportError(f"图片下载返回 HTTP {exc.code}，但没有跳转地址") from exc
            current = urllib.parse.urljoin(current, location)
    raise ExportError("图片重定向次数过多")


def image_extension(content_type: str, source: str) -> str:
    if content_type in IMAGE_CONTENT_TYPES:
        return IMAGE_CONTENT_TYPES[content_type]
    suffix = Path(urllib.parse.urlsplit(source).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    guessed = mimetypes.guess_extension(content_type) if content_type else None
    return guessed or ".img"


def rewrite_images(
    markdown: str,
    images: list[ImageRef],
    md_path: Path,
    args: argparse.Namespace,
    source: CsdnSource,
    checkpoint: Any | None = None,
) -> tuple[str, list[dict[str, str]], int]:
    rewritten = markdown
    failures: list[dict[str, str]] = []
    saved = 0
    item_key = source.item_key
    assets_dir = md_path.with_name(f"{md_path.stem}_assets")
    for index, image in enumerate(images, start=1):
        check_stopped(args)
        source_url = image.source
        resource_key = f"{item_key}:image:{hashlib.sha256(source_url.encode('utf-8')).hexdigest()[:20]}"
        if not getattr(args, "download_images", False):
            rewritten = rewritten.replace(image.token, CsdnMarkdownRenderer._markdown_url(source_url))
            continue
        try:
            throttle_request(args)
            if checkpoint:
                checkpoint.upsert_resource(item_key, resource_key, "image", safe_resource_url(source_url), metadata={"index": index})
                checkpoint.start_resource(resource_key)
            raw = content_type = final_url = None
            last_error: Exception | None = None
            for candidate in (source_url, *image.fallback_sources):
                try:
                    raw, content_type, final_url = download_image(candidate, source.canonical_url)
                    break
                except ExportStopped:
                    raise
                except Exception as exc:  # noqa: BLE001 - try the page's safe display-size image before giving up.
                    last_error = exc
            if raw is None or content_type is None or final_url is None:
                assert last_error is not None
                raise last_error
            assets_dir.mkdir(parents=True, exist_ok=True)
            target = assets_dir / f"image-{index:03d}{image_extension(content_type, final_url)}"
            target.write_bytes(raw)
            relative = f"{assets_dir.name}/{target.name}"
            rewritten = rewritten.replace(image.token, CsdnMarkdownRenderer._markdown_url(relative))
            saved += 1
            if checkpoint:
                checkpoint.complete_resource(resource_key, local_path=str(target), target=relative, metadata={"contentType": content_type})
            emit(args, f"CSDN图片已保存：{target.name}", event="resource.download.completed", resource={"type": "image", "index": index, "path": str(target)})
        except ExportStopped:
            if checkpoint:
                checkpoint.fail_resource(resource_key, "stopped")
            raise
        except Exception as exc:  # noqa: BLE001 - preserve the article with its remote image link.
            rewritten = rewritten.replace(image.token, CsdnMarkdownRenderer._markdown_url(source_url))
            failure = {"source": safe_resource_url(source_url), "error": str(exc), "index": str(index)}
            failures.append(failure)
            if checkpoint:
                checkpoint.fail_resource(resource_key, str(exc))
            emit(args, f"CSDN图片下载失败，已保留远程链接：{exc}", event="resource.download.failed", level="warn", resource={"type": "image", "index": index}, error={"type": type(exc).__name__, "message": str(exc)})
    return rewritten, failures, saved


def build_front_matter(payload: dict[str, Any], source: CsdnSource) -> str:
    fields = [
        ("title", str(payload.get("title") or "CSDN内容")),
        ("source", source.canonical_url),
        ("content_type", "article"),
        ("author_slug", source.author_slug),
    ]
    for key, payload_key in (("author", "author"), ("published_at", "publishedAt"), ("updated_at", "updatedAt"), ("copyright", "copyright")):
        value = str(payload.get(payload_key) or "").strip()
        if value:
            fields.append((key, value))
    fields.append(("exported_at", time.strftime("%Y-%m-%dT%H:%M:%S%z")))
    return "---\n" + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields) + "\n---\n\n"


def markdown_matches_source(path: Path, source: CsdnSource) -> bool:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            prefix = handle.read(8192)
    except OSError:
        return False
    if not prefix.startswith("---\n"):
        return False
    header_end = prefix.find("\n---\n", 4)
    if header_end < 0:
        return False
    for line in prefix[4:header_end].splitlines():
        if not line.startswith("source: "):
            continue
        try:
            return str(json.loads(line[len("source: ") :])) == source.canonical_url
        except json.JSONDecodeError:
            return False
    return False


def markdown_path(output: Path, title: str, source: CsdnSource) -> Path:
    filename = sanitize_filename(str(title or "CSDN内容"), fallback="CSDN内容", max_len=110)
    candidate = output / f"{filename}.md"
    if not candidate.exists() or markdown_matches_source(candidate, source):
        return candidate
    disambiguated = f"{filename} [article-{source.content_id}]"
    for index in range(1, 1000):
        suffix = "" if index == 1 else f" ({index})"
        candidate = output / f"{disambiguated}{suffix}.md"
        if not candidate.exists() or markdown_matches_source(candidate, source):
            return candidate
    raise ExportError("无法为CSDN导出文件分配安全的唯一文件名。")


def export_csdn(args: argparse.Namespace) -> dict[str, Any]:
    source = parse_csdn_url(args.source_url)
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    started = time.time()
    checkpoint = open_checkpoint_from_args(args, PROVIDER_ID, "export")
    cdp: CDPClient | None = None
    process = None
    exported = skipped = image_success = 0
    failures: list[dict[str, str]] = []
    image_failures: list[dict[str, str]] = []
    md_path: Path | None = None
    item_key = source.item_key
    try:
        if checkpoint:
            checkpoint.start_task({"source": source.canonical_url, "outputDir": str(output), "totalDocs": 1, "resume": bool(args.resume)})
            checkpoint.upsert_item(item_key, title=source.content_id, source_url=source.canonical_url, source_id=source.content_id)
            status = checkpoint.item_status(item_key)
            if args.retry_failed and status and status != "failed":
                skipped = 1
                emit(args, "CSDN任务没有可重试的失败项。", event="task.skipped", level="warn")
                return finish_export(
                    output, source, started, exported, skipped, image_success, failures, image_failures, checkpoint, md_path, args
                )
            if args.resume and status == "completed":
                skipped = 1
                emit(args, "CSDN单篇内容已完成，继续任务时跳过。", event="task.skipped")
                return finish_export(
                    output, source, started, exported, skipped, image_success, failures, image_failures, checkpoint, md_path, args
                )
            checkpoint.start_item(item_key, "content")
        emit(args, "正在打开 CSDN 文章并读取正文…", event="document.export.started", doc={"id": source.content_id, "type": "article"})
        cdp, process = connect_csdn_browser(args, source.canonical_url)
        payload = wait_for_page_payload(cdp, source, args)
        title = str(payload.get("title") or f"CSDN文章-{source.content_id}").strip()
        md_path = markdown_path(output, title, source)
        if args.incremental and md_path.exists() and markdown_matches_source(md_path, source) and not args.retry_failed:
            skipped = 1
            if checkpoint:
                checkpoint.complete_item(item_key, local_path=str(md_path), metadata={"skippedExisting": True})
            emit(args, f"目标文件已存在，已跳过：{md_path.name}", event="document.export.skipped", doc={"id": source.content_id, "path": str(md_path)})
        else:
            renderer = CsdnMarkdownRenderer(source.canonical_url)
            body = renderer.render(str(payload.get("html") or ""))
            if len(body.strip()) < 20:
                raise ExportError("CSDN页面没有返回可导出的正文。")
            markdown = build_front_matter(payload, source) + f"# {title}\n\n" + body
            markdown, image_failures, image_success = rewrite_images(markdown, renderer.images, md_path, args, source, checkpoint)
            md_path.write_text(markdown, encoding="utf-8", newline="\n")
            exported = 1
            if checkpoint:
                if image_failures:
                    checkpoint.fail_item(item_key, f"{len(image_failures)} 个图片下载失败")
                else:
                    checkpoint.complete_item(item_key, local_path=str(md_path), metadata={"images": image_success})
            emit(
                args,
                f"CSDN内容导出完成：{md_path.name}",
                event="document.export.completed",
                level="success" if not image_failures else "warn",
                doc={"id": source.content_id, "type": "article", "path": str(md_path)},
                stats={"imageSuccess": image_success, "imageFailureCount": len(image_failures)},
            )
    except ExportStopped:
        if checkpoint:
            checkpoint.fail_item(item_key, "stopped")
            checkpoint.fail_task("stopped", status="stopped")
            checkpoint.close()
        raise
    except Exception as exc:  # noqa: BLE001 - return a report that the task center can render.
        failures.append({"source": source.canonical_url, "title": source.content_id, "error": str(exc)})
        if checkpoint:
            checkpoint.fail_item(item_key, str(exc))
        emit(args, f"CSDN 内容导出失败：{exc}", event="document.export.failed", level="error", doc={"id": source.content_id, "type": "article"}, error={"type": type(exc).__name__, "message": str(exc)})
    finally:
        if cdp:
            cdp.close()
        if process and args.close_started_chrome:
            process.terminate()
    return finish_export(output, source, started, exported, skipped, image_success, failures, image_failures, checkpoint, md_path, args)


def finish_export(
    output: Path,
    source: CsdnSource,
    started: float,
    exported: int,
    skipped: int,
    image_success: int,
    failures: list[dict[str, str]],
    image_failures: list[dict[str, str]],
    checkpoint: Any | None,
    md_path: Path | None,
    args: argparse.Namespace,
) -> dict[str, Any]:
    try:
        return write_report(
            output, source, started, exported, skipped, image_success, failures, image_failures, checkpoint, md_path, args
        )
    finally:
        if checkpoint:
            checkpoint.close()


def write_report(
    output: Path,
    source: CsdnSource,
    started: float,
    exported: int,
    skipped: int,
    image_success: int,
    failures: list[dict[str, str]],
    image_failures: list[dict[str, str]],
    checkpoint: Any | None,
    md_path: Path | None,
    args: argparse.Namespace,
) -> dict[str, Any]:
    report_path = output / "00-导出报告.json"
    report = finalize_report(
        {
            "platform": "csdn",
            "sourceUrl": source.canonical_url,
            "contentType": "article",
            "total": 1,
            "exported": exported,
            "skipped": skipped,
            "imageSuccess": image_success,
            "imageFailureCount": len(image_failures),
            "failures": failures,
            "imageFailures": image_failures,
            "elapsedSeconds": round(time.time() - started, 2),
            "checkpoint": checkpoint.stats() if checkpoint else {},
        },
        provider=PROVIDER_ID,
        mode="export",
        report_file=report_path,
        output=output,
    )
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if checkpoint:
        if failures or image_failures:
            checkpoint.fail_task(f"{len(failures)} 个内容失败，{len(image_failures)} 个图片失败")
        else:
            checkpoint.complete_task(report)
    emit(
        args,
        "CSDN导出完成" if not failures else "CSDN导出完成，但正文读取失败。",
        event="task.completed",
        level="success" if not failures and not image_failures else "warn",
        reportFile=str(report_path),
        stats={"exportedDocs": exported, "skippedDocs": skipped, "imageSuccess": image_success, "failureCount": len(failures), "imageFailureCount": len(image_failures)},
    )
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出单篇 CSDN 博客文章为 Markdown")
    parser.add_argument("--gui", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--login", action="store_true", help="打开CSDN网页并保存已确认的登录会话摘要")
    parser.add_argument("--login-wait-seconds", type=int, default=0, help="非交互登录时等待保存会话的秒数")
    parser.add_argument("--source-url", default="", help="CSDN 博客文章链接")
    parser.add_argument("--output", default=str(default_data_dir() / "exports" / "csdn"), help="输出目录")
    parser.add_argument("--download-images", dest="download_images", action="store_true", default=True, help="下载正文图片到本地")
    parser.add_argument("--no-download-images", dest="download_images", action="store_false", help="保留正文图片的远程链接")
    parser.add_argument("--incremental", action="store_true", help="目标 Markdown 已存在时跳过")
    add_checkpoint_args(parser)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="CSDN专用 Chrome 调试端口")
    parser.add_argument("--profile-dir", default=str(default_profile_path()), help="CSDN专用浏览器配置目录")
    parser.add_argument("--browser-path", default="", help="Chrome/Edge/Chromium 可执行文件路径")
    parser.add_argument("--auth-file", default=str(default_auth_path()), help="登录会话摘要文件（不含 Cookie 或 Token）")
    parser.add_argument("--progress-every", type=int, default=1, help="保留统一任务接口的进度参数")
    parser.add_argument("--request-delay", type=float, default=0.8, help="页面和图片请求延迟秒")
    parser.add_argument("--request-jitter", type=float, default=0.2, help="请求随机浮动秒")
    parser.add_argument("--verification-wait-seconds", type=int, default=DEFAULT_ACCESS_WAIT_SECONDS, help="等待用户完成 CSDN 登录或安全验证的秒数")
    parser.add_argument("--close-started-chrome", action="store_true", help="任务结束后关闭本插件启动的浏览器")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.login:
            result = run_login(args)
        else:
            if not args.source_url:
                raise ExportError("导出前请先填写 CSDN 博客文章链接。")
            result = export_csdn(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not result.get("failures") else 1
    except ExportStopped as exc:
        emit(args, f"CSDN导出已停止：{exc}", event="task.stopped", level="warn")
        print(str(exc), file=sys.stderr, flush=True)
        return 130
    except ExportError as exc:
        emit(args, f"CSDN导出失败：{exc}", event="task.failed", level="error", error={"type": type(exc).__name__, "message": str(exc)})
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    except Exception as exc:  # noqa: BLE001 - final CLI boundary for the desktop task runner.
        emit(args, f"CSDN导出失败：{exc}", event="task.failed", level="error", error={"type": type(exc).__name__, "message": str(exc)})
        print(f"CSDN导出失败：{exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
