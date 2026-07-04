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
import hashlib
import html
import json
import mimetypes
import os
import queue
import re
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

from export_aliyun_thoughts import (
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


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR
DEFAULT_SOURCE_DIR = default_data_dir() / "exports" / "yuque"
DEFAULT_CONFIG_FILE = default_state_path(".yuque_import_config.json")
YUQUE_HOME_URL = "https://www.yuque.com"
MARKDOWN_LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(([^)\n]+)\)")
EXPORT_FOOTER_RE = re.compile(r"\n---\n\n来源:[\s\S]*$", re.M)


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
    config_file.parent.mkdir(parents=True, exist_ok=True)
    safe_data = {key: value for key, value in data.items() if value not in (None, "")}
    config_file.write_text(json.dumps(safe_data, ensure_ascii=False, indent=2), encoding="utf-8")


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
    timeout: int = 60,
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
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if exc.code == 401:
            raise ExportError(f"语雀登录已失效，请重新点击“登录并保存凭证”后重试。HTTP 401：{raw[:800]}") from exc
        raise ExportError(f"语雀接口 HTTP {exc.code}：{raw[:1200]}") from exc
    except urllib.error.URLError as exc:
        raise ExportError(f"请求语雀接口失败：{exc}") from exc
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise ExportError(f"语雀接口返回非 JSON：{raw[:500]}") from exc
    if isinstance(data, dict) and data.get("status") and int(data.get("status") or 0) >= 400:
        raise ExportError(f"语雀接口返回错误：{data.get('message') or data}")
    return data


def request_text(host: str, cookies: list[dict[str, Any]], path_or_url: str, *, timeout: int = 60) -> str:
    url = path_or_url if path_or_url.startswith("http") else f"https://{host}{path_or_url}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Cookie": cookies_for_url(cookies, url),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if exc.code == 401:
            raise ExportError(f"语雀登录已失效，请重新点击“登录并保存凭证”后重试。HTTP 401：{raw[:800]}") from exc
        raise ExportError(f"读取语雀页面 HTTP {exc.code}：{raw[:800]}") from exc
    except urllib.error.URLError as exc:
        raise ExportError(f"读取语雀页面失败：{exc}") from exc


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
    target = target.strip().strip("<>")
    parsed = urllib.parse.urlparse(target)
    return bool(parsed.scheme in ("http", "https", "data", "mailto") or target.startswith("#"))


def resolve_local_target(md_path: Path, target: str) -> Path | None:
    target = target.strip().strip("<>")
    if not target or is_remote_or_anchor(target):
        return None
    parsed = urllib.parse.urlparse(target)
    if parsed.scheme:
        return None
    local_path = (md_path.parent / urllib.parse.unquote(parsed.path)).resolve()
    return local_path if local_path.exists() and local_path.is_file() else None


def is_image_path(path: Path) -> bool:
    mime, _ = mimetypes.guess_type(path.name)
    return bool(mime and mime.startswith("image/"))


def scan_local_resources(markdown: str, md_path: Path) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for is_image, text, target in MARKDOWN_LINK_RE.findall(markdown):
        local_path = resolve_local_target(md_path, target)
        if not local_path:
            continue
        key = str(local_path)
        if key in seen:
            continue
        kind = "image" if is_image or is_image_path(local_path) else "attachment"
        resources.append({"path": local_path, "target": target, "kind": kind, "title": text or local_path.name})
        seen.add(key)
    return resources


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
                "resources": scan_local_resources(body, path),
            }
        )
    if not docs:
        raise ExportError(f"没有在目录中找到 Markdown 文件：{source_dir}")
    return docs


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
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if exc.code == 401:
            raise ExportError(f"语雀登录已失效，请重新点击“登录并保存凭证”后重试。HTTP 401：{raw[:800]}") from exc
        raise ExportError(f"上传资源失败 HTTP {exc.code}：{raw[:1000]}") from exc
    data = json.loads(raw or "{}")
    item = data.get("data") or {}
    if not item.get("url"):
        raise ExportError(f"上传资源返回异常：{raw[:1000]}")
    return item


def replace_resource_links(
    markdown: str,
    resources: list[dict[str, Any]],
    uploads: dict[str, dict[str, Any]],
) -> str:
    by_target: dict[str, str] = {}
    for resource in resources:
        uploaded = uploads.get(str(resource["path"]))
        if uploaded and uploaded.get("url"):
            by_target[str(resource["target"])] = str(uploaded["url"])

    def repl(match: re.Match[str]) -> str:
        marker, label, target = match.groups()
        replacement = by_target.get(target.strip().strip("<>"))
        if not replacement:
            return match.group(0)
        return f"{marker}[{label}]({replacement})"

    return MARKDOWN_LINK_RE.sub(repl, markdown)


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
        raise ExportError(f"创建语雀文档失败：{title}")
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
        raise ExportError(f"更新语雀文档失败：{title}")
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
    if existing_doc:
        if skip_existing or not update_existing:
            return {"action": "skipped", "id": existing_doc.get("id"), "slug": doc["slug"], "title": doc["title"]}, toc
        doc_id = int(existing_doc["id"])
        action = "updated"
    else:
        created = create_doc(host, cookies, book_id, doc["title"], doc["slug"], placeholder, book_url)
        doc_id = int(created["id"])
        existing[doc["slug"]] = created

    uploads: dict[str, dict[str, Any]] = {}
    image_count = attachment_count = 0
    for resource in doc["resources"]:
        uploaded = upload_resource(host, cookies, Path(resource["path"]), doc_id, book_url)
        uploads[str(resource["path"])] = uploaded
        if resource.get("kind") == "image":
            image_count += 1
        else:
            attachment_count += 1

    body = replace_resource_links(doc["body"], doc["resources"], uploads)
    updated = update_doc(host, cookies, doc_id, book_id, doc["title"], doc["slug"], body, book_url)
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


def import_docs(args: argparse.Namespace) -> dict[str, Any]:
    config = config_args(args)
    target_url = config.get("targetBookUrl") or getattr(args, "target_book_url", None)
    host, namespace, book_url = parse_target_book(target_url)
    source_dir = Path(config.get("sourceDir") or getattr(args, "source_dir", None) or DEFAULT_SOURCE_DIR).resolve()
    cookies = load_auth_cookies_or_fail(args, host)
    docs = scan_markdown_docs(source_dir)
    if getattr(args, "doc_path", None):
        wanted = Path(args.doc_path).resolve()
        docs = [doc for doc in docs if Path(doc["path"]).resolve() == wanted]
        if not docs:
            raise ExportError(f"单篇测试文件不在 Markdown 目录内：{wanted}")
    if getattr(args, "max_import", 0) and args.max_import > 0:
        docs = docs[: args.max_import]

    book_data = load_target_book(host, cookies, book_url)
    book = book_data["book"]
    book_id = int(book["id"])
    toc = refresh_catalog(host, cookies, book_id, book_url)
    existing = existing_docs_by_slug(host, cookies, book_id, book_url)
    image_count = sum(1 for doc in docs for res in doc["resources"] if res.get("kind") == "image")
    attachment_count = sum(1 for doc in docs for res in doc["resources"] if res.get("kind") == "attachment")
    folders = sorted({"/".join(doc["folders"][:i]) for doc in docs for i in range(1, len(doc["folders"]) + 1)})

    if args.plan or args.dry_run:
        return {
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
            "sampleDocs": [{k: doc[k] for k in ("relativePath", "title", "slug", "level", "size")} for doc in docs[:10]],
            "capabilities": ["create_update_markdown", "preserve_directory_tree", "upload_local_images", "upload_local_attachments"],
        }

    if not args.yes:
        raise ExportError("写入语雀需要显式确认，请追加 --yes。")

    created = updated = skipped = 0
    uploaded_images = uploaded_attachments = 0
    failures: list[dict[str, str]] = []
    imported_docs: list[dict[str, Any]] = []
    stopped = False

    for index, doc in enumerate(docs, start=1):
        if stop_requested(args):
            stopped = True
            emit(args, "收到停止请求，正在结束。")
            break
        check_stopped(args)
        try:
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
            if result["action"] == "created":
                created += 1
            elif result["action"] == "updated":
                updated += 1
            else:
                skipped += 1
            uploaded_images += int(result.get("imageUploads") or 0)
            uploaded_attachments += int(result.get("attachmentUploads") or 0)
        except ExportStopped:
            stopped = True
            emit(args, "收到停止请求，当前文档未完成，正在结束。")
            break
        except Exception as exc:
            failures.append({"relativePath": doc["relativePath"], "error": str(exc)})
        emit(
            args,
            f"progress {index}/{len(docs)} created={created} updated={updated} skipped={skipped} "
            f"images={uploaded_images} attachments={uploaded_attachments} failures={len(failures)}",
        )

    report = {
        "provider": "yuque-import",
        "host": host,
        "namespace": namespace,
        "book": book,
        "sourceDir": str(source_dir),
        "totalDocs": len(docs),
        "createdDocs": created,
        "updatedDocs": updated,
        "skippedDocs": skipped,
        "failureCount": len(failures),
        "folderCount": len(folders),
        "imageUploads": uploaded_images,
        "attachmentUploads": uploaded_attachments,
        "stopped": stopped,
        "failures": failures,
        "importedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    (source_dir / "00-语雀导入报告.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
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
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
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
        "imageUploads",
        "attachmentUploads",
        "stopped",
        "cookieCount",
        "authFile",
    )
    print(json.dumps({key: report[key] for key in keys if key in report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
