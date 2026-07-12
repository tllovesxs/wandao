#!/usr/bin/env python3
# Author: tllovesxs
"""
Import local Markdown documents into a Yuque knowledge base.

The complete importer uses the same browser-login cookies as the Yuque exporter.
It mirrors a user's normal web actions: create/update documents, upload local
resources, and rebuild the catalog tree through Yuque's web endpoints.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import html
import json
import mimetypes
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from pathlib import Path
from typing import Any, Callable

from wandao_core.browser import (
    ExportError,
    ExportStopped,
    check_stopped,
    default_data_dir,
    default_state_path,
    emit,
    sanitize_filename,
    stop_requested,
)
from export_yuque import (
    DEFAULT_PORT,
    auth_path_from_args,
    cookies_for_url,
    default_auth_path,
    default_profile_path,
    login_and_save_auth,
    normalize_book_url,
    parse_book_url,
    read_auth_cookies,
)
from wandao_core.report import finalize_report
from wandao_core.credentials import write_private_json
from wandao_core.checkpoint import add_checkpoint_args, open_checkpoint_from_args


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR
DEFAULT_SOURCE_DIR = default_data_dir() / "exports" / "yuque"
DEFAULT_CONFIG_FILE = default_state_path(".yuque_import_config.json")
YUQUE_HOME_URL = "https://www.yuque.com"
OBSIDIAN_EMBED_RE = re.compile(r"!\[\[([^\]\n]+)\]\]")
HTML_IMG_RE = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", re.I)
EXPORT_FOOTER_RE = re.compile(r"\n---\n\n来源:[\s\S]*$", re.M)
DEFAULT_REQUEST_TIMEOUT = 90
DEFAULT_UPLOAD_TIMEOUT = 180
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_DELAY = 1.2
DEFAULT_UPLOAD_CONCURRENCY = 2
FAILURE_LOG_LIMIT = 8
COMMON_FAILURE_ABORT_AFTER = 10
REMOTE_IMAGE_POLICIES = {"link", "keep", "remove"}
MARKDOWN_DOC_EXTENSIONS = {".md", ".markdown", ".mdown"}

REQUEST_TIMEOUT = DEFAULT_REQUEST_TIMEOUT
UPLOAD_TIMEOUT = DEFAULT_UPLOAD_TIMEOUT
RETRY_ATTEMPTS = DEFAULT_RETRY_ATTEMPTS
RETRY_DELAY = DEFAULT_RETRY_DELAY
UPLOAD_CONCURRENCY = DEFAULT_UPLOAD_CONCURRENCY

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def default_config_path() -> Path:
    return DEFAULT_CONFIG_FILE


def load_config(config_file: Path) -> dict[str, Any]:
    if not config_file.exists():
        return {}
    try:
        return json.loads(config_file.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(config_file: Path, data: dict[str, Any]) -> None:
    safe_data = {key: value for key, value in data.items() if value not in (None, "")}
    write_private_json(config_file, safe_data)


def configure_runtime(args: argparse.Namespace) -> None:
    global REQUEST_TIMEOUT, UPLOAD_TIMEOUT, RETRY_ATTEMPTS, RETRY_DELAY, UPLOAD_CONCURRENCY
    REQUEST_TIMEOUT = max(10, int(getattr(args, "request_timeout", DEFAULT_REQUEST_TIMEOUT) or DEFAULT_REQUEST_TIMEOUT))
    UPLOAD_TIMEOUT = max(30, int(getattr(args, "upload_timeout", DEFAULT_UPLOAD_TIMEOUT) or DEFAULT_UPLOAD_TIMEOUT))
    RETRY_ATTEMPTS = max(1, int(getattr(args, "retry_attempts", DEFAULT_RETRY_ATTEMPTS) or DEFAULT_RETRY_ATTEMPTS))
    RETRY_DELAY = max(0.0, float(getattr(args, "retry_delay", DEFAULT_RETRY_DELAY) or DEFAULT_RETRY_DELAY))
    UPLOAD_CONCURRENCY = min(4, max(1, int(getattr(args, "upload_concurrency", DEFAULT_UPLOAD_CONCURRENCY) or DEFAULT_UPLOAD_CONCURRENCY)))


def retry_sleep(attempt: int) -> None:
    if RETRY_DELAY <= 0:
        return
    time.sleep(RETRY_DELAY * attempt)


def parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text) if text else None
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def format_yuque_api_error(status: int, raw: str) -> str:
    data = parse_json_object(raw)
    if not data:
        return f"语雀接口 HTTP {status}：{raw[:1200]}"

    code = str(data.get("code") or "").strip()
    message = str(data.get("message") or data.get("msg") or "").strip()
    limit = data.get("limit")
    details = []
    if code:
        details.append(f"code={code}")
    if message:
        details.append(f"message={message}")
    if limit not in (None, ""):
        details.append(f"limit={limit}")
    if details:
        return f"语雀接口 HTTP {status}：" + "，".join(details) + f"，raw={raw[:1200]}"
    return f"语雀接口 HTTP {status}：{raw[:1200]}"


def is_retryable_http(code: int) -> bool:
    return code in {408, 429, 500, 502, 503, 504}


def config_args(args: argparse.Namespace) -> dict[str, Any]:
    data = load_config(Path(args.config_file))
    if getattr(args, "target_book_url", None):
        data["targetBookUrl"] = args.target_book_url
    if getattr(args, "source_dir", None):
        data["sourceDir"] = args.source_dir
    if getattr(args, "auth_file", None):
        data["authFile"] = args.auth_file
    if getattr(args, "profile_dir", None):
        data["profileDir"] = args.profile_dir
    if getattr(args, "browser_path", None):
        data["browserPath"] = args.browser_path
    return data


def parse_target_book(value: str | None) -> tuple[str, str, str]:
    if not value:
        raise ExportError("请填写目标语雀知识库 URL")
    group_slug, book_slug, normalized = parse_book_url(value)
    host = urllib.parse.urlparse(normalized).netloc or "www.yuque.com"
    return host, f"{group_slug}/{book_slug}", normalized


def load_auth_cookies_or_fail(args: argparse.Namespace, host: str) -> list[dict[str, Any]]:
    auth_file = auth_path_from_args(args)
    cookies = read_auth_cookies(auth_file)
    cookie_header = cookies_for_url(cookies, f"https://{host}/")
    if not cookie_header:
        raise ExportError(f"没有可用的语雀登录凭证：{auth_file}。请先点击“登录并保存凭证”。")
    return cookies


def request_json(
    host: str,
    cookies: list[dict[str, Any]],
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    referer: str | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    url = f"https://{host}{path}"
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if method not in ("GET", "HEAD") else None
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Cookie": cookies_for_url(cookies, url),
    }
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    raw = ""
    timeout = timeout or REQUEST_TIMEOUT
    last_error: BaseException | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
            break
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            if exc.code == 401:
                raise ExportError(f"语雀登录已失效，请重新点击“登录并保存凭证”后重试。HTTP 401：{raw[:800]}") from exc
            last_error = exc
            if attempt < RETRY_ATTEMPTS and is_retryable_http(exc.code):
                retry_sleep(attempt)
                continue
            raise ExportError(format_yuque_api_error(exc.code, raw)) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            last_error = exc
            if attempt < RETRY_ATTEMPTS:
                retry_sleep(attempt)
                continue
            raise ExportError(f"请求语雀接口失败：{exc}") from exc
    else:
        raise ExportError(f"请求语雀接口失败：{last_error}")
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise ExportError(f"语雀接口返回非 JSON：{raw[:500]}") from exc
    if isinstance(data, dict) and data.get("status") and int(data.get("status") or 0) >= 400:
        raise ExportError(f"语雀接口返回错误：{data.get('message') or data}")
    return data


def compact_error(error: Any, limit: int = 600) -> str:
    text = str(error or "未知错误").replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


def failure_signature(error: Any) -> str:
    text = compact_error(error, 220)
    text = re.sub(r'"[^"]{1,120}"', '"..."', text)
    text = re.sub(r"'[^']{1,120}'", "'...'", text)
    text = re.sub(r"\b\d{4,}\b", "<num>", text)
    return text[:220]


def fatal_yuque_error_message(error: Any) -> str:
    text = compact_error(error, 1200)
    if "max_doc_note_number" in text or "文档数超过限制" in text:
        limit_match = re.search(r"(?:limit[=:]|\"limit\"\s*:\s*)(\d+)", text)
        limit_text = f"当前限制 {limit_match.group(1)} 篇" if limit_match else "当前知识库或账号已达到文档数量限制"
        return f"语雀文档数超过限制（{limit_text}），请清理目标知识库、升级语雀空间，或换一个可写知识库后重试。"
    return ""


def write_json_report(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    report = finalize_report(data, provider="yuque-import", mode="import", report_file=path, output=data.get("sourceDir"))
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def request_text(host: str, cookies: list[dict[str, Any]], path_or_url: str, *, timeout: int | None = None) -> str:
    url = path_or_url if path_or_url.startswith("http") else f"https://{host}{path_or_url}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Cookie": cookies_for_url(cookies, url),
        },
    )
    timeout = timeout or REQUEST_TIMEOUT
    last_error: BaseException | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            if exc.code == 401:
                raise ExportError(f"语雀登录已失效，请重新点击“登录并保存凭证”后重试。HTTP 401：{raw[:800]}") from exc
            last_error = exc
            if attempt < RETRY_ATTEMPTS and is_retryable_http(exc.code):
                retry_sleep(attempt)
                continue
            raise ExportError(f"读取语雀页面 HTTP {exc.code}：{raw[:800]}") from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            last_error = exc
            if attempt < RETRY_ATTEMPTS:
                retry_sleep(attempt)
                continue
            raise ExportError(f"读取语雀页面失败：{exc}") from exc
    raise ExportError(f"读取语雀页面失败：{last_error}")


def parse_app_data(page_html: str) -> dict[str, Any]:
    match = re.search(r"window\.appData\s*=\s*JSON\.parse\(decodeURIComponent\(\"(.+?)\"\)\)", page_html)
    if not match:
        raise ExportError("没有从语雀页面中解析到 appData，可能未登录或页面结构变化。")
    decoded = urllib.parse.unquote(match.group(1))
    return json.loads(decoded)


def load_target_book(host: str, cookies: list[dict[str, Any]], book_url: str) -> dict[str, Any]:
    page_html = request_text(host, cookies, book_url)
    app_data = parse_app_data(page_html)
    book = app_data.get("book") or {}
    toc = app_data.get("book", {}).get("toc") or []
    if not book.get("id"):
        raise ExportError("没有读取到目标语雀知识库 ID，请确认账号有访问/编辑权限。")
    return {
        "book": {"id": book.get("id"), "name": book.get("name"), "slug": book.get("slug")},
        "toc": toc,
        "group": app_data.get("group") or {},
        "me": {"id": (app_data.get("me") or {}).get("id")},
    }


def clean_title(raw: str) -> str:
    title = re.sub(r"^\s*\d+[-_.、\s]+", "", raw).strip()
    return title or raw.strip() or "未命名"


def title_from_markdown(path: Path, text: str) -> str:
    for line in text.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            return clean_title(match.group(1))
    return clean_title(path.stem)


def strip_export_footer(text: str) -> str:
    return EXPORT_FOOTER_RE.sub("", text).rstrip() + "\n"


def slug_for_relative_path(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/")
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:10]
    stem = Path(normalized).stem
    stem = re.sub(r"^\d+[-_.、\s]+", "", stem)
    ascii_part = re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-").lower()
    if ascii_part:
        return f"wandao-{ascii_part[:42]}-{digest}"
    return f"wandao-{digest}"


def is_remote_or_anchor(target: str) -> bool:
    target = normalize_link_target(target)
    parsed = urllib.parse.urlparse(target)
    return bool(parsed.scheme in ("http", "https", "data", "mailto") or target.startswith("#"))


def normalize_link_target(target: str) -> str:
    target = html.unescape(str(target or "")).strip()
    if target.startswith("<"):
        close = target.find(">")
        if close > 0:
            return unescape_markdown_target(target[1:close].strip())
    # Markdown allows an optional title after the URL, for example:
    # ![alt](assets/a.png "title"). Keep spaces in filenames unless this exact title form is present.
    match = re.match(r"^(?P<path>.+?)\s+\"[^\"]*\"\s*$", target) or re.match(r"^(?P<path>.+?)\s+'[^']*'\s*$", target)
    if match:
        path = match.group("path").strip()
        if path:
            return unescape_markdown_target(path.strip("<>"))
    return unescape_markdown_target(target.strip("<>"))


def unescape_markdown_target(target: str) -> str:
    return re.sub(r"\\([\\(){}\[\]#.!_`*+\- ])", r"\1", target)


def strip_local_url_suffix(target: str) -> str:
    parsed = urllib.parse.urlparse(target)
    return parsed.path if not parsed.scheme else target


def is_markdown_doc_target(target: str) -> bool:
    normalized = normalize_link_target(target)
    if not normalized or is_remote_or_anchor(normalized):
        return False
    candidates = [normalized, strip_local_url_suffix(normalized)]
    return any(Path(urllib.parse.unquote(candidate)).suffix.lower() in MARKDOWN_DOC_EXTENSIONS for candidate in candidates)


def resolve_local_target(md_path: Path, target: str) -> Path | None:
    target = normalize_link_target(target)
    if not target or is_remote_or_anchor(target):
        return None
    candidates = [target]
    stripped = strip_local_url_suffix(target)
    if stripped and stripped != target:
        candidates.append(stripped)
    for candidate in candidates:
        parsed = urllib.parse.urlparse(candidate)
        if parsed.scheme:
            continue
        local_path = (md_path.parent / urllib.parse.unquote(candidate)).resolve()
        if local_path.exists() and local_path.is_file():
            return local_path
    return None


def iter_markdown_scan_segments(markdown: str) -> list[tuple[int, str]]:
    segments: list[tuple[int, str]] = []
    in_fence = False
    segment_start: int | None = None
    segment_parts: list[str] = []
    offset = 0
    for line in markdown.splitlines(keepends=True):
        if re.match(r"^\s{0,3}(```|~~~)", line):
            if not in_fence and segment_parts and segment_start is not None:
                segments.append((segment_start, "".join(segment_parts)))
                segment_parts = []
                segment_start = None
            in_fence = not in_fence
            offset += len(line)
            continue
        if not in_fence:
            if segment_start is None:
                segment_start = offset
            segment_parts.append(line)
        offset += len(line)
    if segment_parts and segment_start is not None:
        segments.append((segment_start, "".join(segment_parts)))
    return segments


def iter_markdown_links(markdown: str) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for base, segment in iter_markdown_scan_segments(markdown):
        index = 0
        while index < len(segment):
            bang = segment.startswith("![", index)
            plain = segment.startswith("[", index)
            if not bang and not plain:
                index += 1
                continue
            label_start = index + (2 if bang else 1)
            label_end = segment.find("]", label_start)
            if label_end < 0 or label_end + 1 >= len(segment) or segment[label_end + 1] != "(":
                index += 1
                continue
            target_start = label_end + 2
            cursor = target_start
            depth = 0
            escaped = False
            while cursor < len(segment):
                char = segment[cursor]
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == "(":
                    depth += 1
                elif char == ")":
                    if depth == 0:
                        break
                    depth -= 1
                elif char in "\r\n":
                    break
                cursor += 1
            if cursor >= len(segment) or segment[cursor] != ")":
                index += 1
                continue
            links.append(
                {
                    "marker": "!" if bang else "",
                    "label": segment[label_start:label_end],
                    "target": segment[target_start:cursor],
                    "start": base + index,
                    "end": base + cursor + 1,
                }
            )
            index = cursor + 1
    return links


def iter_extra_image_links(markdown: str) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for base, segment in iter_markdown_scan_segments(markdown):
        for match in OBSIDIAN_EMBED_RE.finditer(segment):
            target = match.group(1).split("|", 1)[0].strip()
            links.append({"marker": "!", "label": Path(target).name, "target": target, "start": base + match.start(), "end": base + match.end(), "syntax": "obsidian"})
        for match in HTML_IMG_RE.finditer(segment):
            target = html.unescape(match.group(1).strip())
            links.append({"marker": "!", "label": Path(target).name, "target": target, "start": base + match.start(), "end": base + match.end(), "syntax": "html"})
    return links


def is_image_path(path: Path) -> bool:
    mime, _ = mimetypes.guess_type(path.name)
    return bool(mime and mime.startswith("image/"))


def scan_local_resources(markdown: str, md_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    resources: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for link in iter_markdown_links(markdown) + iter_extra_image_links(markdown):
        target = str(link.get("target") or "")
        normalized_target = normalize_link_target(target)
        local_path = resolve_local_target(md_path, target)
        if is_markdown_doc_target(normalized_target):
            # Markdown files are imported as documents, not uploaded as attachments.
            continue
        if not local_path:
            if normalized_target and not is_remote_or_anchor(normalized_target):
                warnings.append({"target": normalized_target, "reason": "local_file_missing"})
            continue
        key = str(local_path)
        if key in seen:
            aliases = seen[key].setdefault("targets", [])
            if normalized_target and normalized_target not in aliases:
                aliases.append(normalized_target)
            continue
        kind = "image" if link.get("marker") == "!" or is_image_path(local_path) else "attachment"
        resource = {
            "path": local_path,
            "target": normalized_target,
            "targets": [normalized_target] if normalized_target else [],
            "kind": kind,
            "title": link.get("label") or local_path.name,
            "syntax": link.get("syntax") or "markdown",
        }
        resources.append(resource)
        seen[key] = resource
    return resources, warnings


def scan_markdown_docs(source_dir: Path) -> list[dict[str, Any]]:
    if not source_dir.exists():
        raise ExportError(f"Markdown 目录不存在：{source_dir}")
    docs: list[dict[str, Any]] = []
    for path in sorted(source_dir.rglob("*.md"), key=lambda p: str(p.relative_to(source_dir)).lower()):
        rel_parts = path.relative_to(source_dir).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        if path.name.startswith("00-") and ("导入报告" in path.name or "导出报告" in path.name):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        body = strip_export_footer(text)
        resources, resource_warnings = scan_local_resources(body, path)
        relative = path.relative_to(source_dir).as_posix()
        docs.append(
            {
                "path": str(path),
                "relativePath": relative,
                "title": title_from_markdown(path, body),
                "slug": slug_for_relative_path(relative),
                "body": body,
                "level": max(0, len(rel_parts) - 1),
                "folders": [clean_title(Path(part).stem) for part in rel_parts[:-1]],
                "size": path.stat().st_size,
                "resources": resources,
                "resourceWarnings": resource_warnings,
            }
        )
    if not docs:
        raise ExportError(f"没有在目录中找到 Markdown 文件：{source_dir}")
    return docs


def markdown_safe_label(value: str) -> str:
    label = re.sub(r"\s+", " ", str(value or "").strip()) or "远程图片"
    return label.replace("[", "\\[").replace("]", "\\]")


def remote_image_replacement(link: dict[str, Any], policy: str) -> str:
    target = normalize_link_target(str(link.get("target") or ""))
    if policy == "remove":
        return f"<!-- 远程图片已跳过：{target} -->"
    label = markdown_safe_label(str(link.get("label") or Path(urllib.parse.urlparse(target).path).name or "远程图片"))
    return f"[{label}]({target})"


def rewrite_remote_images(markdown: str, policy: str) -> tuple[str, int]:
    if policy == "keep":
        return markdown, 0
    replacements: list[tuple[int, int, str]] = []
    for link in iter_markdown_links(markdown) + iter_extra_image_links(markdown):
        target = normalize_link_target(str(link.get("target") or ""))
        if link.get("marker") == "!" and is_remote_or_anchor(target):
            replacements.append((int(link["start"]), int(link["end"]), remote_image_replacement(link, policy)))
    if not replacements:
        return markdown, 0
    result = markdown
    for start, end, replacement in sorted(replacements, reverse=True):
        result = result[:start] + replacement + result[end:]
    return result, len(replacements)


def multipart_body(fields: dict[str, Any], files: list[tuple[str, str, bytes, str]]) -> tuple[str, bytes]:
    boundary = "----wandao" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode("utf-8")
        )
    for field, filename, data, content_type in files:
        safe_filename = filename.replace('"', "")
        chunks.append(
            (
                f"--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"{field}\"; filename=\"{safe_filename}\"\r\n"
                f"Content-Type: {content_type or 'application/octet-stream'}\r\n\r\n"
            ).encode("utf-8")
        )
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return boundary, b"".join(chunks)


def upload_resource(
    host: str,
    cookies: list[dict[str, Any]],
    file_path: Path,
    doc_id: int,
    book_url: str,
) -> dict[str, Any]:
    url = f"https://{host}/api/upload/attach"
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    boundary, body = multipart_body(
        {"attachable_type": "Doc", "attachable_id": str(doc_id)},
        [("file", file_path.name, file_path.read_bytes(), content_type)],
    )
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Cookie": cookies_for_url(cookies, url),
            "Referer": book_url,
        },
    )
    raw = ""
    last_error: BaseException | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=UPLOAD_TIMEOUT) as response:
                raw = response.read().decode("utf-8", errors="replace")
            break
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            if exc.code == 401:
                raise ExportError(f"语雀登录已失效，请重新点击“登录并保存凭证”后重试。HTTP 401：{raw[:800]}") from exc
            last_error = exc
            if attempt < RETRY_ATTEMPTS and is_retryable_http(exc.code):
                retry_sleep(attempt)
                continue
            raise ExportError(f"上传资源失败 HTTP {exc.code}：{raw[:1000]}") from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            last_error = exc
            if attempt < RETRY_ATTEMPTS:
                retry_sleep(attempt)
                continue
            raise ExportError(f"上传资源失败：{exc}") from exc
    else:
        raise ExportError(f"上传资源失败：{last_error}")
    data = json.loads(raw or "{}")
    item = data.get("data") or {}
    if not item.get("url"):
        raise ExportError(f"上传资源返回异常：{raw[:1000]}")
    return item


def upload_doc_resources(
    host: str,
    cookies: list[dict[str, Any]],
    resources: list[dict[str, Any]],
    doc_id: int,
    book_url: str,
) -> tuple[dict[str, dict[str, Any]], int, int]:
    uploads: dict[str, dict[str, Any]] = {}
    image_count = attachment_count = 0
    if not resources:
        return uploads, image_count, attachment_count

    def upload_one(resource: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        path = Path(resource["path"])
        try:
            return resource, upload_resource(host, cookies, path, doc_id, book_url)
        except Exception as exc:
            raise ExportError(f"{path.name} 上传失败：{exc}") from exc

    if UPLOAD_CONCURRENCY <= 1 or len(resources) <= 1:
        results = [upload_one(resource) for resource in resources]
    else:
        results = []
        max_workers = min(UPLOAD_CONCURRENCY, len(resources))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(upload_one, resource) for resource in resources]
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())

    for resource, uploaded in results:
        uploads[str(resource["path"])] = uploaded
        if resource.get("kind") == "image":
            image_count += 1
        else:
            attachment_count += 1
    return uploads, image_count, attachment_count


def replace_resource_links(
    markdown: str,
    resources: list[dict[str, Any]],
    uploads: dict[str, dict[str, Any]],
) -> str:
    by_target: dict[str, dict[str, str]] = {}
    for resource in resources:
        uploaded = uploads.get(str(resource["path"]))
        if uploaded and uploaded.get("url"):
            aliases = list(resource.get("targets") or []) or [resource.get("target")]
            for alias in aliases:
                normalized_target = normalize_link_target(str(alias or ""))
                if normalized_target:
                    by_target[normalized_target] = {
                        "url": str(uploaded["url"]),
                        "kind": str(resource.get("kind") or ""),
                        "title": str(resource.get("title") or ""),
                    }

    replacements = []
    for link in iter_markdown_links(markdown) + iter_extra_image_links(markdown):
        target = normalize_link_target(str(link.get("target") or ""))
        uploaded = by_target.get(target)
        if not uploaded:
            continue
        label = str(link.get("label") or uploaded.get("title") or Path(target).name)
        marker = "!" if uploaded.get("kind") == "image" else str(link.get("marker") or "")
        replacements.append((int(link["start"]), int(link["end"]), f"{marker}[{label}]({uploaded['url']})"))

    if not replacements:
        return markdown

    result = markdown
    for start, end, replacement in sorted(replacements, reverse=True):
        result = result[:start] + replacement + result[end:]
    return result


def existing_docs_by_slug(host: str, cookies: list[dict[str, Any]], book_id: int, book_url: str) -> dict[str, dict[str, Any]]:
    data = request_json(host, cookies, "GET", f"/api/docs?book_id={book_id}", referer=book_url)
    docs = data.get("data") or []
    return {str(doc.get("slug")): doc for doc in docs if doc.get("slug")}


def catalog_node_for_doc(toc: list[dict[str, Any]], doc_id: int) -> dict[str, Any] | None:
    for item in toc:
        if str(item.get("doc_id") or item.get("id") or "") == str(doc_id):
            return item
    return None


def refresh_catalog(host: str, cookies: list[dict[str, Any]], book_id: int, book_url: str) -> list[dict[str, Any]]:
    data = request_json(host, cookies, "GET", f"/api/catalog_nodes?book_id={book_id}", referer=book_url)
    return data.get("data") or []


def extract_toc_from_catalog_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(data.get("data"), list):
        return data["data"]
    meta = data.get("meta") or {}
    if isinstance(meta.get("toc"), list):
        return meta["toc"]
    return []


def create_catalog_title(
    host: str,
    cookies: list[dict[str, Any]],
    book_id: int,
    title: str,
    book_url: str,
    parent_uuid: str | None = None,
) -> dict[str, Any]:
    payload = {"book_id": book_id, "action": "insert", "type": "TITLE", "title": title}
    data = request_json(host, cookies, "PUT", "/api/catalog_nodes", payload, referer=book_url)
    nodes = [node for node in extract_toc_from_catalog_response(data) if node.get("title") == title and node.get("type") == "TITLE"]
    if not nodes:
        raise ExportError(f"创建语雀目录失败：{title}")
    node = nodes[0]
    if parent_uuid:
        move_catalog_node(host, cookies, book_id, str(node["uuid"]), parent_uuid, book_url)
        node["parent_uuid"] = parent_uuid
    return node


def move_catalog_node(
    host: str,
    cookies: list[dict[str, Any]],
    book_id: int,
    node_uuid: str,
    target_uuid: str,
    book_url: str,
) -> None:
    payload = {"book_id": book_id, "action": "appendChild", "node_uuid": node_uuid, "target_uuid": target_uuid}
    request_json(host, cookies, "PUT", "/api/catalog_nodes", payload, referer=book_url)


def find_title_node(toc: list[dict[str, Any]], title: str, parent_uuid: str | None) -> dict[str, Any] | None:
    parent = parent_uuid or ""
    for item in toc:
        if item.get("type") != "TITLE":
            continue
        if (item.get("title") or "") == title and str(item.get("parent_uuid") or "") == parent:
            return item
    return None


def ensure_folder_path(
    host: str,
    cookies: list[dict[str, Any]],
    book_id: int,
    toc: list[dict[str, Any]],
    folders: list[str],
    book_url: str,
) -> tuple[str | None, list[dict[str, Any]]]:
    parent_uuid: str | None = None
    current_toc = toc
    for folder in folders:
        node = find_title_node(current_toc, folder, parent_uuid)
        if not node:
            node = create_catalog_title(host, cookies, book_id, folder, book_url, parent_uuid)
            current_toc = refresh_catalog(host, cookies, book_id, book_url)
        parent_uuid = str(node.get("uuid"))
    return parent_uuid, current_toc


def add_doc_to_catalog(
    host: str,
    cookies: list[dict[str, Any]],
    book_id: int,
    doc_id: int,
    book_url: str,
) -> dict[str, Any]:
    payload = {"book_id": book_id, "ids": [doc_id]}
    data = request_json(host, cookies, "POST", "/api/docs/add_to_catalog", payload, referer=book_url)
    toc = extract_toc_from_catalog_response(data) or refresh_catalog(host, cookies, book_id, book_url)
    node = catalog_node_for_doc(toc, doc_id)
    if not node:
        raise ExportError(f"文档已创建但没有加入目录：{doc_id}")
    return node


def create_doc(
    host: str,
    cookies: list[dict[str, Any]],
    book_id: int,
    title: str,
    slug: str,
    body: str,
    book_url: str,
) -> dict[str, Any]:
    payload = {"book_id": book_id, "title": title, "slug": slug, "body": body, "format": "markdown"}
    data = request_json(host, cookies, "POST", "/api/docs", payload, referer=book_url)
    doc = data.get("data") or {}
    if not doc.get("id"):
        detail = data.get("message") or data.get("error") or data
        raise ExportError(f"创建语雀文档失败：{title}。语雀返回：{compact_error(detail)}")
    return doc


def update_doc(
    host: str,
    cookies: list[dict[str, Any]],
    doc_id: int,
    book_id: int,
    title: str,
    slug: str,
    body: str,
    book_url: str,
) -> dict[str, Any]:
    payload = {"book_id": book_id, "title": title, "slug": slug, "body": body, "format": "markdown"}
    data = request_json(host, cookies, "PUT", f"/api/docs/{doc_id}", payload, referer=book_url)
    doc = data.get("data") or {}
    if not doc.get("id"):
        detail = data.get("message") or data.get("error") or data
        raise ExportError(f"更新语雀文档失败：{title}。语雀返回：{compact_error(detail)}")
    return doc


def import_one_doc(
    host: str,
    cookies: list[dict[str, Any]],
    book_id: int,
    book_url: str,
    doc: dict[str, Any],
    existing: dict[str, dict[str, Any]],
    toc: list[dict[str, Any]],
    update_existing: bool,
    skip_existing: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    placeholder = f"# {doc['title']}\n\n正在上传资源并写入正文...\n"
    existing_doc = existing.get(doc["slug"])
    action = "created"
    has_resources = bool(doc["resources"])
    if existing_doc:
        if skip_existing or not update_existing:
            return {"action": "skipped", "id": existing_doc.get("id"), "slug": doc["slug"], "title": doc["title"]}, toc
        doc_id = int(existing_doc["id"])
        action = "updated"
    else:
        created = create_doc(host, cookies, book_id, doc["title"], doc["slug"], placeholder if has_resources else doc["body"], book_url)
        doc_id = int(created["id"])
        existing[doc["slug"]] = created

    uploads, image_count, attachment_count = upload_doc_resources(host, cookies, doc["resources"], doc_id, book_url)

    if has_resources or existing_doc:
        body = replace_resource_links(doc["body"], doc["resources"], uploads)
        updated = update_doc(host, cookies, doc_id, book_id, doc["title"], doc["slug"], body, book_url)
    else:
        updated = created
    node = catalog_node_for_doc(toc, doc_id)
    if not node:
        node = add_doc_to_catalog(host, cookies, book_id, doc_id, book_url)
        toc = refresh_catalog(host, cookies, book_id, book_url)
    parent_uuid, toc = ensure_folder_path(host, cookies, book_id, toc, doc.get("folders") or [], book_url)
    if parent_uuid:
        node = catalog_node_for_doc(toc, doc_id) or node
        if str(node.get("parent_uuid") or "") != parent_uuid:
            move_catalog_node(host, cookies, book_id, str(node["uuid"]), parent_uuid, book_url)
            toc = refresh_catalog(host, cookies, book_id, book_url)

    return (
        {
            "action": action,
            "id": updated.get("id") or doc_id,
            "slug": updated.get("slug") or doc["slug"],
            "title": doc["title"],
            "imageUploads": image_count,
            "attachmentUploads": attachment_count,
        },
        toc,
    )


def checkpoint_item_key(doc: dict[str, Any]) -> str:
    return f"yuque-import:{doc['relativePath']}"


def select_checkpoint_docs(
    docs: list[dict[str, Any]],
    checkpoint: Any,
    *,
    resume: bool,
    retry_failed: bool,
) -> list[dict[str, Any]]:
    if not checkpoint:
        return docs
    if retry_failed:
        return [doc for doc in docs if checkpoint.item_status(checkpoint_item_key(doc)) == "failed"]
    if resume:
        return [doc for doc in docs if checkpoint.item_status(checkpoint_item_key(doc)) != "completed"]
    return docs


def import_docs(args: argparse.Namespace) -> dict[str, Any]:
    configure_runtime(args)
    config = config_args(args)
    target_url = config.get("targetBookUrl") or getattr(args, "target_book_url", None)
    host, namespace, book_url = parse_target_book(target_url)
    source_dir = Path(config.get("sourceDir") or getattr(args, "source_dir", None) or DEFAULT_SOURCE_DIR).resolve()
    report_path = source_dir / "00-语雀导入报告.json"
    plan_report_path = source_dir / "00-语雀导入计划.json"
    cookies = load_auth_cookies_or_fail(args, host)
    docs = scan_markdown_docs(source_dir)
    remote_image_policy = str(getattr(args, "remote_image_policy", "link") or "link").strip().lower()
    if remote_image_policy not in REMOTE_IMAGE_POLICIES:
        raise ExportError(f"未知远程图片处理策略：{remote_image_policy}")
    checkpoint = None
    if not (args.plan or args.dry_run):
        checkpoint = open_checkpoint_from_args(args, "yuque-import", "import")
    if checkpoint:
        checkpoint.start_task({"source": str(source_dir), "target": book_url, "totalDocs": len(docs)})
        for doc in docs:
            relative_path = str(doc.get("relativePath") or "")
            checkpoint.upsert_item(checkpoint_item_key(doc), title=relative_path, source_id=relative_path)
        docs = select_checkpoint_docs(
            docs,
            checkpoint,
            resume=bool(getattr(args, "resume", False)),
            retry_failed=bool(getattr(args, "retry_failed", False)),
        )
    elif getattr(args, "retry_failed", False):
        if not report_path.exists():
            raise ExportError(f"没有找到上次导入报告，无法重试失败项：{report_path}")
        previous = json.loads(report_path.read_text(encoding="utf-8"))
        failed_paths = {str(item.get("relativePath") or "") for item in previous.get("failures") or [] if item.get("relativePath")}
        if not failed_paths:
            raise ExportError("上次导入报告中没有失败项，不需要重试。")
        docs = [doc for doc in docs if doc["relativePath"] in failed_paths]
        if not docs:
            raise ExportError("上次失败项在当前 Markdown 目录中没有匹配文件。")
    if getattr(args, "doc_path", None):
        wanted = Path(args.doc_path).resolve()
        docs = [doc for doc in docs if Path(doc["path"]).resolve() == wanted]
        if not docs:
            raise ExportError(f"单篇测试文件不在 Markdown 目录内：{wanted}")
    if getattr(args, "max_import", 0) and args.max_import > 0:
        docs = docs[: args.max_import]

    emit(
        args,
        f"开始语雀 Markdown 导入准备：共 {len(docs)} 篇。",
        event="task.started",
        totals={"documents": len(docs)},
        sourceDir=str(source_dir),
        target={"host": host, "namespace": namespace},
    )

    book_data = load_target_book(host, cookies, book_url)
    book = book_data["book"]
    book_id = int(book["id"])
    toc = refresh_catalog(host, cookies, book_id, book_url)
    existing = existing_docs_by_slug(host, cookies, book_id, book_url)
    image_count = sum(1 for doc in docs for res in doc["resources"] if res.get("kind") == "image")
    attachment_count = sum(1 for doc in docs for res in doc["resources"] if res.get("kind") == "attachment")
    missing_resource_count = sum(len(doc.get("resourceWarnings") or []) for doc in docs)
    remote_image_items = [
        {
            "relativePath": doc["relativePath"],
            "target": normalize_link_target(str(link.get("target") or "")),
            "syntax": str(link.get("syntax") or "markdown"),
        }
        for doc in docs
        for link in iter_markdown_links(doc["body"]) + iter_extra_image_links(doc["body"])
        if link.get("marker") == "!" and is_remote_or_anchor(str(link.get("target") or ""))
    ]
    remote_image_count = len(remote_image_items)
    remote_image_converted_count = 0
    remote_image_will_convert_count = 0 if remote_image_policy == "keep" else remote_image_count
    should_rewrite_remote_images = remote_image_count and remote_image_policy != "keep" and not (args.plan or args.dry_run)
    if should_rewrite_remote_images:
        for doc in docs:
            doc["body"], converted = rewrite_remote_images(doc["body"], remote_image_policy)
            remote_image_converted_count += converted
        emit(
            args,
            f"检测到远程图片 {remote_image_count} 个，已按策略 {remote_image_policy} 处理 {remote_image_converted_count} 个，"
            "本地图片仍会上传到语雀。",
        )
    elif remote_image_count and remote_image_policy == "keep":
        emit(args, f"检测到远程图片 {remote_image_count} 个，将保留原始远程图片地址；如果这些地址 403，语雀可能无法创建文档。")
    resource_warning_items = [
        {"relativePath": doc["relativePath"], **warning}
        for doc in docs
        for warning in doc.get("resourceWarnings") or []
    ]
    large_images = [
        {"relativePath": doc["relativePath"], "image": Path(res["path"]).name, "bytes": Path(res["path"]).stat().st_size}
        for doc in docs
        for res in doc["resources"]
        if res.get("kind") == "image" and Path(res["path"]).exists() and Path(res["path"]).stat().st_size >= 3 * 1024 * 1024
    ]
    folders = sorted({"/".join(doc["folders"][:i]) for doc in docs for i in range(1, len(doc["folders"]) + 1)})

    if args.plan or args.dry_run:
        plan_report = {
            "provider": "yuque-import",
            "readOnly": True,
            "host": host,
            "namespace": namespace,
            "book": book,
            "sourceDir": str(source_dir),
            "totalDocs": len(docs),
            "folderCount": len(folders),
            "localImageCount": image_count,
            "localAttachmentCount": attachment_count,
            "missingLocalResourceCount": missing_resource_count,
            "remoteImageCount": remote_image_count,
            "remoteImagePolicy": remote_image_policy,
            "remoteImageConvertedCount": 0,
            "remoteImageWillConvertCount": remote_image_will_convert_count,
            "remoteImageHint": "远程图片不会被万能导上传；默认会在导入时转为普通链接，避免语雀抓取 403 图片导致整篇失败。",
            "largeImageCount": len(large_images),
            "largeImages": large_images,
            "resourceWarnings": resource_warning_items,
            "remoteImages": remote_image_items,
            "reportFile": str(plan_report_path),
            "retryFailures": bool(getattr(args, "retry_failed", False)),
            "uploadConcurrency": UPLOAD_CONCURRENCY,
            "sampleDocs": [{k: doc[k] for k in ("relativePath", "title", "slug", "level", "size")} for doc in docs[:10]],
            "capabilities": ["create_update_markdown", "preserve_directory_tree", "upload_local_images", "upload_local_attachments"],
        }
        write_json_report(plan_report_path, plan_report)
        return plan_report

    if not args.yes:
        raise ExportError("写入语雀需要显式确认，请追加 --yes。")

    created = updated = skipped = 0
    uploaded_images = uploaded_attachments = 0
    failures: list[dict[str, str]] = []
    imported_docs: list[dict[str, Any]] = []
    stopped = False
    processed = 0
    consecutive_failures = 0
    failure_signatures: dict[str, int] = {}

    def build_report(*, aborted: bool = False, abort_reason: str = "") -> dict[str, Any]:
        report: dict[str, Any] = {
            "provider": "yuque-import",
            "host": host,
            "namespace": namespace,
            "book": book,
            "sourceDir": str(source_dir),
            "totalDocs": len(docs),
            "processedDocs": processed,
            "createdDocs": created,
            "updatedDocs": updated,
            "skippedDocs": skipped,
            "failureCount": len(failures),
            "folderCount": len(folders),
            "imageUploads": uploaded_images,
            "attachmentUploads": uploaded_attachments,
            "stopped": stopped,
            "aborted": aborted,
            "abortReason": abort_reason,
            "failures": failures,
            "reportFile": str(report_path),
            "retryFailures": bool(getattr(args, "retry_failed", False)),
            "missingLocalResourceCount": missing_resource_count,
            "remoteImageCount": remote_image_count,
            "remoteImagePolicy": remote_image_policy,
            "remoteImageConvertedCount": remote_image_converted_count,
            "remoteImageWillConvertCount": remote_image_will_convert_count,
            "largeImageCount": len(large_images),
            "largeImages": large_images,
            "resourceWarnings": resource_warning_items,
            "remoteImages": remote_image_items,
            "uploadConcurrency": UPLOAD_CONCURRENCY,
            "importedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        if not aborted:
            report.pop("abortReason", None)
        return report

    def save_partial_report(*, aborted: bool = False, abort_reason: str = "") -> None:
        try:
            write_json_report(report_path, build_report(aborted=aborted, abort_reason=abort_reason))
        except Exception as exc:
            emit(args, f"写入语雀导入报告失败：{compact_error(exc, 240)}")

    for index, doc in enumerate(docs, start=1):
        processed = index
        item_key = checkpoint_item_key(doc)
        if stop_requested(args):
            stopped = True
            if checkpoint:
                checkpoint.fail_item(item_key, "stopped")
            emit(args, "收到停止请求，正在结束。")
            break
        try:
            check_stopped(args)
            if checkpoint:
                checkpoint.start_item(item_key, "import")
            emit(
                args,
                f"开始导入文档：{doc.get('relativePath')}",
                event="document.import.started",
                doc={"title": doc.get("title", ""), "path": doc.get("relativePath", ""), "index": index},
            )
            result, toc = import_one_doc(
                host,
                cookies,
                book_id,
                book_url,
                doc,
                existing,
                toc,
                args.update_existing,
                args.skip_existing,
            )
            imported_docs.append({**doc, **result})
            if checkpoint:
                checkpoint.complete_item(
                    item_key,
                    target_id=str(result.get("id") or ""),
                    metadata={"action": result.get("action") or ""},
                )
            if result["action"] == "created":
                created += 1
            elif result["action"] == "updated":
                updated += 1
            else:
                skipped += 1
            consecutive_failures = 0
            uploaded_images += int(result.get("imageUploads") or 0)
            uploaded_attachments += int(result.get("attachmentUploads") or 0)
            emit(
                args,
                f"文档导入完成：{doc.get('relativePath')}",
                event="document.import.completed",
                doc={"title": doc.get("title", ""), "path": doc.get("relativePath", ""), "index": index},
                result=result,
            )
        except ExportStopped:
            stopped = True
            if checkpoint:
                checkpoint.fail_item(item_key, "stopped")
            emit(args, "收到停止请求，当前文档未完成，正在结束。")
            break
        except Exception as exc:
            error_text = compact_error(exc, 1200)
            if checkpoint:
                checkpoint.fail_item(item_key, error_text)
            fatal_message = fatal_yuque_error_message(error_text)
            failure: dict[str, str] = {"relativePath": doc["relativePath"], "title": doc.get("title", ""), "error": error_text}
            if fatal_message:
                failure["category"] = "语雀文档数量限制"
                failure["suggestion"] = fatal_message
            failures.append(failure)
            consecutive_failures += 1
            signature = failure_signature(error_text)
            failure_signatures[signature] = failure_signatures.get(signature, 0) + 1
            if len(failures) <= FAILURE_LOG_LIMIT:
                visible_error = fatal_message or error_text
                emit(
                    args,
                    f"失败 {len(failures)}：{doc['relativePath']}：{compact_error(visible_error, 360)}",
                    event="document.import.failed",
                    level="error",
                    doc={"title": doc.get("title", ""), "path": doc.get("relativePath", ""), "index": index},
                    error={"message": error_text, "category": failure.get("category", "")},
                    suggestion=failure.get("suggestion", ""),
                )
            elif len(failures) == FAILURE_LOG_LIMIT + 1:
                emit(args, f"已连续出现较多失败，后续失败原因会继续写入报告：{report_path}")

            if fatal_message:
                save_partial_report(aborted=True, abort_reason=fatal_message)
                raise ExportError(f"{fatal_message}第一条失败：{doc['relativePath']}。已生成部分报告：{report_path}") from exc

            abort_after = min(COMMON_FAILURE_ABORT_AFTER, max(1, len(docs)))
            common_failures = max(failure_signatures.values() or [0])
            if (
                created + updated + skipped == 0
                and consecutive_failures >= abort_after
                and common_failures >= max(3, abort_after // 2)
            ):
                first_failure = failures[0]
                abort_reason = (
                    f"前 {consecutive_failures} 篇文档连续创建/更新失败，疑似目标语雀知识库没有写入权限、"
                    f"登录态不可写、目标 URL 不是可编辑知识库、远程图片 403 导致语雀抓取失败，或语雀接口拒绝创建。"
                    f"第一条失败：{first_failure.get('relativePath')}：{first_failure.get('error')}"
                )
                save_partial_report(aborted=True, abort_reason=abort_reason)
                raise ExportError(f"{abort_reason}。已生成部分报告：{report_path}")
        emit(
            args,
            f"progress {index}/{len(docs)} created={created} updated={updated} skipped={skipped} "
            f"images={uploaded_images} attachments={uploaded_attachments} failures={len(failures)}",
            event="task.progress",
            progress={"current": index, "total": len(docs)},
            stats={
                "createdDocs": created,
                "updatedDocs": updated,
                "skippedDocs": skipped,
                "imageSuccess": uploaded_images,
                "attachmentUploads": uploaded_attachments,
                "failureCount": len(failures),
            },
        )
        if failures or index % 10 == 0:
            save_partial_report()

    report = build_report()
    write_json_report(report_path, report)
    if checkpoint:
        if stopped:
            checkpoint.fail_task("stopped", status="stopped")
        else:
            checkpoint.complete_task(report)
        report["checkpoint"] = checkpoint.stats()
        write_json_report(report_path, report)
        checkpoint.close()
    emit(
        args,
        "语雀 Markdown 导入完成" if not stopped else "语雀 Markdown 导入已停止",
        event="task.completed" if not stopped else "task.stopped",
        level="success" if not stopped else "warn",
        reportFile=str(report_path),
        stats={
            "createdDocs": created,
            "updatedDocs": updated,
            "skippedDocs": skipped,
            "failureCount": len(failures),
            "imageSuccess": uploaded_images,
            "attachmentUploads": uploaded_attachments,
        },
    )
    return report


def run_gui() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext
    from gui_utils import create_scrollable_body

    root = tk.Tk()
    root.title("语雀 Markdown 导入工具")
    root.geometry("980x760")
    body = create_scrollable_body(root)

    config_file = default_config_path()
    config = load_config(config_file)
    url_var = tk.StringVar(value=str(config.get("targetBookUrl") or ""))
    source_var = tk.StringVar(value=str(config.get("sourceDir") or DEFAULT_SOURCE_DIR.resolve()))
    auth_var = tk.StringVar(value=str(config.get("authFile") or default_auth_path().resolve()))
    profile_var = tk.StringVar(value=str(config.get("profileDir") or default_profile_path().resolve()))
    browser_path_var = tk.StringVar(value=str(config.get("browserPath") or ""))
    update_var = tk.BooleanVar(value=True)
    log_queue: queue.Queue[str] = queue.Queue()
    current_stop_event: dict[str, threading.Event | None] = {"event": None}

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
        root.after(120, poll_log)

    def browse_source() -> None:
        directory = filedialog.askdirectory(initialdir=source_var.get() or str(PROJECT_DIR))
        if directory:
            source_var.set(directory)

    def browse_auth() -> None:
        selected = filedialog.asksaveasfilename(initialfile=".yuque_auth.json", initialdir=str(PROJECT_DIR))
        if selected:
            auth_var.set(selected)

    def browse_profile() -> None:
        selected = filedialog.askdirectory(initialdir=profile_var.get() or str(PROJECT_DIR))
        if selected:
            profile_var.set(selected)

    def open_source() -> None:
        path = Path(source_var.get())
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])

    def build_args(*, plan: bool, single: bool = False, login: bool = False) -> argparse.Namespace:
        if not url_var.get().strip():
            raise ExportError("请填写目标语雀知识库 URL")
        if not source_var.get().strip() and not login:
            raise ExportError("请填写 Markdown 目录")
        return argparse.Namespace(
            target_book_url=url_var.get().strip(),
            book_url=url_var.get().strip(),
            source_dir=source_var.get().strip(),
            config_file=str(config_file),
            auth_file=auth_var.get().strip() or str(default_auth_path()),
            profile_dir=profile_var.get().strip() or str(default_profile_path()),
            browser_path=browser_path_var.get().strip() or None,
            port=DEFAULT_PORT,
            login_wait_seconds=0.0,
            close_started_chrome=False,
            skip_auth_load=False,
            doc_path=None,
            max_import=1 if single else 0,
            plan=plan,
            dry_run=False,
            yes=not plan,
            update_existing=update_var.get(),
            skip_existing=not update_var.get(),
            stop_event=None,
            log_callback=log,
        )

    def wait_for_login_dialog() -> None:
        event = threading.Event()

        def ask() -> None:
            messagebox.showinfo(
                "完成登录后继续",
                "浏览器已经打开。\n\n请在浏览器里完成语雀登录，并确认目标知识库页面能正常打开。\n完成后回到这里点击“确定”，工具会保存登录凭证。",
            )
            event.set()

        root.after(0, ask)
        event.wait()

    def save_current_config() -> None:
        save_config(
            config_file,
            {
                "targetBookUrl": url_var.get().strip(),
                "sourceDir": source_var.get().strip(),
                "authFile": auth_var.get().strip(),
                "profileDir": profile_var.get().strip(),
                "browserPath": browser_path_var.get().strip(),
            },
        )
        log(f"已保存语雀导入配置：{config_file}")

    def stop_current() -> None:
        event = current_stop_event.get("event")
        if event:
            event.set()
            log("已发送停止请求。")

    def run_worker(name: str, args: argparse.Namespace, fn: Callable[[], dict[str, Any]]) -> None:
        def worker() -> None:
            event = threading.Event()
            args.stop_event = event
            current_stop_event["event"] = event
            log(f"开始：{name}")
            try:
                save_current_config()
                result = fn()
                log(f"{'已停止' if result.get('stopped') else '完成'}：{name}")
                summary = {
                    key: result.get(key)
                    for key in (
                        "totalDocs",
                        "folderCount",
                        "createdDocs",
                        "updatedDocs",
                        "skippedDocs",
                        "failureCount",
                        "localImageCount",
                        "localAttachmentCount",
                        "imageUploads",
                        "attachmentUploads",
                        "stopped",
                    )
                    if key in result
                }
                log(json.dumps(summary, ensure_ascii=False, indent=2))
            except ExportStopped as exc:
                log(f"已停止：{exc}")
            except Exception as exc:
                root.after(0, lambda text=str(exc): messagebox.showerror("执行失败", text))
                log(f"失败：{exc}")
            finally:
                current_stop_event["event"] = None

        threading.Thread(target=worker, daemon=True).start()

    form = tk.Frame(body, padx=14, pady=12)
    form.pack(fill="x")
    form.columnconfigure(1, weight=1)

    tk.Label(form, text="目标语雀知识库 URL").grid(row=0, column=0, sticky="w", pady=5)
    tk.Entry(form, textvariable=url_var).grid(row=0, column=1, sticky="ew", padx=8, pady=5)
    tk.Label(form, text="Markdown 目录").grid(row=1, column=0, sticky="w", pady=5)
    tk.Entry(form, textvariable=source_var).grid(row=1, column=1, sticky="ew", padx=8, pady=5)
    tk.Button(form, text="选择", command=browse_source).grid(row=1, column=2, pady=5)
    tk.Label(form, text="凭证文件").grid(row=2, column=0, sticky="w", pady=5)
    tk.Entry(form, textvariable=auth_var).grid(row=2, column=1, sticky="ew", padx=8, pady=5)
    tk.Button(form, text="选择", command=browse_auth).grid(row=2, column=2, pady=5)
    tk.Label(form, text="浏览器配置目录").grid(row=3, column=0, sticky="w", pady=5)
    tk.Entry(form, textvariable=profile_var).grid(row=3, column=1, sticky="ew", padx=8, pady=5)
    tk.Button(form, text="选择", command=browse_profile).grid(row=3, column=2, pady=5)
    tk.Checkbutton(form, text="已存在同路径文档则更新", variable=update_var).grid(row=4, column=1, sticky="w", pady=5)

    actions = tk.Frame(body, padx=14, pady=4)
    actions.pack(fill="x")
    tk.Button(
        actions,
        text="登录并保存凭证",
        command=lambda: run_worker("登录并保存凭证", build_args(plan=True, login=True), lambda: login_and_save_auth(build_args(plan=True, login=True), wait_for_login_dialog)),
        width=18,
    ).pack(side="left", padx=5)
    tk.Button(actions, text="保存配置", command=save_current_config, width=12).pack(side="left", padx=5)
    tk.Button(actions, text="生成计划", command=lambda: run_worker("生成计划", build_args(plan=True), lambda: import_docs(build_args(plan=True))), width=12).pack(side="left", padx=5)
    tk.Button(actions, text="单篇导入测试", command=lambda: run_worker("单篇导入测试", build_args(plan=False, single=True), lambda: import_docs(build_args(plan=False, single=True))), width=16).pack(side="left", padx=5)
    tk.Button(actions, text="批量导入", command=lambda: run_worker("批量导入", build_args(plan=False), lambda: import_docs(build_args(plan=False))), width=12).pack(side="left", padx=5)
    tk.Button(actions, text="停止", command=stop_current, width=10).pack(side="left", padx=5)
    tk.Button(actions, text="打开目录", command=open_source, width=12).pack(side="left", padx=5)

    note = tk.Label(
        body,
        text="说明：语雀导入会按本地文件夹创建语雀目录，并上传本地图片和附件。凭证文件保存的是登录 Cookie，不保存密码。",
        anchor="w",
        wraplength=900,
        padx=14,
        pady=8,
    )
    note.pack(fill="x")

    log_text = scrolledtext.ScrolledText(body, height=18, state="disabled")
    log_text.pack(fill="both", expand=True, padx=14, pady=12)
    poll_log()
    root.mainloop()
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import local Markdown documents into Yuque.")
    parser.add_argument("--gui", action="store_true", help="Open graphical interface")
    parser.add_argument("--login", action="store_true", help="Open browser, let you log in, then save Yuque cookies")
    parser.add_argument("--target-book-url", "--book-url", dest="target_book_url", help="Target Yuque book URL")
    parser.add_argument("--source-dir", help="Local Markdown directory")
    parser.add_argument("--config-file", default=str(default_config_path()), help="Local config JSON path")
    parser.add_argument("--auth-file", help=f"Auth cookie file. Omit to auto-use {default_auth_path()}")
    parser.add_argument("--profile-dir", help=f"Chrome profile dir. Omit to auto-use {default_profile_path()}")
    parser.add_argument("--browser-path", help="Optional Chrome/Edge/Chromium executable path")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Chrome remote debugging port")
    parser.add_argument("--login-wait-seconds", type=float, default=0.0, help="Wait this many seconds before saving login cookies")
    parser.add_argument("--close-started-chrome", action="store_true", help="Close Chrome started by this script after login")
    parser.add_argument("--save-config", action="store_true", help="Save target URL and source dir locally")
    parser.add_argument("--plan", action="store_true", help="Only scan local Markdown and verify target book")
    parser.add_argument("--dry-run", action="store_true", help="Same as --plan")
    parser.add_argument("--api-import-one", action="store_true", help="Import one Markdown file")
    parser.add_argument("--api-import-all", action="store_true", help="Import all Markdown files")
    parser.add_argument("--doc-path", help="Markdown file used by --api-import-one")
    parser.add_argument("--max-import", type=int, default=0, help="Limit import count")
    parser.add_argument("--yes", action="store_true", help="Confirm write operations")
    parser.add_argument("--update-existing", action="store_true", default=True, help="Update documents with the same generated slug")
    parser.add_argument("--skip-existing", action="store_true", help="Skip existing documents")
    add_checkpoint_args(parser, retry_flag="--retry-failures")
    parser.add_argument("--request-timeout", type=int, default=DEFAULT_REQUEST_TIMEOUT, help="Yuque API request timeout seconds")
    parser.add_argument("--upload-timeout", type=int, default=DEFAULT_UPLOAD_TIMEOUT, help="Yuque resource upload timeout seconds")
    parser.add_argument("--retry-attempts", type=int, default=DEFAULT_RETRY_ATTEMPTS, help="Retry attempts for timeout/429/5xx requests")
    parser.add_argument("--retry-delay", type=float, default=DEFAULT_RETRY_DELAY, help="Base retry delay seconds")
    parser.add_argument("--upload-concurrency", type=int, default=DEFAULT_UPLOAD_CONCURRENCY, help="Concurrent resource uploads per document, 1-4")
    parser.add_argument(
        "--remote-image-policy",
        choices=sorted(REMOTE_IMAGE_POLICIES),
        default="link",
        help="How to handle remote image URLs before importing: link keeps the document import stable, keep leaves image syntax unchanged, remove drops them.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    if not argv or "--gui" in argv:
        print("旧版 Python GUI 已废弃，请使用 Electron 桌面端：start-wandao.cmd 或 ./start-wandao.sh", file=sys.stderr)
        return 2
    args = parse_args(argv)
    try:
        if args.save_config:
            save_config(Path(args.config_file), config_args(args))
            print(json.dumps({"configFile": str(Path(args.config_file).resolve())}, ensure_ascii=False, indent=2))
            return 0
        if args.login:
            if not args.target_book_url:
                raise ExportError("--target-book-url is required for --login")
            args.book_url = normalize_book_url(args.target_book_url)
            report = login_and_save_auth(args)
        else:
            if not (args.plan or args.dry_run or args.api_import_one or args.api_import_all):
                args.plan = True
            if args.api_import_one and not args.doc_path and not args.max_import:
                args.max_import = 1
            report = import_docs(args)
    except (KeyboardInterrupt, ExportStopped):
        emit(args, "语雀导入任务已停止。", event="task.stopped", level="warn")
        return 130
    except Exception as exc:
        emit(
            args,
            f"语雀导入任务失败：{compact_error(exc, 500)}",
            event="task.failed",
            level="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1
    keys = (
        "provider",
        "readOnly",
        "host",
        "namespace",
        "totalDocs",
        "folderCount",
        "createdDocs",
        "updatedDocs",
        "skippedDocs",
        "failureCount",
        "localImageCount",
        "localAttachmentCount",
        "missingLocalResourceCount",
        "remoteImageCount",
        "remoteImagePolicy",
        "remoteImageConvertedCount",
        "remoteImageWillConvertCount",
        "largeImageCount",
        "imageUploads",
        "attachmentUploads",
        "reportFile",
        "retryFailures",
        "uploadConcurrency",
        "stopped",
        "failures",
        "cookieCount",
        "authFile",
    )
    print(json.dumps({key: report[key] for key in keys if key in report}, ensure_ascii=False, indent=2))
    return 130 if report.get("stopped") else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
