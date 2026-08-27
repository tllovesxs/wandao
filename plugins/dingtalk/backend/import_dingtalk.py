#!/usr/bin/env python3
"""Import local documents into a DingTalk folder without losing Markdown images.

The browser is deliberately the only place that sees DingTalk cookies, access
tokens, and short-lived OSS URLs.  Python streams local file bytes to a small
page helper through CDP; the helper uploads them, replaces opaque image
placeholders in Markdown, and starts the DingTalk import request.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import mimetypes
import os
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


# The export implementation already owns the isolated DingTalk browser profile
# and the safe connection/login behavior.  This script is also executable as a
# standalone file, so make its sibling module importable when the host does not
# add the backend directory to sys.path.
_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from export_dingtalk import (  # noqa: E402
    DEFAULT_PORT,
    ENTRY_URL,
    SUPPORTED_HOSTS,
    auth_path_from_args,
    connect_dingtalk_browser,
    default_auth_path,
    default_profile_path,
    parse_dingtalk_url,
    safe_path_segment,
    save_auth_summary,
)
from wandao_core.browser import (  # noqa: E402
    CDPClient,
    ExportError,
    ExportStopped,
    check_stopped,
    default_data_dir,
    emit,
    throttle_request,
)
from wandao_core.checkpoint import add_checkpoint_args, open_checkpoint_from_args  # noqa: E402
from wandao_core.report import finalize_report  # noqa: E402


PLUGIN_ID = "dingtalk-import"
IMPORT_HELPER_VERSION = 1
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
MAX_IMAGE_BYTES = 25 * 1024 * 1024
CDP_CHUNK_BYTES = 384 * 1024
DEFAULT_REQUEST_DELAY = 0.3
DEFAULT_REQUEST_JITTER = 0.2

# Values are DingTalk's documentType enum observed in the web importer.  Do
# not guess unsupported types: for example, PDF is a cloud-file capability and
# not an online-document import capability.
ONLINE_DOCUMENT_TYPES: dict[str, int] = {
    "doc": 0,
    "docx": 0,
    "txt": 0,
    "md": 0,
    "mark": 0,
    "markdown": 0,
    "xls": 1,
    "xlsx": 1,
    "xmind": 6,
}

MARKDOWN_SUFFIXES = {"md", "mark", "markdown"}
IMAGE_SUFFIXES = {
    ".apng", ".avif", ".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".png", ".svg", ".tif", ".tiff", ".webp",
}
IGNORED_DIRECTORY_NAMES = {".git", ".wandao", "__pycache__", ".ds_store"}


@dataclass(frozen=True)
class LocalDocument:
    path: Path
    relative_path: Path
    title: str
    suffix: str
    document_type: int
    folders: tuple[str, ...]


@dataclass(frozen=True)
class LocalResource:
    resource_id: str
    path: Path
    relative_path: Path
    content_type: str
    size: int


@dataclass
class PreparedDocument:
    document: LocalDocument
    data: bytes
    resources: list[LocalResource] = field(default_factory=list)
    image_reference_count: int = 0
    image_warnings: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ImportPlan:
    source_root: Path
    root_folder_name: str
    documents: list[LocalDocument]
    prepared_documents: dict[str, PreparedDocument]
    folders: list[tuple[str, ...]]
    referenced_resources: dict[str, LocalResource]
    skipped_files: list[dict[str, str]]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _safe_error(value: Any) -> str:
    """Return an API error string while dropping accidental signed URL text."""
    text = str(value or "未知错误").strip()
    text = re.sub(r"https?://[^\s'\"]+", "[临时链接已隐藏]", text)
    return text[:500]


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _source_path(args: argparse.Namespace) -> tuple[Path, bool]:
    """Resolve the source and whether it is a directory import."""
    source_file = str(getattr(args, "source_file", "") or "").strip()
    source_dir = str(getattr(args, "source_dir", "") or "").strip()
    if source_file:
        path = Path(source_file).expanduser().resolve()
        if not path.is_file():
            raise ExportError(f"待导入文件不存在：{path}")
        if source_dir:
            root = Path(source_dir).expanduser().resolve()
            if not root.is_dir():
                raise ExportError(f"本地待导入目录不存在：{root}")
            if not _is_within(path, root):
                raise ExportError("单篇测试文件必须位于本地待导入目录中。")
            return path, False
        return path, False
    if not source_dir:
        raise ExportError("请填写本地待导入目录，或选择单篇测试文件。")
    path = Path(source_dir).expanduser().resolve()
    if not path.is_dir():
        raise ExportError(f"本地待导入目录不存在：{path}")
    return path, True


def _source_root(args: argparse.Namespace, source: Path, is_directory: bool) -> Path:
    if is_directory:
        return source
    source_dir = str(getattr(args, "source_dir", "") or "").strip()
    if source_dir:
        root = Path(source_dir).expanduser().resolve()
        if root.is_dir() and _is_within(source, root):
            return root
    return source.parent


def _relative_key(path: Path) -> str:
    return path.as_posix()


def _document_type_for(path: Path) -> int | None:
    return ONLINE_DOCUMENT_TYPES.get(path.suffix.lower().lstrip("."))


def _file_content_type(path: Path, suffix: str) -> str:
    if suffix in MARKDOWN_SUFFIXES:
        return "text/markdown"
    if suffix == "txt":
        return "text/plain"
    guessed, _encoding = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def _walk_files(source: Path, is_directory: bool) -> Iterable[Path]:
    if not is_directory:
        yield source
        return
    for candidate in sorted(source.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not candidate.is_file():
            continue
        relative_parts = candidate.relative_to(source).parts
        if any(part.casefold() in IGNORED_DIRECTORY_NAMES for part in relative_parts[:-1]):
            continue
        yield candidate


def _make_document(path: Path, source_root: Path) -> LocalDocument | None:
    document_type = _document_type_for(path)
    if document_type is None:
        return None
    relative_path = path.relative_to(source_root)
    suffix = path.suffix.lower().lstrip(".")
    title = safe_path_segment(path.stem, "未命名文档")
    return LocalDocument(
        path=path,
        relative_path=relative_path,
        title=title,
        suffix=suffix,
        document_type=document_type,
        folders=tuple(safe_path_segment(part, "未命名目录") for part in relative_path.parts[:-1]),
    )


def _read_markdown(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ExportError(f"Markdown 不是 UTF-8 或 GB18030 文本：{path}")


def _normalize_reference_id(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "").strip()).casefold()


_MD_INLINE_IMAGE = re.compile(
    r"(?P<prefix>!\[[^\]\r\n]*\]\(\s*)(?P<target><[^>\r\n]+>|[^)\s]+)(?P<suffix>(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\))"
)
_HTML_IMAGE_SRC = re.compile(
    r"(?P<prefix><img\b[^>]*?\bsrc\s*=\s*)(?P<quote>[\"'])(?P<target>.*?)(?P=quote)", re.IGNORECASE
)
_MD_IMAGE_REFERENCE = re.compile(r"!\[[^\]\r\n]*\]\[(?P<label>[^\]\r\n]*)\]")
_MD_REFERENCE_DEFINITION = re.compile(
    r"^(?P<prefix>\s*\[(?P<label>[^\]\r\n]+)\]:\s*)(?P<target><[^>\r\n]+>|\S+)(?P<suffix>.*)$",
    re.MULTILINE,
)


def _resolve_local_image(target: str, document_path: Path, source_root: Path) -> tuple[Path | None, str | None]:
    value = html.unescape(target or "").strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme or parsed.netloc or value.startswith("//") or value.startswith("#"):
        return None, None
    if not parsed.path:
        return None, None
    local_path = urllib.parse.unquote(parsed.path).replace("/", os.sep).replace("\\", os.sep)
    candidate = (document_path.parent / local_path).resolve()
    if not _is_within(candidate, source_root):
        return None, "图片引用位于本次导入目录外，已保留原链接"
    if not candidate.is_file():
        return None, "本地图片不存在，已保留原链接"
    if candidate.suffix.lower() not in IMAGE_SUFFIXES:
        return None, "本地引用不是受支持的图片格式，已保留原链接"
    return candidate, None


def _resource_for_path(path: Path, source_root: Path, cache: dict[str, LocalResource]) -> LocalResource:
    # ``Path.resolve()`` is important here on macOS: temporary directories
    # may be presented as /var/... while resolved image paths are under the
    # equivalent /private/var/... location.  Normalize both operands before
    # calculating the relative path so local Markdown images remain importable.
    resolved_path = path.resolve()
    resolved_root = source_root.resolve()
    raw = resolved_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    resource_id = f"image-{digest[:40]}"
    if resource_id not in cache:
        cache[resource_id] = LocalResource(
            resource_id=resource_id,
            path=resolved_path,
            relative_path=resolved_path.relative_to(resolved_root),
            content_type=_file_content_type(resolved_path, resolved_path.suffix.lower().lstrip(".")),
            size=len(raw),
        )
    return cache[resource_id]


def rewrite_markdown_images(
    markdown: str,
    *,
    document_path: Path,
    source_root: Path,
    resource_cache: dict[str, LocalResource],
) -> tuple[str, list[LocalResource], int, list[dict[str, str]]]:
    """Replace safely-resolved local image URLs with browser-only placeholders."""
    resources: dict[str, LocalResource] = {}
    warnings: list[dict[str, str]] = []
    reference_count = 0

    def replacement(target: str) -> str:
        nonlocal reference_count
        local_path, warning = _resolve_local_image(target, document_path, source_root)
        if warning:
            warnings.append({"source": target, "error": warning})
            return target
        if not local_path:
            return target
        if local_path.stat().st_size > MAX_IMAGE_BYTES:
            warnings.append({"source": target, "error": f"图片超过 {MAX_IMAGE_BYTES // 1024 // 1024} MB 限制，已保留原链接"})
            return target
        resource = _resource_for_path(local_path, source_root, resource_cache)
        resources[resource.resource_id] = resource
        reference_count += 1
        return f"wandao-resource://{resource.resource_id}"

    def inline_replacer(match: re.Match[str]) -> str:
        target = match.group("target")
        updated = replacement(target)
        return f"{match.group('prefix')}{updated}{match.group('suffix')}"

    def html_replacer(match: re.Match[str]) -> str:
        target = match.group("target")
        updated = replacement(target)
        return f"{match.group('prefix')}{match.group('quote')}{updated}{match.group('quote')}"

    markdown = _MD_INLINE_IMAGE.sub(inline_replacer, markdown)
    markdown = _HTML_IMAGE_SRC.sub(html_replacer, markdown)

    image_reference_labels = {
        _normalize_reference_id(match.group("label"))
        for match in _MD_IMAGE_REFERENCE.finditer(markdown)
        if _normalize_reference_id(match.group("label"))
    }

    def definition_replacer(match: re.Match[str]) -> str:
        label = _normalize_reference_id(match.group("label"))
        if label not in image_reference_labels:
            return match.group(0)
        updated = replacement(match.group("target"))
        return f"{match.group('prefix')}{updated}{match.group('suffix')}"

    markdown = _MD_REFERENCE_DEFINITION.sub(definition_replacer, markdown)
    return markdown, list(resources.values()), reference_count, warnings


def prepare_document(
    document: LocalDocument,
    source_root: Path,
    resource_cache: dict[str, LocalResource],
) -> PreparedDocument:
    if document.path.stat().st_size > MAX_DOCUMENT_BYTES:
        raise ExportError(f"文件超过 {MAX_DOCUMENT_BYTES // 1024 // 1024} MB 限制：{document.relative_path}")
    if document.suffix not in MARKDOWN_SUFFIXES:
        return PreparedDocument(document=document, data=document.path.read_bytes())
    markdown = _read_markdown(document.path)
    rewritten, resources, reference_count, warnings = rewrite_markdown_images(
        markdown,
        document_path=document.path,
        source_root=source_root,
        resource_cache=resource_cache,
    )
    return PreparedDocument(
        document=document,
        data=rewritten.encode("utf-8"),
        resources=resources,
        image_reference_count=reference_count,
        image_warnings=warnings,
    )


def build_import_plan(args: argparse.Namespace) -> ImportPlan:
    source, is_directory = _source_path(args)
    source_root = _source_root(args, source, is_directory)
    resource_cache: dict[str, LocalResource] = {}
    documents: list[LocalDocument] = []
    skipped: list[dict[str, str]] = []
    all_files: list[Path] = []
    for path in _walk_files(source, is_directory):
        all_files.append(path)
        document = _make_document(path, source_root)
        if document:
            documents.append(document)
        elif path.suffix.lower() not in IMAGE_SUFFIXES:
            skipped.append({"path": _relative_key(path.relative_to(source_root)), "reason": "钉钉在线文档导入暂不支持该文件类型"})

    documents.sort(key=lambda item: item.relative_path.as_posix().casefold())
    if getattr(args, "import_one", False) and not getattr(args, "source_file", ""):
        documents = documents[:1]
    max_import = max(0, int(getattr(args, "max_import", 0) or 0))
    if max_import:
        documents = documents[:max_import]
    if not documents:
        raise ExportError("没有找到可导入的文档。支持 Markdown、TXT、Word、Excel 和 XMind。")

    prepared: dict[str, PreparedDocument] = {}
    referenced_resource_paths: set[Path] = set()
    for document in documents:
        item = prepare_document(document, source_root, resource_cache)
        prepared[_relative_key(document.relative_path)] = item
        referenced_resource_paths.update(resource.path for resource in item.resources)

    for path in all_files:
        if path.suffix.lower() in IMAGE_SUFFIXES and path not in referenced_resource_paths:
            skipped.append({"path": _relative_key(path.relative_to(source_root)), "reason": "独立图片不会创建为钉钉在线文档；如需保留，请在 Markdown 中引用它"})

    folders: set[tuple[str, ...]] = set()
    if not getattr(args, "flatten_folders", False):
        for document in documents:
            for length in range(1, len(document.folders) + 1):
                folders.add(document.folders[:length])
    root_folder_name = safe_path_segment(source_root.name, "导入内容") if is_directory and not getattr(args, "no_create_root_folder", False) else ""
    return ImportPlan(
        source_root=source_root,
        root_folder_name=root_folder_name,
        documents=documents,
        prepared_documents=prepared,
        folders=sorted(folders, key=lambda item: (len(item), tuple(part.casefold() for part in item))),
        referenced_resources=resource_cache,
        skipped_files=sorted(skipped, key=lambda item: item["path"].casefold()),
    )


def parse_target_url(value: str) -> str:
    parsed = parse_dingtalk_url(value)
    if (parsed.hostname or "").lower() not in SUPPORTED_HOSTS:
        raise ExportError("请输入 docs.dingtalk.com 或 alidocs.dingtalk.com 的钉钉目录链接。")
    patterns = (
        r"/i/(?:desktop/)?folders/([A-Za-z0-9_-]{4,200})",
        r"/i/nodes/([A-Za-z0-9_-]{4,200})",
    )
    for pattern in patterns:
        match = re.search(pattern, parsed.path)
        if match:
            return urllib.parse.unquote(match.group(1))
    raise ExportError("链接中未找到钉钉目标目录标识。请复制“我的文档/团队文件”的目录链接。")


# Kept entirely in the authenticated DingTalk page.  Its return values are
# intentionally reduced to IDs and status fields so Python never receives
# cookies, A-Token, temporary OSS URLs, dentry keys, or document keys.
DINGTALK_IMPORT_HELPER_JS = rf"""
(() => {{
  if (window.__wandaoDingTalkImport && window.__wandaoDingTalkImport.version === {IMPORT_HELPER_VERSION}) return true;
  const base = location.origin;
  const pending = new Map();
  const resources = new Map();
  const withTimeout = async (promise, timeoutMs, label) => {{
    let timer = null;
    try {{
      return await Promise.race([
        promise,
        new Promise((_, reject) => {{ timer = setTimeout(() => reject(new Error(label + '超时')), timeoutMs); }})
      ]);
    }} finally {{ if (timer) clearTimeout(timer); }}
  }};
  const text = (value) => String(value == null ? '' : value);
  const apiError = (value, fallback) => {{
    const raw = text(value || fallback || '未知错误');
    return raw.replace(/https?:\/\/[^\s'\"]+/g, '[临时链接已隐藏]').slice(0, 500);
  }};
  const fetchJson = async (url, options = {{}}, timeoutMs = 45000) => {{
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {{
      const response = await fetch(url, Object.assign({{}}, options, {{signal: controller.signal}}));
      let payload = {{}};
      try {{ payload = await response.json(); }} catch (_) {{ /* The status below is still useful. */ }}
      return {{response, payload}};
    }} catch (error) {{
      if (error && error.name === 'AbortError') throw new Error('钉钉请求超时（' + Math.ceil(timeoutMs / 1000) + ' 秒）');
      throw error;
    }} finally {{ clearTimeout(timer); }}
  }};
  const authHeaders = async () => {{
    const {{payload}} = await fetchJson('/portal/api/v1/token/getAccessToken', {{method:'POST', credentials:'include'}}, 30000);
    const token = payload && payload.data && payload.data.accessToken;
    if (!token) throw new Error('钉钉登录已失效，请重新登录并保存会话。');
    const headers = {{'A-Token': token}};
    const match = document.cookie.match(/(?:^|;\s*)portal_corp_id=([^;]+)/);
    if (match && match[1]) {{
      headers['corp-id'] = decodeURIComponent(match[1]);
    }} else {{
      const {{payload:userPayload}} = await fetchJson('/api/users/getUserInfo', {{method:'POST', credentials:'include', headers}}, 30000);
      const orgs = userPayload && userPayload.data && (userPayload.data.orgs || userPayload.data.orgDTOList) || [];
      const main = Array.isArray(orgs) && (orgs.find((item) => item && item.isMainOrg) || orgs[0]) || {{}};
      const corpId = main.corpId || main.id || '';
      if (corpId) headers['corp-id'] = text(corpId);
    }}
    return headers;
  }};
  const requestJson = async (path, options = {{}}, timeoutMs = 45000) => {{
    const headers = Object.assign({{}}, await authHeaders(), options.headers || {{}});
    const {{response, payload}} = await fetchJson(base + path, Object.assign({{credentials:'include'}}, options, {{headers}}), timeoutMs);
    if (!response.ok || !payload || !payload.isSuccess) {{
      throw new Error(apiError(payload && (payload.message || payload.errorMessage || payload.errorCode), 'HTTP ' + response.status));
    }}
    return payload.data || {{}};
  }};
  const portal = async (route, args) => {{
    if (!window.lwpClient || typeof window.lwpClient.sendMsg !== 'function') {{
      throw new Error('钉钉上传组件尚未就绪，请打开任意钉钉文档后重试。');
    }}
    const response = await withTimeout(window.lwpClient.sendMsg(route, {{}}, args), 30000, '钉钉临时资源请求');
    const body = response && response.body && typeof response.body === 'object' ? response.body : {{}};
    if (!body.success) throw new Error(apiError(body.errorMessage || body.message, '钉钉临时资源请求失败'));
    return body.result || {{}};
  }};
  const decodeChunks = (chunks) => {{
    const pieces = [];
    let total = 0;
    for (const chunk of chunks) {{
      const binary = atob(chunk);
      const piece = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) piece[index] = binary.charCodeAt(index);
      pieces.push(piece);
      total += piece.length;
    }}
    const combined = new Uint8Array(total);
    let offset = 0;
    for (const piece of pieces) {{ combined.set(piece, offset); offset += piece.length; }}
    return combined;
  }};
  const toText = (bytes) => new TextDecoder('utf-8', {{fatal:false}}).decode(bytes);
  const replacePlaceholders = (bytes) => {{
    const missing = [];
    const updated = toText(bytes).replace(/wandao-resource:\/\/([A-Za-z0-9._-]+)/g, (_, resourceId) => {{
      const resource = resources.get(resourceId);
      if (!resource || !resource.accessUrl) {{ missing.push(resourceId); return _; }}
      return resource.accessUrl;
    }});
    if (missing.length) throw new Error('Markdown 图片资源尚未上传：' + missing.slice(0, 3).join(', '));
    return new TextEncoder().encode(updated);
  }};
  const begin = (resourceId, metadata) => {{
    resourceId = text(resourceId);
    if (!/^[A-Za-z0-9._-]{{6,160}}$/.test(resourceId)) throw new Error('资源标识无效');
    if (!metadata || !Number.isFinite(Number(metadata.size)) || Number(metadata.size) < 0) throw new Error('文件大小无效');
    pending.set(resourceId, {{
      name: text(metadata.name || '未命名'),
      suffix: text(metadata.suffix || '').replace(/^\./, '').toLowerCase(),
      contentType: text(metadata.contentType || 'application/octet-stream'),
      size: Number(metadata.size),
      chunks: []
    }});
    return {{resourceId}};
  }};
  const append = (resourceId, chunk) => {{
    const item = pending.get(text(resourceId));
    if (!item) throw new Error('上传资源不存在或已完成');
    const value = text(chunk);
    if (!/^[A-Za-z0-9+/]*={{0,2}}$/.test(value)) throw new Error('上传分块不是 Base64 数据');
    item.chunks.push(value);
    return {{resourceId:text(resourceId), chunks:item.chunks.length}};
  }};
  const upload = async (resourceId, replaceResources) => {{
    resourceId = text(resourceId);
    const item = pending.get(resourceId);
    if (!item) throw new Error('上传资源不存在或已完成');
    let bytes = decodeChunks(item.chunks);
    if (bytes.length !== item.size) throw new Error('上传文件大小不匹配');
    if (replaceResources) bytes = replacePlaceholders(bytes);
    const uploadInfo = await portal('/r/Adaptor/DingTalkDocPortalI/getTmpResUploadInfo', [
      item.suffix, item.contentType, false, item.name, 2, '', bytes.length
    ]);
    const resKey = text(uploadInfo.resKey);
    const uploadUrl = text(uploadInfo.url);
    if (!resKey || !uploadUrl) throw new Error('钉钉没有返回临时上传地址');
    const response = await withTimeout(fetch(uploadUrl, {{
      method:'PUT', headers:{{'Content-Type':item.contentType}}, body:bytes
    }}), 60000, '文件上传');
    if (!response.ok) throw new Error('钉钉临时文件上传失败：HTTP ' + response.status);
    const accessInfo = await portal('/r/Adaptor/DingTalkDocPortalI/getTmpResAccessInfo', [resKey]);
    const accessUrl = text(accessInfo.url || accessInfo.downloadUrl || accessInfo.accessUrl);
    if (!accessUrl) throw new Error('钉钉没有返回临时文件访问地址');
    resources.set(resourceId, {{resKey, accessUrl, size:bytes.length}});
    pending.delete(resourceId);
    return {{resourceId, size:bytes.length}};
  }};
  const getUuid = (value) => text(
    value && (value.dentryUuid || value.uuid || value.dentryId || value.id) || ''
  );
  const safeDentry = (value) => {{
    const entry = value && typeof value === 'object' ? value : {{}};
    return {{
      dentryUuid:getUuid(entry),
      name:text(entry.name || entry.title || ''),
      parentDentryUuid:text(entry.parentDentryUuid || entry.parentId || ''),
      isFolder:text(entry.dentryType || entry.contentType).toLowerCase() === 'folder'
    }};
  }};
  const helpers = {{
    version: {IMPORT_HELPER_VERSION},
    profile: async () => {{
      const result = await requestJson('/api/users/getUserInfo', {{method:'POST'}});
      const user = result.user || {{}};
      return {{loggedIn:true, displayName:text(user.nick || user.name || user.userName || '')}};
    }},
    targetInfo: async (dentryUuid) => {{
      const result = await requestJson('/box/api/v2/dentry/info?dentryUuid=' + encodeURIComponent(text(dentryUuid)), {{method:'GET'}});
      return safeDentry(result.dentry || result);
    }},
    createFolder: async (parentDentryUuid, name) => {{
      const result = await requestJson('/box/api/v2/dentry/createfolder', {{
        method:'POST', headers:{{'Content-Type':'application/json;charset=UTF-8'}},
        body:JSON.stringify({{parentDentryUuid:text(parentDentryUuid), name:text(name), conflictHandleStrategy:'return_existing_dentry'}})
      }});
      const entry = safeDentry(result.dentry || result);
      if (!entry.dentryUuid) throw new Error('钉钉创建目录后未返回目录标识');
      return entry;
    }},
    beginUpload: async (resourceId, metadata) => begin(resourceId, metadata),
    appendUpload: async (resourceId, chunk) => append(resourceId, chunk),
    finishUpload: async (resourceId, replaceResources) => upload(resourceId, Boolean(replaceResources)),
    importDocument: async (payload) => {{
      const item = resources.get(text(payload && payload.resourceId));
      if (!item || !item.accessUrl) throw new Error('待导入文档尚未上传');
      const body = {{
        name:text(payload.name),
        downloadUrl:item.accessUrl,
        fileSize:Number(payload.fileSize || item.size),
        batchUploadType:0,
        batchId:text(payload.batchId),
        batchParentDentryUuid:text(payload.parentDentryUuid),
        suffix:text(payload.suffix).replace(/^\./, '').toLowerCase(),
        parentDentryUuid:text(payload.parentDentryUuid),
        documentType:Number(payload.documentType)
      }};
      const result = await requestJson('/box/api/v2/import/document', {{
        method:'POST', headers:{{'Content-Type':'application/json;charset=UTF-8'}}, body:JSON.stringify(body)
      }}, 90000);
      const entry = safeDentry(result.dentry || result);
      const status = Number(result.status);
      if (Number.isFinite(status) && status !== 0) throw new Error('钉钉文档导入返回失败状态：' + status);
      if (!entry.dentryUuid) throw new Error('钉钉文档导入后未返回文档标识');
      return {{dentryUuid:entry.dentryUuid, name:entry.name, parentDentryUuid:entry.parentDentryUuid, status:0}};
    }},
    clear: async () => {{ pending.clear(); resources.clear(); return {{cleared:true}}; }}
  }};
  window.__wandaoDingTalkImport = helpers;
  return true;
}})()
"""


def install_import_helper(cdp: CDPClient) -> None:
    cdp.evaluate(DINGTALK_IMPORT_HELPER_JS, timeout=45)


def call_import_helper(cdp: CDPClient, method: str, *args: Any, timeout: int = 90) -> dict[str, Any]:
    install_import_helper(cdp)
    expression = f"window.__wandaoDingTalkImport[{_json(method)}](...{_json(list(args))})"
    result = cdp.evaluate(expression, timeout=timeout)
    if not isinstance(result, dict):
        raise ExportError("钉钉页面没有返回有效数据，请重新登录后重试。")
    return result


def save_import_auth_summary(args: argparse.Namespace, cdp: CDPClient) -> dict[str, Any]:
    profile = call_import_helper(cdp, "profile", timeout=45)
    auth_file = auth_path_from_args(args)
    # Reuse export's private summary writer through save_auth_summary so the
    # two providers intentionally share one browser profile/session record.
    summary = save_auth_summary(args, cdp)
    return {"authFile": str(auth_file), "displayName": str(profile.get("displayName") or summary.get("displayName") or "")}


def run_login(args: argparse.Namespace) -> dict[str, Any]:
    cdp, process = connect_dingtalk_browser(args, ENTRY_URL)
    try:
        cdp.navigate(ENTRY_URL)
        emit(args, "请在浏览器中完成钉钉登录；登录成功后回到万能导确认保存会话。")
        wait_seconds = max(0, int(getattr(args, "login_wait_seconds", 0) or 0))
        if wait_seconds:
            deadline = time.time() + wait_seconds
            last_error = ""
            while time.time() < deadline:
                try:
                    return finalize_report(
                        {"platform": "dingtalk", "loggedIn": True, **save_import_auth_summary(args, cdp)},
                        provider=PLUGIN_ID,
                        mode="login",
                    )
                except Exception as exc:  # noqa: BLE001 - user may still be logging in.
                    last_error = str(exc)
                    time.sleep(1)
            raise ExportError(last_error or "未检测到钉钉登录状态。")
        input()
        check_stopped(args)
        return finalize_report(
            {"platform": "dingtalk", "loggedIn": True, **save_import_auth_summary(args, cdp)},
            provider=PLUGIN_ID,
            mode="login",
        )
    finally:
        cdp.close()
        if process and args.close_started_chrome:
            process.terminate()


def resolve_target_folder(cdp: CDPClient, target_url: str) -> dict[str, str]:
    requested_uuid = parse_target_url(target_url)
    target = call_import_helper(cdp, "targetInfo", requested_uuid, timeout=60)
    target_uuid = str(target.get("dentryUuid") or "")
    if not target_uuid:
        raise ExportError("钉钉目标目录没有返回有效标识。")
    if bool(target.get("isFolder")):
        return {"dentryUuid": target_uuid, "name": str(target.get("name") or ""), "resolvedFrom": "folder"}
    parent_uuid = str(target.get("parentDentryUuid") or "")
    if not parent_uuid:
        raise ExportError("目标链接是文档，但钉钉没有返回其父目录。请复制目录链接后重试。")
    parent = call_import_helper(cdp, "targetInfo", parent_uuid, timeout=60)
    if not bool(parent.get("isFolder")):
        raise ExportError("目标链接未解析为可写入的钉钉目录。")
    return {"dentryUuid": str(parent.get("dentryUuid") or parent_uuid), "name": str(parent.get("name") or ""), "resolvedFrom": "document-parent"}


def probe_target(args: argparse.Namespace) -> dict[str, Any]:
    cdp, process = connect_dingtalk_browser(args, args.target_url or ENTRY_URL)
    try:
        target = resolve_target_folder(cdp, args.target_url)
        return finalize_report(
            {"platform": "dingtalk", "readOnly": True, "target": target, "loggedIn": True},
            provider=PLUGIN_ID,
            mode="probe",
        )
    finally:
        cdp.close()
        if process and args.close_started_chrome:
            process.terminate()


def plan_json(plan: ImportPlan, *, target: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "platform": "dingtalk",
        "readOnly": True,
        "sourceRoot": str(plan.source_root),
        "rootFolderName": plan.root_folder_name,
        "target": target or {},
        "totalDocs": len(plan.documents),
        "folderCount": len(plan.folders) + (1 if plan.root_folder_name else 0),
        "imageReferenceCount": sum(item.image_reference_count for item in plan.prepared_documents.values()),
        "localImageCount": len(plan.referenced_resources),
        "documents": [
            {
                "path": _relative_key(document.relative_path),
                "title": document.title,
                "suffix": document.suffix,
                "documentType": document.document_type,
                "folders": list(document.folders),
                "localImageCount": len(plan.prepared_documents[_relative_key(document.relative_path)].resources),
                "imageWarnings": plan.prepared_documents[_relative_key(document.relative_path)].image_warnings,
            }
            for document in plan.documents
        ],
        "skippedFiles": plan.skipped_files,
    }


def generate_plan(args: argparse.Namespace) -> dict[str, Any]:
    plan = build_import_plan(args)
    target: dict[str, str] | None = None
    cdp = process = None
    try:
        if getattr(args, "target_url", ""):
            cdp, process = connect_dingtalk_browser(args, args.target_url)
            target = resolve_target_folder(cdp, args.target_url)
        result = plan_json(plan, target=target)
        return finalize_report(result, provider=PLUGIN_ID, mode="plan")
    finally:
        if cdp:
            cdp.close()
        if process and args.close_started_chrome:
            process.terminate()


def _folder_key(parts: tuple[str, ...]) -> str:
    return "/".join(parts) if parts else "."


def _doc_item_key(document: LocalDocument) -> str:
    digest = hashlib.sha256(_relative_key(document.relative_path).encode("utf-8")).hexdigest()[:24]
    return f"dingtalk:import:{digest}"


def _upload_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:36]
    return f"{prefix}-{digest}"


def _stream_to_browser(
    cdp: CDPClient,
    *,
    resource_id: str,
    data: bytes,
    name: str,
    suffix: str,
    content_type: str,
    replace_resources: bool = False,
) -> dict[str, Any]:
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ExportError(f"文件超过 {MAX_DOCUMENT_BYTES // 1024 // 1024} MB 限制：{name}")
    call_import_helper(
        cdp,
        "beginUpload",
        resource_id,
        {"name": name, "suffix": suffix, "contentType": content_type, "size": len(data)},
        timeout=60,
    )
    for offset in range(0, len(data), CDP_CHUNK_BYTES):
        encoded = base64.b64encode(data[offset:offset + CDP_CHUNK_BYTES]).decode("ascii")
        call_import_helper(cdp, "appendUpload", resource_id, encoded, timeout=60)
    return call_import_helper(cdp, "finishUpload", resource_id, bool(replace_resources), timeout=150)


def _folder_map_from_checkpoint(checkpoint: Any) -> dict[str, str]:
    if not checkpoint:
        return {}
    raw = checkpoint.load_cursor("folder-map", {})
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in raw.items()
        if isinstance(key, str) and isinstance(value, str) and value
    }


def _save_folder_map(checkpoint: Any, folder_map: dict[str, str]) -> None:
    if checkpoint:
        checkpoint.save_cursor("folder-map", folder_map)


def _ensure_folder(
    cdp: CDPClient,
    *,
    folder_map: dict[str, str],
    checkpoint: Any,
    parts: tuple[str, ...],
    parent_uuid: str,
) -> str:
    key = _folder_key(parts)
    existing = folder_map.get(key)
    if existing:
        return existing
    if not parts:
        return parent_uuid
    parent_key = _folder_key(parts[:-1])
    actual_parent = folder_map.get(parent_key, parent_uuid)
    created = call_import_helper(cdp, "createFolder", actual_parent, parts[-1], timeout=90)
    folder_uuid = str(created.get("dentryUuid") or "")
    if not folder_uuid:
        raise ExportError(f"钉钉创建目录失败：{'/'.join(parts)}")
    folder_map[key] = folder_uuid
    _save_folder_map(checkpoint, folder_map)
    return folder_uuid


def _required_folder_parts(plan: ImportPlan, document: LocalDocument) -> tuple[str, ...]:
    if plan.root_folder_name:
        return (plan.root_folder_name, *document.folders)
    return document.folders


def import_documents(args: argparse.Namespace) -> dict[str, Any]:
    if not getattr(args, "yes", False):
        raise ExportError("导入会在钉钉创建目录和文档；请确认后重试。")
    plan = build_import_plan(args)
    started = time.time()
    checkpoint = open_checkpoint_from_args(args, PLUGIN_ID, "import")
    cdp, process = connect_dingtalk_browser(args, args.target_url)
    failures: list[dict[str, str]] = []
    imported = skipped = image_uploads = folders_created = 0
    image_warnings: list[dict[str, str]] = []
    resource_uploaded: set[str] = set()
    try:
        target = resolve_target_folder(cdp, args.target_url)
        target_uuid = target["dentryUuid"]
        if checkpoint:
            checkpoint.start_task({
                "source": str(plan.source_root),
                "target": target_uuid,
                "totalDocs": len(plan.documents),
                "rootFolderName": plan.root_folder_name,
                "flattenFolders": bool(args.flatten_folders),
            })
            for document in plan.documents:
                checkpoint.upsert_item(
                    _doc_item_key(document),
                    title=document.title,
                    source_id=_relative_key(document.relative_path),
                    parent_key=_folder_key(_required_folder_parts(plan, document)),
                    metadata={"relativePath": _relative_key(document.relative_path), "suffix": document.suffix},
                )
        folder_map = _folder_map_from_checkpoint(checkpoint)
        # A fresh session must not inherit page-memory resource URLs from a
        # previous run.  The underlying OSS resources are intentionally short
        # lived, so local images are re-uploaded on resume when needed.
        call_import_helper(cdp, "clear", timeout=30)
        if plan.root_folder_name:
            before = len(folder_map)
            _ensure_folder(
                cdp,
                folder_map=folder_map,
                checkpoint=checkpoint,
                parts=(plan.root_folder_name,),
                parent_uuid=target_uuid,
            )
            folders_created += int(len(folder_map) > before)
        if not args.flatten_folders:
            for folder in plan.folders:
                parts = (plan.root_folder_name, *folder) if plan.root_folder_name else folder
                before = len(folder_map)
                _ensure_folder(cdp, folder_map=folder_map, checkpoint=checkpoint, parts=parts, parent_uuid=target_uuid)
                folders_created += int(len(folder_map) > before)

        selected_documents = list(plan.documents)
        if checkpoint and args.retry_failed:
            selected_documents = [doc for doc in selected_documents if checkpoint.item_status(_doc_item_key(doc)) == "failed"]
        elif checkpoint and args.resume:
            selected_documents = [doc for doc in selected_documents if checkpoint.item_status(_doc_item_key(doc)) != "completed"]

        skipped = len(plan.documents) - len(selected_documents)

        total = len(selected_documents)
        emit(args, f"开始导入钉钉文档：共 {total} 篇。", event="task.started", totals={"documents": total})
        for index, document in enumerate(selected_documents, start=1):
            check_stopped(args)
            item_key = _doc_item_key(document)
            prepared = plan.prepared_documents[_relative_key(document.relative_path)]
            try:
                if checkpoint:
                    checkpoint.start_item(item_key, "preparing")
                if prepared.image_warnings:
                    for warning in prepared.image_warnings:
                        image_warnings.append({"path": _relative_key(document.relative_path), **warning})
                    if args.require_images:
                        raise ExportError(f"本地图片处理失败：{prepared.image_warnings[0]['error']}")
                target_parts = _required_folder_parts(plan, document)
                parent_uuid = folder_map.get(_folder_key(target_parts), target_uuid)
                if target_parts and not parent_uuid:
                    parent_uuid = _ensure_folder(cdp, folder_map=folder_map, checkpoint=checkpoint, parts=target_parts, parent_uuid=target_uuid)
                emit(args, f"开始导入钉钉文档：{document.title}", event="document.import.started", doc={"title": document.title, "index": index, "path": _relative_key(document.relative_path)})
                for resource in prepared.resources:
                    check_stopped(args)
                    if resource.resource_id in resource_uploaded:
                        continue
                    if resource.size > MAX_IMAGE_BYTES:
                        raise ExportError(f"图片超过 {MAX_IMAGE_BYTES // 1024 // 1024} MB 限制：{resource.relative_path}")
                    _stream_to_browser(
                        cdp,
                        resource_id=resource.resource_id,
                        data=resource.path.read_bytes(),
                        name=resource.path.name,
                        suffix=resource.path.suffix.lower().lstrip("."),
                        content_type=resource.content_type,
                    )
                    resource_uploaded.add(resource.resource_id)
                    image_uploads += 1
                document_resource_id = _upload_id("document", _relative_key(document.relative_path) + str(time.time_ns()))
                uploaded_document = _stream_to_browser(
                    cdp,
                    resource_id=document_resource_id,
                    data=prepared.data,
                    name=document.path.name,
                    suffix=document.suffix,
                    content_type=_file_content_type(document.path, document.suffix),
                    replace_resources=document.suffix in MARKDOWN_SUFFIXES,
                )
                throttle_request(args)
                imported_entry = call_import_helper(
                    cdp,
                    "importDocument",
                    {
                        "resourceId": document_resource_id,
                        "name": document.title,
                        "fileSize": int(uploaded_document.get("size") or len(prepared.data)),
                        "batchId": f"wandao-{int(time.time() * 1000)}-{index}",
                        "parentDentryUuid": parent_uuid,
                        "suffix": document.suffix,
                        "documentType": document.document_type,
                    },
                    timeout=180,
                )
                target_id = str(imported_entry.get("dentryUuid") or "")
                if checkpoint:
                    checkpoint.complete_item(
                        item_key,
                        target_id=target_id,
                        metadata={
                            "relativePath": _relative_key(document.relative_path),
                            "dentryUuid": target_id,
                            "localImageCount": len(prepared.resources),
                            "imageWarnings": prepared.image_warnings,
                        },
                    )
                imported += 1
                emit(args, f"钉钉文档导入完成：{document.title}", event="document.import.completed", doc={"id": target_id, "title": document.title, "index": index, "path": _relative_key(document.relative_path)}, stats={"imageUploadsInDoc": len(prepared.resources), "imageWarningsInDoc": len(prepared.image_warnings)})
            except ExportStopped:
                if checkpoint:
                    checkpoint.fail_item(item_key, "stopped")
                raise
            except Exception as exc:  # noqa: BLE001 - keep processing other documents for a complete report.
                message = _safe_error(exc)
                if checkpoint:
                    checkpoint.fail_item(item_key, message)
                failures.append({"path": _relative_key(document.relative_path), "title": document.title, "error": message})
                emit(args, f"钉钉文档导入失败：{document.title}：{message}", event="document.import.failed", level="error", doc={"title": document.title, "index": index, "path": _relative_key(document.relative_path)}, error={"type": type(exc).__name__, "message": message})
            if index % max(1, args.progress_every) == 0 or index == total:
                emit(args, f"progress {index}/{total} imported={imported} failures={len(failures)} image_uploads={image_uploads}", event="task.progress", progress={"current": index, "total": total}, stats={"importedDocs": imported, "failureCount": len(failures), "imageUploads": image_uploads})

        report_dir = default_data_dir() / "reports" / "dingtalk-import"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_name = safe_path_segment(str(getattr(args, "checkpoint_task_id", "") or "") or f"import-{int(started)}", "import")
        report_file = report_dir / f"{report_name}.json"
        report = finalize_report(
            {
                "platform": "dingtalk",
                "target": target,
                "sourceRoot": str(plan.source_root),
                "rootFolderName": plan.root_folder_name,
                "totalDocs": total,
                "importedDocs": imported,
                "skippedDocs": skipped,
                "folderCount": len(folder_map),
                "foldersCreated": folders_created,
                "imageUploads": image_uploads,
                "imageWarnings": image_warnings,
                "skippedFiles": plan.skipped_files,
                "failures": failures,
                "elapsedSeconds": round(time.time() - started, 2),
                "checkpoint": checkpoint.stats() if checkpoint else {},
            },
            provider=PLUGIN_ID,
            mode="import",
            report_file=report_file,
            output=plan.source_root,
        )
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if checkpoint:
            if failures:
                checkpoint.fail_task(f"{len(failures)} 个文档导入失败", status="failed")
            else:
                checkpoint.complete_task(report)
        emit(args, "钉钉文档导入完成" if not failures else f"钉钉文档导入完成，但有 {len(failures)} 个失败项", event="task.completed", level="success" if not failures else "warn", reportFile=str(report_file), stats={"importedDocs": imported, "failureCount": len(failures), "imageUploads": image_uploads})
        return report
    finally:
        try:
            call_import_helper(cdp, "clear", timeout=20)
        except Exception:
            pass
        cdp.close()
        if process and args.close_started_chrome:
            process.terminate()
        if checkpoint:
            checkpoint.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导入本地文档到钉钉目录，保留目录和 Markdown 本地图片")
    parser.add_argument("--gui", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--login", action="store_true", help="打开钉钉网页并保存登录会话")
    parser.add_argument("--login-wait-seconds", type=int, default=0, help="非交互登录时等待会话的秒数")
    parser.add_argument("--probe", action="store_true", help="只检测目标钉钉目录")
    parser.add_argument("--plan", action="store_true", help="只扫描本地文件并验证目标目录，不创建内容")
    parser.add_argument("--import-one", action="store_true", help="仅导入一篇测试文档")
    parser.add_argument("--import-all", action="store_true", help="导入全部支持的本地文档")
    parser.add_argument("--target-url", default="", help="钉钉目标目录链接")
    parser.add_argument("--source-dir", default="", help="本地待导入目录")
    parser.add_argument("--source-file", default="", help="可选：单篇测试文件")
    parser.add_argument("--max-import", type=int, default=0, help="最多导入篇数，0 表示全部")
    parser.add_argument("--flatten-folders", action="store_true", help="平铺导入，不保留本地子目录")
    parser.add_argument("--no-create-root-folder", action="store_true", help="不在目标目录下新建同名根目录")
    parser.add_argument("--require-images", action="store_true", help="本地图片处理失败时令对应 Markdown 导入失败")
    parser.add_argument("--yes", action="store_true", help="确认创建钉钉目录和文档")
    add_checkpoint_args(parser)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Chrome 调试端口")
    parser.add_argument("--profile-dir", default=str(default_profile_path()), help="钉钉专用浏览器配置目录")
    parser.add_argument("--browser-path", default="", help="Chrome/Edge 可执行文件路径")
    parser.add_argument("--auth-file", default=str(default_auth_path()), help="登录会话摘要文件，不含 Cookie/Token")
    parser.add_argument("--progress-every", type=int, default=1, help="每处理多少篇输出一次进度")
    parser.add_argument("--request-delay", type=float, default=DEFAULT_REQUEST_DELAY, help="请求延迟秒")
    parser.add_argument("--request-jitter", type=float, default=DEFAULT_REQUEST_JITTER, help="请求随机浮动秒")
    parser.add_argument("--close-started-chrome", action="store_true", help="任务结束后关闭本插件启动的浏览器")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.login:
            print(json.dumps(run_login(args), ensure_ascii=False, indent=2))
            return 0
        if args.probe:
            if not args.target_url:
                raise ExportError("检测前请填写钉钉目标目录链接。")
            print(json.dumps(probe_target(args), ensure_ascii=False, indent=2))
            return 0
        if not (args.plan or args.import_one or args.import_all):
            args.plan = True
        if not args.target_url:
            raise ExportError("请填写钉钉目标目录链接。")
        result = generate_plan(args) if args.plan else import_documents(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not result.get("failures") else 1
    except ExportStopped as exc:
        emit(args, f"钉钉文档导入已停止：{exc}", event="task.stopped", level="warn")
        print(str(exc), file=sys.stderr, flush=True)
        return 130
    except ExportError as exc:
        message = _safe_error(exc)
        emit(args, f"钉钉文档导入失败：{message}", event="task.failed", level="error", error={"type": type(exc).__name__, "message": message})
        print(message, file=sys.stderr, flush=True)
        return 1
    except Exception as exc:  # noqa: BLE001 - final task boundary.
        message = _safe_error(exc)
        emit(args, f"钉钉文档导入失败：{message}", event="task.failed", level="error", error={"type": type(exc).__name__, "message": message})
        print(f"钉钉文档导入失败：{message}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
