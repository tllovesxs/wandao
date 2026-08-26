#!/usr/bin/env python3
# Author: tllovesxs
"""
ima knowledge-base importer/exporter for Wandao.

This module uses ima OpenAPI to list knowledge bases, export knowledge-base
files to local folders, and import local files into a target knowledge base.
It intentionally focuses on the knowledge-base model instead of the separate
note model because knowledge bases expose folder browsing and original-file
download APIs.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import html
import json
import mimetypes
import os
import random
import re
import shutil
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wandao_core.checkpoint import add_checkpoint_args, open_checkpoint_from_args
from wandao_cli import extend_arg_list_from_file
from wandao_core.logging import emit_legacy
from wandao_core.report import finalize_report
from wandao_core.credentials import write_private_json
from wandao_core.source_paths import iter_regular_files_under_root, resolve_local_reference


PROJECT_DIR = Path(__file__).resolve().parent
BASE_URL = "https://ima.qq.com"
WIKI_PATH = "openapi/wiki/v1"
NOTE_PATH = "openapi/note/v1"
DOC_ID_SEPARATOR = "|||"
FORBIDDEN_FILENAME_CHARS = r'<>:"/\|?*'


class ImaError(RuntimeError):
    pass


class ImaStopped(ImaError):
    pass


MEDIA_TYPES: dict[str, tuple[int, str]] = {
    ".pdf": (1, "application/pdf"),
    ".doc": (3, "application/msword"),
    ".docx": (3, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".ppt": (4, "application/vnd.ms-powerpoint"),
    ".pptx": (4, "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ".xls": (5, "application/vnd.ms-excel"),
    ".xlsx": (5, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".csv": (5, "text/csv"),
    ".md": (7, "text/markdown"),
    ".markdown": (7, "text/markdown"),
    ".png": (9, "image/png"),
    ".jpg": (9, "image/jpeg"),
    ".jpeg": (9, "image/jpeg"),
    ".webp": (9, "image/webp"),
    ".txt": (13, "text/plain"),
    ".xmind": (14, "application/x-xmind"),
    ".mp3": (15, "audio/mpeg"),
    ".m4a": (15, "audio/x-m4a"),
    ".wav": (15, "audio/wav"),
    ".aac": (15, "audio/aac"),
}

MEDIA_EXTENSIONS = {
    1: ".pdf",
    3: ".docx",
    4: ".pptx",
    5: ".xlsx",
    7: ".md",
    9: ".png",
    11: ".md",
    13: ".txt",
    14: ".xmind",
    15: ".mp3",
}

CONTENT_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "text/csv": ".csv",
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    "application/md": ".md",
    "application/markdown": ".md",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "text/plain": ".txt",
    "application/x-xmind": ".xmind",
    "audio/mpeg": ".mp3",
    "audio/x-m4a": ".m4a",
    "audio/wav": ".wav",
    "audio/aac": ".aac",
}

SKIP_SOURCE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "runtime",
    "exports",
    "wandao_electron",
    ".tmp-runtime-test",
}

MARKDOWN_REFERENCE_RE = re.compile(
    r"!\[[^\]]*\]\(([^)]+)\)|\[[^\]]+\]\(([^)]+)\)|<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
MARKDOWN_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\(\s*(?:<(?P<bracket>https?://[^>\s]+)>|(?P<plain>https?://[^)\s]+))",
    re.IGNORECASE,
)
HTML_IMAGE_RE = re.compile(r"<img\b[^>]*\bsrc\s*=\s*[\"'](?P<src>[^\"']+)[\"']", re.IGNORECASE)
REMOTE_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)
DATA_IMAGE_RE = re.compile(r"data:image/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]+", re.IGNORECASE)
IMAGE_URL_SUFFIXES = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
READ_RETRY_ACTIONS = {
    "check_repeated_names",
    "get_addable_knowledge_base_list",
    "get_doc_content",
    "get_knowledge_list",
    "get_media_info",
    "search_knowledge_base",
}
BARE_IMAGE_LINE_RE = re.compile(
    r"(?m)^(?P<indent>\s*)(?P<url>https?://[^\s<>\"')]+)(?P<trailing>\s*)$",
    re.IGNORECASE,
)
MARKDOWN_IMAGE_REFERENCE_RE = re.compile(
    r"!\[[^\]]*\]\(\s*(?P<value><[^>\n]+>|[^)\n]+)\)",
    re.IGNORECASE,
)
HTML_IMAGE_REFERENCE_RE = re.compile(r"<img\b[^>]*\bsrc\s*=\s*[\"'](?P<src>[^\"']+)[\"']", re.IGNORECASE)


@dataclass
class KnowledgeBase:
    id: str
    name: str


@dataclass
class KnowledgeEntry:
    kb_id: str
    kb_name: str
    media_id: str
    title: str
    parent_node_id: str
    relative_parts: list[str]
    is_folder: bool
    media_type: int | None = None

    @property
    def export_id(self) -> str:
        return f"{self.kb_id}{DOC_ID_SEPARATOR}{self.media_id}"


def emit(message: str, *, event: str = "log.message", level: str = "info", **fields: Any) -> None:
    emit_legacy("ima", message, event=event, level=level, **fields)


def emit_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)


def default_config_file() -> Path:
    data_dir = os.environ.get("WANDAO_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "ima_config.json"
    return PROJECT_DIR / ".ima_config.json"


def default_output_dir() -> Path:
    data_dir = os.environ.get("WANDAO_DATA_DIR")
    root = Path(data_dir) if data_dir else PROJECT_DIR
    return root / "exports" / "ima"


def sanitize_filename(name: str, fallback: str = "untitled") -> str:
    text = re.sub(f"[{re.escape(FORBIDDEN_FILENAME_CHARS)}]", "_", str(name or "").strip())
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:180] or fallback


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 2
    while True:
        candidate = parent / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception as exc:
        raise ImaError(f"读取配置失败：{path}：{exc}") from exc


def save_json(path: Path, data: dict[str, Any]) -> None:
    write_private_json(path, data)


def require_credentials(args: argparse.Namespace) -> tuple[str, str]:
    config_file = Path(args.config_file) if args.config_file else default_config_file()
    config = load_json(config_file)
    client_id = args.client_id or os.environ.get("IMA_CLIENT_ID") or config.get("client_id") or ""
    api_key = args.api_key or os.environ.get("IMA_API_KEY") or config.get("api_key") or ""
    if not client_id:
        client_id = input("ima Client ID: ").strip()
    if not api_key:
        api_key = getpass.getpass("ima API Key: ").strip()
    if not client_id or not api_key:
        raise ImaError("请填写 ima Client ID 和 API Key")
    return client_id, api_key


def maybe_save_config(args: argparse.Namespace) -> dict[str, Any]:
    client_id, api_key = require_credentials(args)
    config_file = Path(args.config_file) if args.config_file else default_config_file()
    config = load_json(config_file)
    config.update({"client_id": client_id, "api_key": api_key})
    if args.knowledge_base_id:
        config["knowledge_base_id"] = args.knowledge_base_id
    if args.folder_id:
        config["folder_id"] = args.folder_id
    save_json(config_file, config)
    return {"provider": "ima", "configFile": str(config_file), "saved": True}


def stop_requested(args: argparse.Namespace | None) -> bool:
    event = getattr(args, "stop_event", None) if args is not None else None
    return bool(event and event.is_set())


def check_stopped(args: argparse.Namespace | None) -> None:
    if stop_requested(args):
        raise ImaStopped("用户已停止当前任务")


def wait_with_stop(args: argparse.Namespace | None, seconds: float) -> None:
    deadline = time.time() + max(0.0, seconds)
    while time.time() < deadline:
        check_stopped(args)
        time.sleep(min(0.2, deadline - time.time()))


def throttle(args: argparse.Namespace | None) -> None:
    if not args:
        return
    delay = max(0.0, float(getattr(args, "request_delay", 0.2) or 0))
    jitter = max(0.0, float(getattr(args, "request_jitter", 0.2) or 0))
    pause = delay + (random.uniform(0, jitter) if jitter else 0)
    if pause:
        wait_with_stop(args, pause)
    args._request_count = int(getattr(args, "_request_count", 0) or 0) + 1


def is_timeout_exception(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        return isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).lower()
    return "timed out" in str(exc).lower()


class ImaClient:
    def __init__(self, client_id: str, api_key: str, args: argparse.Namespace | None = None) -> None:
        self.client_id = client_id
        self.api_key = api_key
        self.args = args

    def post(self, base_path: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{BASE_URL}/{base_path}/{action}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        attempts = 3 if action in READ_RETRY_ACTIONS else 1
        raw = ""
        for attempt in range(attempts):
            check_stopped(self.args)
            if attempt:
                wait_with_stop(self.args, min(1.5 * attempt, 4.0))
            throttle(self.args)
            request = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "ima-openapi-clientid": self.client_id,
                    "ima-openapi-apikey": self.api_key,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise ImaError(f"ima API HTTP {exc.code}：{detail}") from exc
            except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
                if is_timeout_exception(exc) and attempt + 1 < attempts:
                    emit(
                        f"ima API {action} 读取超时，正在重试（{attempt + 1}/{attempts - 1}）",
                        event="request.retry",
                        level="warn",
                        action=action,
                    )
                    continue
                if is_timeout_exception(exc):
                    raise ImaError(f"ima API {action} 读取超时：{exc}") from exc
                raise ImaError(f"ima API 请求失败：{exc}") from exc
        if not raw:
            raise ImaError(f"ima API {action} 未返回内容")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ImaError(f"ima API 返回非 JSON：{raw[:300]}") from exc
        if data.get("code") not in (0, "0", None):
            raise ImaError(f"ima API {action} 失败：{data.get('msg') or data}")
        return data.get("data") or {}

    def wiki(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post(WIKI_PATH, action, payload)

    def note(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post(NOTE_PATH, action, payload)


def response_list(data: dict[str, Any], *names: str) -> list[dict[str, Any]]:
    for name in names:
        value = data.get(name)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    for value in data.values():
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            return value
    return []


def get_knowledge_bases(client: ImaClient, *, addable_only: bool = False) -> list[KnowledgeBase]:
    result: list[KnowledgeBase] = []
    seen: set[str] = set()
    cursor = ""
    endpoint = "get_addable_knowledge_base_list" if addable_only else "search_knowledge_base"
    while True:
        payload = {"cursor": cursor, "limit": 50 if addable_only else 20}
        if not addable_only:
            payload["query"] = ""
        data = client.wiki(endpoint, payload)
        rows = response_list(data, "knowledge_base_list", "searched_knowledge_base_list", "addable_knowledge_base_list")
        for item in rows:
            kb_id = str(item.get("id") or item.get("knowledge_base_id") or "").strip()
            if not kb_id or kb_id in seen:
                continue
            seen.add(kb_id)
            result.append(KnowledgeBase(id=kb_id, name=str(item.get("name") or "未命名知识库")))
        if data.get("is_end", True):
            break
        cursor = str(data.get("next_cursor") or "")
        if not cursor:
            break
    if not result and not addable_only:
        return get_knowledge_bases(client, addable_only=True)
    return result


def is_folder_item(item: dict[str, Any]) -> bool:
    media_id = str(item.get("media_id") or item.get("folder_id") or "")
    if media_id.startswith("folder_"):
        return True
    return any(key in item for key in ("folder_number", "file_number", "is_top")) and not item.get("url")


def item_id(item: dict[str, Any]) -> str:
    return str(item.get("media_id") or item.get("folder_id") or item.get("id") or "").strip()


def item_title(item: dict[str, Any]) -> str:
    return str(item.get("title") or item.get("name") or item.get("file_name") or "未命名").strip()


def list_folder(
    client: ImaClient,
    kb: KnowledgeBase,
    *,
    folder_id: str = "",
    parent_node_id: str = "",
    parts: list[str] | None = None,
) -> list[KnowledgeEntry]:
    entries: list[KnowledgeEntry] = []
    cursor = ""
    parts = list(parts or [])
    while True:
        payload: dict[str, Any] = {"knowledge_base_id": kb.id, "cursor": cursor, "limit": 50}
        if folder_id:
            payload["folder_id"] = folder_id
        data = client.wiki("get_knowledge_list", payload)
        rows = response_list(data, "knowledge_list", "info_list", "list")
        for item in rows:
            media_id = item_id(item)
            if not media_id:
                continue
            title = item_title(item)
            if is_folder_item(item):
                node_id = f"ima-folder:{kb.id}:{media_id}"
                folder_parts = [*parts, sanitize_filename(title, "folder")]
                entry = KnowledgeEntry(
                    kb_id=kb.id,
                    kb_name=kb.name,
                    media_id=media_id,
                    title=title,
                    parent_node_id=parent_node_id,
                    relative_parts=folder_parts,
                    is_folder=True,
                )
                entries.append(entry)
                entries.extend(
                    list_folder(
                        client,
                        kb,
                        folder_id=media_id,
                        parent_node_id=node_id,
                        parts=folder_parts,
                    )
                )
            else:
                entries.append(
                    KnowledgeEntry(
                        kb_id=kb.id,
                        kb_name=kb.name,
                        media_id=media_id,
                        title=title,
                        parent_node_id=parent_node_id,
                        relative_parts=parts,
                        is_folder=False,
                        media_type=int(item.get("media_type")) if str(item.get("media_type", "")).isdigit() else None,
                    )
                )
        if data.get("is_end", True):
            break
        cursor = str(data.get("next_cursor") or "")
        if not cursor:
            break
    return entries


def scan_remote_tree(client: ImaClient, args: argparse.Namespace, *, addable_only: bool = False) -> tuple[list[KnowledgeBase], list[KnowledgeEntry]]:
    if args.knowledge_base_id:
        name = args.knowledge_base_name or args.knowledge_base_id
        kbs = [KnowledgeBase(id=args.knowledge_base_id, name=name)]
    else:
        kbs = get_knowledge_bases(client, addable_only=addable_only)
    entries: list[KnowledgeEntry] = []
    for kb in kbs:
        entries.extend(list_folder(client, kb, parent_node_id=f"ima-kb:{kb.id}"))
    return kbs, entries


def scan_toc(client: ImaClient, args: argparse.Namespace) -> dict[str, Any]:
    kbs, entries = scan_remote_tree(client, args)
    nodes: list[dict[str, Any]] = []
    for kb in kbs:
        nodes.append(
            {
                "nodeType": "knowledge_base",
                "nodeId": f"ima-kb:{kb.id}",
                "exportId": "",
                "title": kb.name,
                "parentNodeId": "",
                "selectable": False,
                "knowledgeBaseId": kb.id,
            }
        )
    for entry in entries:
        if entry.is_folder:
            nodes.append(
                {
                    "nodeType": "folder",
                    "nodeId": f"ima-folder:{entry.kb_id}:{entry.media_id}",
                    "exportId": "",
                    "title": entry.title,
                    "parentNodeId": entry.parent_node_id,
                    "selectable": False,
                    "knowledgeBaseId": entry.kb_id,
                    "folderId": entry.media_id,
                }
            )
        else:
            nodes.append(
                {
                    "nodeType": "media",
                    "nodeId": f"ima-media:{entry.kb_id}:{entry.media_id}",
                    "exportId": entry.export_id,
                    "title": entry.title,
                    "parentNodeId": entry.parent_node_id,
                    "selectable": True,
                    "knowledgeBaseId": entry.kb_id,
                    "mediaId": entry.media_id,
                    "mediaType": entry.media_type,
                }
            )
    return {
        "provider": "ima",
        "knowledgeBases": [kb.__dict__ for kb in kbs],
        "nodes": nodes,
        "totalDocs": sum(1 for entry in entries if not entry.is_folder),
    }


def extract_content_text(data: dict[str, Any]) -> str:
    for key in ("content", "doc_content", "text", "markdown", "plain_text"):
        value = data.get(key)
        if isinstance(value, str):
            return value
    for value in data.values():
        if isinstance(value, str) and len(value) > 20:
            return value
        if isinstance(value, dict):
            nested = extract_content_text(value)
            if nested:
                return nested
    return ""


def extension_for_download(title: str, media_type: int | None, content_type: str) -> str:
    suffix = Path(title).suffix
    if suffix:
        return suffix
    normalized = content_type.split(";", 1)[0].strip().lower()
    if normalized in CONTENT_EXTENSIONS:
        return CONTENT_EXTENSIONS[normalized]
    if media_type in MEDIA_EXTENSIONS:
        return MEDIA_EXTENSIONS[media_type]
    guessed = mimetypes.guess_extension(normalized) if normalized else None
    return guessed or ".bin"


def download_url(url: str, headers: dict[str, str] | None = None) -> tuple[bytes, str]:
    for attempt in range(3):
        request = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read(), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ImaError(f"下载原文 HTTP {exc.code}：{detail}") from exc
        except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
            if is_timeout_exception(exc) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            if is_timeout_exception(exc):
                raise ImaError(f"下载原文读取超时：{exc}") from exc
            raise ImaError(f"下载原文失败：{exc}") from exc
    raise ImaError("下载原文失败：重试次数已用尽")


def _clean_remote_url(value: str) -> str:
    return html.unescape(str(value or "").strip()).strip("<>").rstrip(".,;，。；")


def _is_remote_image_url(url: str, *, allow_unknown_suffix: bool = False) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return False
    suffix = Path(urllib.parse.unquote(parsed.path)).suffix.lower()
    return allow_unknown_suffix or suffix in IMAGE_URL_SUFFIXES


def remote_image_urls(markdown: str) -> list[str]:
    """Return remote image URLs without attempting to fetch arbitrary links."""

    result: list[str] = []
    seen: set[str] = set()

    def add(raw: str, *, allow_unknown_suffix: bool = False) -> None:
        url = _clean_remote_url(raw)
        if url and url not in seen and _is_remote_image_url(url, allow_unknown_suffix=allow_unknown_suffix):
            seen.add(url)
            result.append(url)

    for match in MARKDOWN_IMAGE_RE.finditer(markdown):
        add(match.group("bracket") or match.group("plain"), allow_unknown_suffix=True)
    for match in HTML_IMAGE_RE.finditer(markdown):
        add(match.group("src"), allow_unknown_suffix=True)
    for match in REMOTE_URL_RE.finditer(markdown):
        add(match.group(0))
    return result


def data_image_urls(markdown: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for match in DATA_IMAGE_RE.finditer(markdown):
        value = match.group(0)
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _resource_failure(resource_failures: list[dict[str, str]], url: str, exc: Exception) -> None:
    host = urllib.parse.urlparse(url).netloc or "unknown-host"
    resource_failures.append({"type": "image", "host": host, "error": str(exc)})


def localize_markdown_images(
    markdown: str,
    assets_dir: Path,
    resource_failures: list[dict[str, str]] | None = None,
) -> tuple[str, int]:
    """Download remote Markdown images and rewrite them to sibling local assets."""

    failures = resource_failures if resource_failures is not None else []
    urls = [*remote_image_urls(markdown), *data_image_urls(markdown)]
    if not urls:
        return markdown, 0

    replacements: dict[str, str] = {}
    downloaded = 0
    for index, url in enumerate(urls, 1):
        try:
            if url.startswith("data:"):
                header, encoded = url.split(",", 1)
                content = base64.b64decode(encoded, validate=True)
                content_type = header[5:].split(";", 1)[0]
            else:
                content, content_type = download_url(url)
            if not content:
                raise ImaError("图片响应为空")
            source_name = "" if url.startswith("data:") else Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).name
            suffix = extension_for_download(source_name, None, content_type)
            assets_dir.mkdir(parents=True, exist_ok=True)
            target = unique_path(assets_dir / f"image-{index:03d}{suffix}")
            target.write_bytes(content)
            replacements[url] = f"{assets_dir.name}/{target.name}".replace("\\", "/")
            downloaded += 1
        except Exception as exc:
            _resource_failure(failures, url, exc)

    localized = markdown
    for remote_url, local_url in replacements.items():
        localized = localized.replace(remote_url, local_url)

    def replace_bare_image_line(match: re.Match[str]) -> str:
        url = _clean_remote_url(match.group("url"))
        local_url = replacements.get(url)
        if not local_url:
            return match.group(0)
        return f"{match.group('indent')}![]({local_url}){match.group('trailing')}"

    localized = BARE_IMAGE_LINE_RE.sub(replace_bare_image_line, localized)
    return localized, downloaded


def _local_image_references(markdown: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for match in MARKDOWN_IMAGE_REFERENCE_RE.finditer(markdown):
        value = match.group("value").strip()
        if value.startswith("<") and value.endswith(">"):
            value = value[1:-1].strip()
        else:
            value = re.sub(r"\s+(?:\"[^\"]*\"|'[^']*')\s*$", "", value).strip()
        value = _clean_remote_url(value)
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    for match in HTML_IMAGE_REFERENCE_RE.finditer(markdown):
        value = _clean_remote_url(match.group("src"))
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def embed_local_markdown_images(
    markdown: str,
    source_path: Path,
    source_root: Path,
    resource_failures: list[dict[str, str]] | None = None,
) -> tuple[str, int]:
    """Embed local Markdown images so an IMA note remains self-contained."""

    failures = resource_failures if resource_failures is not None else []
    replacements: dict[str, str] = {}
    embedded = 0
    source_path = source_path.resolve()
    source_root = source_root.resolve()
    for raw_reference in _local_image_references(markdown):
        parsed = urllib.parse.urlparse(raw_reference)
        if parsed.scheme or parsed.netloc or raw_reference.startswith(("/", "\\", "data:")):
            continue
        reference = urllib.parse.unquote(raw_reference.split("#", 1)[0].split("?", 1)[0])
        candidate = (source_path.parent / reference).resolve()
        try:
            candidate.relative_to(source_root)
        except ValueError:
            failures.append({"type": "image", "reference": raw_reference, "error": "引用路径超出导入目录"})
            continue
        if not candidate.is_file() or candidate.suffix.lower() not in IMAGE_URL_SUFFIXES:
            failures.append({"type": "image", "reference": raw_reference, "error": "本地图片不存在或格式不支持"})
            continue
        try:
            content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            encoded = base64.b64encode(candidate.read_bytes()).decode("ascii")
            replacements[raw_reference] = f"data:{content_type};base64,{encoded}"
            embedded += 1
        except OSError as exc:
            failures.append({"type": "image", "reference": raw_reference, "error": str(exc)})

    localized = markdown
    for raw_reference, data_url in replacements.items():
        localized = localized.replace(raw_reference, data_url)
    return localized, embedded


def repair_exported_local_image_references(output_root: Path) -> tuple[int, list[dict[str, str]]]:
    """Restore local Markdown image paths after ima flattens imported files."""

    output_root = output_root.resolve()
    image_paths = [
        path
        for path in iter_regular_files_under_root(output_root)
        if path.suffix.lower() in IMAGE_URL_SUFFIXES
    ]
    by_name: dict[str, list[Path]] = {}
    for path in image_paths:
        by_name.setdefault(path.name, []).append(path)

    repaired = 0
    failures: list[dict[str, str]] = []
    for markdown_path in iter_regular_files_under_root(output_root, suffixes={".md", ".markdown"}):
        try:
            text = markdown_path.read_text("utf-8", errors="ignore")
        except OSError as exc:
            failures.append({"title": markdown_path.name, "reference": "", "reason": str(exc)})
            continue
        for raw_reference in _local_image_references(text):
            parsed = urllib.parse.urlparse(raw_reference)
            if parsed.scheme or parsed.netloc or raw_reference.startswith(("/", "\\", "data:")):
                continue
            reference = urllib.parse.unquote(raw_reference.split("#", 1)[0].split("?", 1)[0])
            if not reference:
                continue
            target = (markdown_path.parent / reference).resolve()
            try:
                target.relative_to(output_root)
            except ValueError:
                failures.append({"title": markdown_path.name, "reference": raw_reference, "reason": "引用路径超出导出目录"})
                continue
            if target.is_file():
                continue
            candidates = [candidate for candidate in by_name.get(Path(reference).name, []) if candidate.is_file()]
            if len(candidates) != 1:
                reason = "找不到同名图片" if not candidates else "同名图片不唯一"
                failures.append({"title": markdown_path.name, "reference": raw_reference, "reason": reason})
                continue
            source = candidates[0]
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
                by_name[Path(reference).name] = [candidate for candidate in candidates if candidate != source]
                repaired += 1
            except OSError as exc:
                failures.append({"title": markdown_path.name, "reference": raw_reference, "reason": str(exc)})
    return repaired, failures


def _note_id_from_media_info(media_info: dict[str, Any]) -> str:
    for key in ("note_id", "notebook_id", "doc_id", "content_id"):
        value = media_info.get(key)
        if value:
            return str(value).strip()
    for key in ("notebook_ext_info", "note_info", "ext_info"):
        nested = media_info.get(key)
        if isinstance(nested, dict):
            note_id = _note_id_from_media_info(nested)
            if note_id:
                return note_id
    return ""


def _resource_warning(resource_failures: list[dict[str, str]]) -> str:
    if not resource_failures:
        return ""
    hosts = sorted({item.get("host", "unknown-host") for item in resource_failures})
    return f"{len(resource_failures)} 张图片下载失败（{', '.join(hosts[:3])}）"


def save_note_entry(
    client: ImaClient,
    entry: KnowledgeEntry,
    target_dir: Path,
    resource_failures: list[dict[str, str]] | None = None,
) -> Path:
    media_info = client.wiki("get_media_info", {"media_id": entry.media_id})
    note_id = _note_id_from_media_info(media_info)
    if not note_id:
        raise ImaError("笔记类型没有返回 notebook_id")
    try:
        note_data = client.note("get_doc_content", {"note_id": note_id, "target_content_format": 1})
    except ImaError:
        note_data = client.note("get_doc_content", {"note_id": note_id, "target_content_format": 0})
    text = extract_content_text(note_data).strip()
    if not text:
        text = f"# {entry.title}\n\n> ima 笔记接口未返回可导出的正文。"
    elif not text.lstrip().startswith("#"):
        text = f"# {entry.title}\n\n{text}\n"
    target_dir.mkdir(parents=True, exist_ok=True)
    name = sanitize_filename(entry.title)
    if Path(name).suffix.lower() not in {".md", ".markdown"}:
        name += ".md"
    target = unique_path(target_dir / name)
    failures = resource_failures if resource_failures is not None else []
    text, _ = localize_markdown_images(
        text,
        target_dir / f"{target.stem}_assets",
        failures,
    )
    target.write_text(text, "utf-8")
    return target


def save_media_entry(client: ImaClient, entry: KnowledgeEntry, output_root: Path) -> tuple[str, Path | None, str]:
    target_dir = output_root / sanitize_filename(entry.kb_name, "knowledge-base")
    for part in entry.relative_parts:
        target_dir /= sanitize_filename(part, "folder")
    target_dir.mkdir(parents=True, exist_ok=True)

    media_info = client.wiki("get_media_info", {"media_id": entry.media_id})
    media_type = int(media_info.get("media_type") or entry.media_type or 0)
    resource_failures: list[dict[str, str]] = []
    if media_type == 11:
        path = save_note_entry(client, entry, target_dir, resource_failures)
        return "exported_note", path, _resource_warning(resource_failures)

    url_info = media_info.get("url_info") or {}
    url = str(url_info.get("url") or "")
    headers = url_info.get("headers") if isinstance(url_info.get("headers"), dict) else {}
    if not url:
        return "skipped", None, "ima 未返回可下载原文链接"

    content, content_type = download_url(url, {str(k): str(v) for k, v in headers.items()})
    ext = extension_for_download(entry.title, media_type, content_type)
    name = sanitize_filename(entry.title)
    if not Path(name).suffix:
        name += ext
    target = unique_path(target_dir / name)
    if media_type == 7 or target.suffix.lower() in {".md", ".markdown"}:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            target.write_bytes(content)
            return "exported", target, ""
        text, _ = localize_markdown_images(
            text,
            target_dir / f"{target.stem}_assets",
            resource_failures,
        )
        target.write_text(text, "utf-8")
    else:
        target.write_bytes(content)
    return "exported", target, _resource_warning(resource_failures)


def selected_entries(entries: list[KnowledgeEntry], doc_ids: list[str]) -> list[KnowledgeEntry]:
    media_entries = [entry for entry in entries if not entry.is_folder]
    if not doc_ids:
        return media_entries
    selected = set(doc_ids)
    selected_media = [entry for entry in media_entries if entry.export_id in selected or entry.media_id in selected]
    if media_entries and not selected_media:
        preview = ", ".join(sorted(selected)[:5])
        raise ImaError(
            "选择的 ima 文档未匹配当前目录，"
            "请重新读取目录后再试。未匹配 ID：" + preview
        )
    return selected_media


def export_selected(client: ImaClient, args: argparse.Namespace) -> dict[str, Any]:
    output = Path(args.output or default_output_dir()).resolve()
    checkpoint = open_checkpoint_from_args(args, "ima", "export")
    kbs, entries = scan_remote_tree(client, args)
    docs = selected_entries(entries, args.doc_id or [])
    if checkpoint:
        checkpoint.start_task(
            {
                "source": str(args.knowledge_base_id or ""),
                "outputDir": str(output),
                "totalDocs": len(docs),
                "resume": bool(getattr(args, "resume", False)),
                "retryFailed": bool(getattr(args, "retry_failed", False)),
            }
        )
        for entry in docs:
            checkpoint.upsert_item(
                f"ima:entry:{entry.export_id}",
                title=entry.title,
                source_url="/".join(
                    [
                        sanitize_filename(entry.kb_name, "knowledge-base"),
                        *(sanitize_filename(part, "folder") for part in entry.relative_parts),
                        sanitize_filename(entry.title),
                    ]
                ),
                source_id=entry.export_id,
                parent_key=entry.parent_node_id,
                metadata={"exportId": entry.export_id, "mediaId": entry.media_id, "knowledgeBaseId": entry.kb_id},
            )
        if getattr(args, "retry_failed", False):
            docs = [entry for entry in docs if checkpoint.item_status(f"ima:entry:{entry.export_id}") == "failed"]
    total = len(docs)
    exported = 0
    skipped = 0
    failures: list[dict[str, str]] = []
    resource_failures: list[dict[str, str]] = []
    local_reference_repairs = 0
    local_reference_failures: list[dict[str, str]] = []
    started = time.time()
    emit(
        f"开始导出 ima 知识库内容：共 {total} 个文件。",
        event="task.started",
        totals={"documents": total, "knowledgeBases": len(kbs)},
        output=str(output),
    )
    for index, entry in enumerate(docs, 1):
        check_stopped(args)
        item_key = f"ima:entry:{entry.export_id}"
        try:
            if checkpoint and getattr(args, "resume", False) and checkpoint.item_status(item_key) == "completed":
                skipped += 1
                continue
            if checkpoint:
                checkpoint.start_item(item_key, "download")
            emit(
                f"开始导出 ima 文件：{entry.title}",
                event="document.export.started",
                doc={"id": entry.export_id, "title": entry.title, "index": index},
            )
            status, path, reason = save_media_entry(client, entry, output)
            if status.startswith("exported"):
                exported += 1
                if reason:
                    resource_failures.append({"title": entry.title, "reason": reason})
                if checkpoint:
                    checkpoint.complete_item(
                        item_key,
                        local_path=str(path or ""),
                        metadata={"exportId": entry.export_id, "resourceWarning": reason},
                    )
                emit(
                    f"ima 文件导出完成：{entry.title}",
                    event="document.export.completed",
                    doc={"id": entry.export_id, "title": entry.title, "index": index, "path": str(path)},
                    warning=reason,
                )
            else:
                skipped += 1
                if reason:
                    if checkpoint:
                        checkpoint.fail_item(item_key, reason)
                    failures.append({"title": entry.title, "reason": reason})
                    emit(
                        f"ima 文件跳过：{entry.title}：{reason}",
                        event="document.export.failed",
                        level="warn",
                        doc={"id": entry.export_id, "title": entry.title, "index": index},
                        error={"message": reason},
                    )
                elif checkpoint:
                    checkpoint.skip_item(item_key, status)
        except Exception as exc:
            skipped += 1
            if checkpoint:
                checkpoint.fail_item(item_key, str(exc))
            failures.append({"title": entry.title, "reason": str(exc)})
            emit(
                f"ima 文件导出失败：{entry.title}：{exc}",
                event="document.export.failed",
                level="error",
                doc={"id": entry.export_id, "title": entry.title, "index": index},
                error={"type": type(exc).__name__, "message": str(exc)},
            )
        if args.progress_every and (index % args.progress_every == 0 or index == total):
            emit(
                f"progress {index}/{total} exported={exported} skipped={skipped} failures={len(failures)}",
                event="task.progress",
                progress={"current": index, "total": total},
                stats={
                    "exportedDocs": exported,
                    "skippedDocs": skipped,
                    "failureCount": len(failures),
                    "resourceFailureCount": len(resource_failures),
                },
            )
    local_reference_repairs, local_reference_failures = repair_exported_local_image_references(output)
    for item in local_reference_failures:
        resource_failures.append(
            {
                "title": item.get("title", ""),
                "reason": f"本地图片引用未修复：{item.get('reference', '')}（{item.get('reason', '未知原因')}）",
            }
        )
    if local_reference_repairs or local_reference_failures:
        emit(
            f"ima 本地图片引用修复完成：修复 {local_reference_repairs} 个，失败 {len(local_reference_failures)} 个",
            event="document.export.resources_repaired",
            level="warn" if local_reference_failures else "info",
            stats={"repaired": local_reference_repairs, "failures": len(local_reference_failures)},
        )
    report = {
        "provider": "ima",
        "mode": "export",
        "knowledgeBaseCount": len(kbs),
        "selectedDocs": total,
        "exportedDocs": exported,
        "skippedDocs": skipped,
        "failureCount": len(failures),
        "failures": failures[:20],
        "resourceFailureCount": len(resource_failures),
        "resourceFailures": resource_failures[:20],
        "localImageReferenceRepairs": local_reference_repairs,
        "localImageReferenceFailures": local_reference_failures[:20],
        "output": str(output),
        "elapsedSeconds": round(time.time() - started, 1),
        "requestCount": int(getattr(args, "_request_count", 0) or 0),
    }
    if checkpoint:
        report["checkpoint"] = checkpoint.stats()
    report = finalize_report(report, provider="ima", mode="export", output=output)
    if checkpoint:
        if failures:
            checkpoint.fail_task(f"{len(failures)} 个文档失败", status="failed")
        else:
            checkpoint.complete_task(report)
        checkpoint.close()
    emit(
        "ima 导出完成" if not failures else f"ima 导出完成，但有 {len(failures)} 个失败项",
        event="task.completed",
        level="success" if not failures else "warn",
        stats={
            "exportedDocs": exported,
            "skippedDocs": skipped,
            "failureCount": len(failures),
            "resourceFailureCount": len(resource_failures),
            "localImageReferenceRepairs": local_reference_repairs,
            "localImageReferenceFailures": len(local_reference_failures),
        },
    )
    return report


def file_media_info(path: Path) -> tuple[int, str]:
    suffix = path.suffix.lower()
    if suffix in MEDIA_TYPES:
        return MEDIA_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(str(path))
    if guessed:
        for ext, info in MEDIA_TYPES.items():
            if info[1] == guessed:
                return info
    raise ImaError(f"暂不支持上传此文件类型：{path.name}")


def should_skip_source_path(source_dir: Path, path: Path) -> bool:
    try:
        relative_parts = path.resolve().relative_to(source_dir.resolve()).parts
    except (OSError, RuntimeError, ValueError):
        return True
    return any(part in SKIP_SOURCE_DIRS or part.startswith(".") for part in relative_parts[:-1])


def is_inside(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def markdown_reference_candidates(raw: str) -> list[str]:
    text = html.unescape(str(raw or "").strip())
    if not text:
        return []
    if text.startswith("<") and text.endswith(">"):
        text = text[1:-1].strip()
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme and parsed.scheme.lower() not in {"file"}:
        return []
    text = urllib.parse.unquote(text)
    if text.startswith("file://"):
        text = urllib.request.url2pathname(urllib.parse.urlparse(text).path)
    text = text.split("#", 1)[0].split("?", 1)[0].strip()
    candidates = [text]
    for marker in (' "', " '", " \t"):
        if marker in text:
            candidates.append(text.split(marker, 1)[0].strip())
    if " " in text:
        candidates.append(text.split(" ", 1)[0].strip())
    result: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        cleaned = item.strip().strip('"').strip("'")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def collect_markdown_referenced_files(source_dir: Path) -> set[Path]:
    referenced: set[Path] = set()
    for md_path in sorted(iter_regular_files_under_root(source_dir, suffixes={".md", ".markdown"})):
        if should_skip_source_path(source_dir, md_path):
            continue
        try:
            text = md_path.read_text("utf-8", errors="ignore")
        except OSError:
            continue
        for match in MARKDOWN_REFERENCE_RE.finditer(text):
            raw = next((group for group in match.groups() if group), "")
            for candidate in markdown_reference_candidates(raw):
                resolved = resolve_local_reference(source_dir, md_path, candidate)
                # Markdown pages linked by an index are separate import targets, not
                # embedded resources. Keeping them out of this exclusion list avoids
                # dropping an entire exported knowledge-base tree.
                if resolved and resolved.suffix.lower() not in {".md", ".markdown"}:
                    referenced.add(resolved)
                    break
    return referenced


def scan_source_files(source_dir: Path, *, include_referenced_assets: bool = False) -> list[dict[str, Any]]:
    if not source_dir.exists():
        raise ImaError(f"本地目录不存在：{source_dir}")
    source_dir = source_dir.resolve()
    referenced_assets = set() if include_referenced_assets else collect_markdown_referenced_files(source_dir)
    files: list[dict[str, Any]] = []
    for path in sorted(iter_regular_files_under_root(source_dir)):
        if should_skip_source_path(source_dir, path):
            continue
        if path in referenced_assets:
            continue
        if path.suffix.lower() not in MEDIA_TYPES:
            continue
        rel = path.relative_to(source_dir).as_posix()
        media_type, content_type = file_media_info(path)
        files.append(
            {
                "path": str(path),
                "relativePath": rel,
                "title": path.name,
                "size": path.stat().st_size,
                "level": len(Path(rel).parts) - 1,
                "mediaType": media_type,
                "contentType": content_type,
            }
        )
    return files


def scan_source(args: argparse.Namespace) -> dict[str, Any]:
    source_dir = Path(args.source_dir or "").resolve()
    files = scan_source_files(source_dir, include_referenced_assets=args.include_referenced_assets)
    return {
        "provider": "ima",
        "mode": "scan-source",
        "sourceDir": str(source_dir),
        "docCount": len(files),
        "sampleDocs": files[:20],
    }


def is_repeated(client: ImaClient, kb_id: str, folder_id: str, name: str, media_type: int) -> bool:
    payload: dict[str, Any] = {
        "knowledge_base_id": kb_id,
        "params": [{"name": name, "media_type": media_type}],
    }
    if folder_id:
        payload["folder_id"] = folder_id
    data = client.wiki("check_repeated_names", payload)
    for item in response_list(data, "results"):
        if str(item.get("name") or "") == name:
            return bool(item.get("is_repeated"))
    return False


def list_remote_folder_items(client: ImaClient, kb_id: str, folder_id: str) -> list[dict[str, Any]]:
    """List every direct item below one remote folder without recursing."""

    cursor = ""
    items: list[dict[str, Any]] = []
    while True:
        payload: dict[str, Any] = {"knowledge_base_id": kb_id, "cursor": cursor, "limit": 50}
        if folder_id:
            payload["folder_id"] = folder_id
        data = client.wiki("get_knowledge_list", payload)
        items.extend(response_list(data, "knowledge_list", "info_list", "list"))
        if data.get("is_end", True):
            break
        next_cursor = str(data.get("next_cursor") or "")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
    return items


def remote_child_folders(
    client: ImaClient,
    kb_id: str,
    folder_id: str,
    cache: dict[str, dict[str, str | None]],
) -> dict[str, str | None]:
    """Return direct folders by title; ``None`` marks an ambiguous duplicate."""

    cache_key = folder_id or ""
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    children: dict[str, str | None] = {}
    for item in list_remote_folder_items(client, kb_id, folder_id):
        if not is_folder_item(item):
            continue
        title = item_title(item)
        child_id = item_id(item)
        if not child_id:
            continue
        if title in children and children[title] != child_id:
            children[title] = None
        else:
            children[title] = child_id
    cache[cache_key] = children
    return children


def ensure_remote_folder(
    client: ImaClient,
    args: argparse.Namespace,
    *,
    parent_folder_id: str,
    title: str,
    cache: dict[str, dict[str, str | None]],
) -> str:
    """Find or create one folder below ``parent_folder_id`` and return its ID."""

    if not title or title in {".", ".."}:
        raise ImaError("本地目录名称无效，无法创建 ima 文件夹")

    children = remote_child_folders(client, args.knowledge_base_id, parent_folder_id, cache)
    if title in children:
        existing = children[title]
        if existing:
            return existing
        raise ImaError(f"目标目录存在多个同名文件夹，无法安全导入：{title}")

    payload: dict[str, Any] = {"knowledge_base_id": args.knowledge_base_id, "name": title}
    if parent_folder_id:
        payload["folder_id"] = parent_folder_id
    client.wiki("create_folder", payload)
    emit(
        f"已创建 ima 文件夹：{title}",
        event="folder.created",
        folder={"title": title, "parentFolderId": parent_folder_id},
    )

    # The current API does not consistently return the created folder ID. Refresh
    # the direct listing instead of issuing a second create request on a slow read.
    cache.pop(parent_folder_id or "", None)
    for attempt in range(3):
        children = remote_child_folders(client, args.knowledge_base_id, parent_folder_id, cache)
        created = children.get(title)
        if created:
            return created
        if title in children:
            raise ImaError(f"新建的 ima 文件夹名称重复，无法安全定位：{title}")
        if attempt < 2:
            wait_with_stop(args, 0.25)
            cache.pop(parent_folder_id or "", None)
    raise ImaError(f"ima 已接受创建文件夹请求，但未读取到目录：{title}")


def import_target_folder(
    client: ImaClient,
    args: argparse.Namespace,
    path: Path,
    source_dir: Path,
    cache: dict[str, dict[str, str | None]],
) -> str:
    """Resolve the destination folder for one local file, preserving its parents."""

    target_folder_id = args.folder_id or ""
    if not getattr(args, "preserve_folders", True):
        return target_folder_id
    try:
        relative_parent = path.relative_to(source_dir).parent
    except ValueError:
        return target_folder_id
    for part in relative_parent.parts:
        if part and part != ".":
            target_folder_id = ensure_remote_folder(
                client,
                args,
                parent_folder_id=target_folder_id,
                title=part,
                cache=cache,
            )
    return target_folder_id


def hmac_sha1(key: bytes | str, data: str) -> str:
    if isinstance(key, str):
        key = key.encode("utf-8")
    return hmac.new(key, data.encode("utf-8"), hashlib.sha1).hexdigest()


def sha1_hex(data: str) -> str:
    return hashlib.sha1(data.encode("utf-8")).hexdigest()


def cos_authorization(
    *,
    secret_id: str,
    secret_key: str,
    method: str,
    pathname: str,
    headers: dict[str, str],
    start_time: str,
    expired_time: str,
) -> str:
    key_time = f"{start_time};{expired_time}"
    sign_key = hmac_sha1(secret_key, key_time)
    header_keys = sorted(headers)
    http_headers = "&".join(f"{key.lower()}={urllib.parse.quote(str(headers[key]), safe='')}" for key in header_keys)
    http_string = f"{method.lower()}\n{pathname}\n\n{http_headers}\n"
    string_to_sign = f"sha1\n{key_time}\n{sha1_hex(http_string)}\n"
    signature = hmac_sha1(sign_key, string_to_sign)
    header_list = ";".join(key.lower() for key in header_keys)
    return "&".join(
        [
            "q-sign-algorithm=sha1",
            f"q-ak={secret_id}",
            f"q-sign-time={key_time}",
            f"q-key-time={key_time}",
            f"q-header-list={header_list}",
            "q-url-param-list=",
            f"q-signature={signature}",
        ]
    )


def upload_to_cos(path: Path, credential: dict[str, Any], content_type: str) -> None:
    token = str(credential.get("token") or "")
    secret_id = str(credential.get("secret_id") or "")
    secret_key = str(credential.get("secret_key") or "")
    bucket = str(credential.get("bucket_name") or "")
    region = str(credential.get("region") or "")
    cos_key = str(credential.get("cos_key") or "")
    start_time = str(credential.get("start_time") or int(time.time()))
    expired_time = str(credential.get("expired_time") or int(time.time()) + 3600)
    if not all([token, secret_id, secret_key, bucket, region, cos_key]):
        raise ImaError("create_media 未返回完整 COS 上传凭证")
    data = path.read_bytes()
    hostname = f"{bucket}.cos.{region}.myqcloud.com"
    pathname = f"/{cos_key}"
    signed_headers = {"content-length": str(len(data)), "host": hostname}
    auth = cos_authorization(
        secret_id=secret_id,
        secret_key=secret_key,
        method="PUT",
        pathname=pathname,
        headers=signed_headers,
        start_time=start_time,
        expired_time=expired_time,
    )
    request = urllib.request.Request(
        f"https://{hostname}{pathname}",
        data=data,
        headers={
            "Content-Type": content_type,
            "Content-Length": str(len(data)),
            "Authorization": auth,
            "x-cos-security-token": token,
        },
        method="PUT",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            if response.status < 200 or response.status >= 300:
                raise ImaError(f"COS 上传失败 HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ImaError(f"COS 上传 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise ImaError(f"COS 上传失败：{exc}") from exc


def upload_markdown_note(
    client: ImaClient,
    args: argparse.Namespace,
    path: Path,
    source_root: Path,
    resource_failures: list[dict[str, str]],
    folder_id: str | None = None,
) -> str:
    kb_id = args.knowledge_base_id
    if not kb_id:
        raise ImaError("导入需要填写目标知识库 ID")
    target_folder_id = args.folder_id or "" if folder_id is None else folder_id
    file_name = path.name
    if args.skip_existing and is_repeated(client, kb_id, target_folder_id, file_name, 11):
        return "skipped_existing"
    try:
        content = path.read_text("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImaError(f"Markdown 不是有效 UTF-8：{path.name}") from exc
    content, embedded = embed_local_markdown_images(content, path, source_root, resource_failures)
    created = client.note(
        "import_doc",
        {
            "title": file_name,
            "content": content,
            "content_format": 1,
        },
    )
    note_id = _note_id_from_media_info(created)
    if not note_id:
        raise ImaError("import_doc 未返回 note_id")
    add_payload: dict[str, Any] = {
        "media_type": 11,
        "title": file_name,
        "knowledge_base_id": kb_id,
        "note_info": {"content_id": note_id},
    }
    if target_folder_id:
        add_payload["folder_id"] = target_folder_id
    client.wiki("add_knowledge", add_payload)
    emit(
        f"Markdown 笔记已导入：{file_name}（内嵌图片 {embedded} 张）",
        event="document.import.resources_embedded",
        stats={"embeddedImages": embedded},
    )
    return note_id


def upload_file(client: ImaClient, args: argparse.Namespace, path: Path, folder_id: str | None = None) -> str:
    kb_id = args.knowledge_base_id
    if not kb_id:
        raise ImaError("导入需要填写目标知识库 ID")
    target_folder_id = args.folder_id or "" if folder_id is None else folder_id
    media_type, content_type = file_media_info(path)
    file_name = path.name
    if args.skip_existing and is_repeated(client, kb_id, target_folder_id, file_name, media_type):
        return "skipped_existing"

    create_payload = {
        "file_name": file_name,
        "file_size": path.stat().st_size,
        "content_type": content_type,
        "knowledge_base_id": kb_id,
        "file_ext": path.suffix.lower().lstrip("."),
    }
    created = client.wiki("create_media", create_payload)
    media_id = str(created.get("media_id") or "")
    credential = created.get("cos_credential") or {}
    if not media_id:
        raise ImaError("create_media 未返回 media_id")
    upload_to_cos(path, credential, content_type)

    add_payload: dict[str, Any] = {
        "media_type": media_type,
        "media_id": media_id,
        "title": file_name,
        "knowledge_base_id": kb_id,
        "file_info": {
            "cos_key": str(credential.get("cos_key") or ""),
            "file_size": path.stat().st_size,
            "last_modify_time": int(path.stat().st_mtime),
            "password": "",
            "file_name": file_name,
        },
    }
    if target_folder_id:
        add_payload["folder_id"] = target_folder_id
    client.wiki("add_knowledge", add_payload)
    return media_id


def import_files(client: ImaClient, args: argparse.Namespace) -> dict[str, Any]:
    source_dir = Path(args.source_dir or "").resolve()
    if not args.knowledge_base_id:
        raise ImaError("请先填写目标知识库 ID")
    files = [Path(item["path"]) for item in scan_source_files(source_dir, include_referenced_assets=args.include_referenced_assets)]
    if args.import_one and args.source_file:
        source_file = Path(args.source_file).resolve()
        files = [source_file]
    if args.import_one and files:
        files = files[:1]
    if args.max_import and args.max_import > 0:
        files = files[: args.max_import]
    if not files:
        raise ImaError("没有找到可导入的文件")
    if not args.yes:
        raise ImaError("导入会写入 ima 知识库，请加 --yes 确认")

    imported = 0
    skipped = 0
    failures: list[dict[str, str]] = []
    resource_failures: list[dict[str, str]] = []
    folder_cache: dict[str, dict[str, str | None]] = {}
    started = time.time()
    total = len(files)
    emit(
        f"开始导入 ima 知识库文件：共 {total} 个文件。",
        event="task.started",
        totals={"documents": total},
        sourceDir=str(source_dir),
        target={"knowledgeBaseId": args.knowledge_base_id, "folderId": args.folder_id or ""},
    )
    for index, path in enumerate(files, 1):
        check_stopped(args)
        try:
            relative_path = path.relative_to(source_dir).as_posix() if path.is_relative_to(source_dir) else str(path)
            emit(
                f"开始上传 ima 文件：{relative_path}",
                event="document.import.started",
                doc={"path": relative_path, "index": index},
            )
            file_resources: list[dict[str, str]] = []
            target_folder_id = import_target_folder(client, args, path, source_dir, folder_cache)
            if path.suffix.lower() in {".md", ".markdown"}:
                result = upload_markdown_note(client, args, path, source_dir, file_resources, target_folder_id)
            else:
                result = upload_file(client, args, path, target_folder_id)
            for item in file_resources:
                resource_failures.append({"relativePath": relative_path, **item})
            if result == "skipped_existing":
                skipped += 1
            else:
                imported += 1
            emit(
                f"ima 文件处理完成：{relative_path}",
                event="document.import.completed",
                doc={"path": relative_path, "index": index},
                result={"status": result},
            )
        except Exception as exc:
            failures.append({"relativePath": path.relative_to(source_dir).as_posix() if path.is_relative_to(source_dir) else str(path), "error": str(exc)})
            emit(
                f"ima 文件导入失败：{path.name}：{exc}",
                event="document.import.failed",
                level="error",
                doc={"path": str(path), "index": index},
                error={"type": type(exc).__name__, "message": str(exc)},
            )
        if args.progress_every and (index % args.progress_every == 0 or index == total):
            emit(
                f"progress {index}/{total} imported={imported} skipped={skipped} failures={len(failures)}",
                event="task.progress",
                progress={"current": index, "total": total},
                stats={
                    "importedDocs": imported,
                    "skippedDocs": skipped,
                    "failureCount": len(failures),
                    "resourceFailureCount": len(resource_failures),
                },
            )
    report = {
        "provider": "ima",
        "mode": "import",
        "sourceDir": str(source_dir),
        "targetKnowledgeBaseId": args.knowledge_base_id,
        "targetFolderId": args.folder_id or "",
        "selectedFiles": total,
        "importedFiles": imported,
        "skippedFiles": skipped,
        "failureCount": len(failures),
        "failures": failures[:20],
        "resourceFailureCount": len(resource_failures),
        "resourceFailures": resource_failures[:20],
        "elapsedSeconds": round(time.time() - started, 1),
        "requestCount": int(getattr(args, "_request_count", 0) or 0),
    }
    report = finalize_report(report, provider="ima", mode="import", output=source_dir)
    emit(
        "ima 导入完成" if not failures else f"ima 导入完成，但有 {len(failures)} 个失败项",
        event="task.completed",
        level="success" if not failures else "warn",
        stats={
            "importedDocs": imported,
            "skippedDocs": skipped,
            "failureCount": len(failures),
            "resourceFailureCount": len(resource_failures),
        },
    )
    return report


def list_knowledge_bases(client: ImaClient, args: argparse.Namespace) -> dict[str, Any]:
    kbs = get_knowledge_bases(client, addable_only=bool(args.addable_only))
    return {
        "provider": "ima",
        "knowledgeBases": [kb.__dict__ for kb in kbs],
        "count": len(kbs),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="万能导：ima 知识库导入导出")
    parser.add_argument("--gui", action="store_true", help="提示用户使用桌面端 ima 入口")
    parser.add_argument("--client-id", help="ima OpenAPI Client ID")
    parser.add_argument("--api-key", help="ima OpenAPI API Key")
    parser.add_argument("--config-file", help="本机配置文件路径，默认使用用户数据目录")
    parser.add_argument("--save-config", action="store_true", help="保存 Client ID / API Key 到本机配置")
    parser.add_argument("--knowledge-base-id", help="目标或来源知识库 ID")
    parser.add_argument("--knowledge-base-name", help="知识库显示名称，仅用于导出目录命名")
    parser.add_argument("--folder-id", help="目标已有文件夹 ID，导入 URL 或文件时可选")
    parser.add_argument("--list-knowledge-bases", action="store_true", help="列出可访问知识库")
    parser.add_argument("--addable-only", action="store_true", help="只列出可添加内容的知识库")
    parser.add_argument("--scan-toc", action="store_true", help="读取知识库目录树")
    parser.add_argument("--output", help="导出输出目录")
    parser.add_argument("--doc-id", action="append", default=[], help="要导出的条目 ID，可重复")
    parser.add_argument("--doc-id-file", default="", help="从文件读取要导出的条目 ID，JSON 数组或逐行文本均可")
    parser.add_argument("--source-dir", help="本地待导入目录")
    parser.add_argument("--source-file", help="单篇测试文件")
    parser.add_argument("--scan-source", action="store_true", help="扫描本地可导入文件")
    parser.add_argument(
        "--include-referenced-assets",
        dest="include_referenced_assets",
        action="store_true",
        default=False,
        help="将 Markdown 引用的本地图片/附件也作为独立文件上传（默认关闭；图片会内嵌到 Markdown 笔记）",
    )
    parser.add_argument(
        "--exclude-referenced-assets",
        dest="include_referenced_assets",
        action="store_false",
        help="不单独上传 Markdown 引用的本地图片/附件",
    )
    parser.add_argument(
        "--preserve-folders",
        dest="preserve_folders",
        action="store_true",
        default=True,
        help="保留本地目录层级，自动创建目标知识库中缺失的文件夹（默认开启）",
    )
    parser.add_argument(
        "--flatten-folders",
        dest="preserve_folders",
        action="store_false",
        help="将所有文件直接导入所选目标文件夹",
    )
    parser.add_argument("--import-one", action="store_true", help="导入第一篇或 source-file")
    parser.add_argument("--import-all", action="store_true", help="批量导入")
    parser.add_argument("--skip-existing", action="store_true", default=True, help="目标已有同名文件时跳过")
    parser.add_argument("--overwrite-existing", action="store_false", dest="skip_existing", help="不做同名跳过检查")
    parser.add_argument("--max-import", type=int, default=0, help="最多导入数量，0 表示不限")
    parser.add_argument("--yes", action="store_true", help="确认执行写入操作")
    parser.add_argument("--request-delay", type=float, default=0.2, help="API 请求固定延迟秒")
    parser.add_argument("--request-jitter", type=float, default=0.2, help="API 请求随机浮动秒")
    parser.add_argument("--progress-every", type=int, default=10, help="每处理多少条输出一次进度")
    add_checkpoint_args(parser)
    args = parser.parse_args(argv)
    extend_arg_list_from_file(args, "doc_id")
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        if args.gui:
            print("旧版 Python GUI 已废弃，请使用 Wandao 桌面端：start-wandao.cmd 或 ./start-wandao.sh", file=sys.stderr)
            return 2
        if args.scan_source:
            emit_json(scan_source(args))
            return 0
        if args.save_config:
            emit_json(maybe_save_config(args))
            return 0
        client_id, api_key = require_credentials(args)
        client = ImaClient(client_id, api_key, args)
        if args.list_knowledge_bases:
            emit_json(list_knowledge_bases(client, args))
        elif args.scan_toc:
            emit_json(scan_toc(client, args))
        elif args.import_one or args.import_all:
            emit_json(import_files(client, args))
        else:
            emit_json(export_selected(client, args))
        return 0
    except ImaStopped as exc:
        emit(str(exc), event="task.stopped", level="warn")
        emit_json({"provider": "ima", "stopped": True, "error": str(exc)})
        return 130
    except Exception as exc:
        emit(
            str(exc),
            event="task.failed",
            level="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        print(str(exc), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
