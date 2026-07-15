#!/usr/bin/env python3
# Author: tllovesxs
"""
Import local Markdown documents into FlowUs (息流).

The importer uses the FlowUs web API to create pages and import content.
It shares authentication with the FlowUs exporter.

Usage:
  python import_flowus.py --login --auth-file .flowus_auth.json
  python import_flowus.py --scan-targets --auth-file .flowus_auth.json
  python import_flowus.py --source-dir ./exports/flowus --space-id xxx --auth-file .flowus_auth.json
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import sys
import time
import uuid
import hashlib
import hmac
from pathlib import Path
from typing import Any

from wandao_core.checkpoint import add_checkpoint_args, open_checkpoint_from_args
from wandao_core.logging import emit_legacy
from wandao_core.report import finalize_report

from export_xiliu import (
    DEFAULT_PORT,
    FlowUsClient,
    FlowUsError,
    _friendly_network_error,
    auth_path_from_args,
    default_auth_path,
    default_profile_path,
    login_and_save_auth,
    parse_flowus_url,
)


PROJECT_DIR = Path(__file__).resolve().parent
FLOWUS_API_BASE = "https://flowus.cn/api"

# API endpoints
USERS_ME_API = f"{FLOWUS_API_BASE}/users/me"
USER_ROOT_API = f"{FLOWUS_API_BASE}/users/{{user_id}}/root"
SPACE_ROOT_API = f"{FLOWUS_API_BASE}/spaces/{{space_id}}/root"
BLOCKS_TRANSACTIONS_API = f"{FLOWUS_API_BASE}/blocks/transactions"
IMPORT_TEMP_FILE_API = f"{FLOWUS_API_BASE}/import_temp_file"
ENQUEUE_TASK_API = f"{FLOWUS_API_BASE}/enqueueTask"
GET_TASKS_API = f"{FLOWUS_API_BASE}/getTasks"
UPLOAD_INFO_API = f"{FLOWUS_API_BASE}/upload/getTcFileUploadInfo"
CREATE_URLS_API = f"{FLOWUS_API_BASE}/file/create_urls"
SEARCH_RESOURCE_API = f"{FLOWUS_API_BASE}/search/resource"

# Tencent COS upload config (from FlowUs frontend VITE_BUCKET)
FLOWUS_COS_BUCKET = "flowus-1316188996"
COS_REGION = "beijing"

# Markdown image patterns
MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^\n)]+)\)")
MARKDOWN_DOC_EXTENSIONS = {".md", ".markdown", ".mdown"}


def emit(message: str, *, event: str = "log.message", level: str = "info", **fields: Any) -> None:
    emit_legacy("flowus-import", message, event=event, level=level, **fields)


def generate_uuid() -> str:
    return str(uuid.uuid4())


def _api_error_msg(result: dict[str, Any]) -> str:
    """Extract a sanitized error message from an API response.

    Only returns the msg field and status code, never the full response
    which may contain signed URLs, tokens, or other sensitive data.
    """
    msg = result.get("msg", "")
    code = result.get("code", "?")
    if msg:
        return f"{msg} (code={code})"
    return f"code={code}"


# ---------------------------------------------------------------------------
# FlowUs API helpers
# ---------------------------------------------------------------------------

def fetch_user_info(client: FlowUsClient) -> dict[str, Any]:
    """Get current user info."""
    data = client.get_json(USERS_ME_API)
    if data.get("code") != 200:
        raise FlowUsError(f"获取用户信息失败：{data.get('msg', data)}")
    return data.get("data", {})


def fetch_user_spaces(client: FlowUsClient, user_id: str) -> dict[str, Any]:
    """Get user's spaces and space views."""
    url = USER_ROOT_API.format(user_id=user_id)
    data = client.get_json(url)
    if data.get("code") != 200:
        raise FlowUsError(f"获取空间列表失败：{data.get('msg', data)}")
    return data.get("data", {})


def fetch_space_root(client: FlowUsClient, space_id: str) -> dict[str, Any]:
    """Get space root blocks."""
    url = SPACE_ROOT_API.format(space_id=space_id)
    data = client.get_json(url)
    if data.get("code") != 200:
        raise FlowUsError(f"获取空间根目录失败：{data.get('msg', data)}")
    return data.get("data", {})


def list_import_targets(client: FlowUsClient) -> list[dict[str, Any]]:
    """List available spaces/pages that can be imported to.

    Returns list of { id, name, spaceId, role } for pages where user has write access.
    """
    user_info = fetch_user_info(client)
    user_id = user_info.get("uuid", "")
    if not user_id:
        raise FlowUsError("无法获取用户 ID")

    spaces_data = fetch_user_spaces(client, user_id)
    space_views = spaces_data.get("spaceViews", {})
    spaces = spaces_data.get("spaces", {})
    permission_groups = spaces_data.get("permissionGroups", [])

    if not space_views or not spaces:
        return []

    targets: list[dict[str, Any]] = []

    # Get each space's root
    for view_id, view in space_views.items():
        space_id = view.get("spaceId", "")
        space = spaces.get(space_id)
        if not space:
            continue

        space_title = space.get("title", "未命名空间")
        sub_nodes = space.get("subNodes", [])

        try:
            root_data = fetch_space_root(client, space_id)
        except FlowUsError:
            continue

        blocks = root_data.get("blocks", {})

        # Filter pages where user has editor/writer role
        for node_id in sub_nodes:
            block = blocks.get(node_id)
            if not block:
                continue
            if block.get("type") != 0:  # Only pages
                continue

            # Check permissions
            permissions = block.get("permissions", [])
            if not permissions:
                continue

            # Check for illegal/restricted
            has_illegal = any(p.get("type") == "illegal" for p in permissions)
            if has_illegal:
                continue

            # Find role
            role = _get_block_role(permissions, user_id, permission_groups)
            if role in ("editor", "writer"):
                title_data = block.get("data", {}).get("segments", [])
                title = title_data[0].get("text", "") if title_data else block.get("title", "未命名页面")
                targets.append({
                    "id": node_id,
                    "name": title or "未命名页面",
                    "spaceId": space_id,
                    "spaceName": space_title,
                    "role": role,
                })

    return targets


def _get_block_role(permissions: list[dict], user_id: str, permission_groups: list) -> str:
    """Get user's role for a block from its permissions."""
    role_weight = {"reader": 1, "commenter": 2, "viewer": 3, "writer": 4, "editor": 5}

    best_role = "none"
    best_weight = 0

    for perm in permissions:
        ptype = perm.get("type", "")
        role = perm.get("role", "none")

        if ptype == "user" and perm.get("userId") == user_id:
            w = role_weight.get(role, 0)
            if w > best_weight:
                best_weight = w
                best_role = role
        elif ptype == "group":
            group_id = perm.get("groupId", "")
            group = next((g for g in permission_groups if g.get("id") == group_id), None)
            if group and user_id in (group.get("userIds") or []):
                w = role_weight.get(role, 0)
                if w > best_weight:
                    best_weight = w
                    best_role = role
        elif ptype == "space":
            w = role_weight.get(role, 0)
            if w > best_weight:
                best_weight = w
                best_role = role

    return best_role


def create_empty_page(
    client: FlowUsClient,
    space_id: str,
    parent_id: str,
    title: str,
    after: str | None = None,
) -> str:
    """Create an empty page block under parent. Returns new page ID."""
    page_id = generate_uuid()

    operations = {
        "requestId": generate_uuid(),
        "transactions": [
            {
                "id": generate_uuid(),
                "spaceId": space_id,
                "operations": [
                    {
                        "id": page_id,
                        "path": [],
                        "command": "set",
                        "table": "block",
                        "args": {
                            "uuid": page_id,
                            "spaceId": space_id,
                            "parentId": parent_id,
                            "textColor": "",
                            "backgroundColor": "",
                            "type": 0,
                            "status": 1,
                            "permissions": [],
                            "data": {
                                "segments": [{"type": 0, "text": title, "enhancer": {}}],
                            },
                        },
                    },
                    {
                        "id": parent_id,
                        "command": "listAfter",
                        "path": ["subNodes"],
                        "table": "block",
                        "args": {
                            "uuid": page_id,
                            **({"after": after} if after else {}),
                        },
                    },
                ],
            }
        ],
    }

    data = client.request("POST", BLOCKS_TRANSACTIONS_API, data=operations, timeout=30)
    result = json.loads(data.decode("utf-8", errors="replace"))
    if result.get("code") != 200:
        raise FlowUsError(f"创建页面失败：{_api_error_msg(result)}")

    return page_id


def update_page_title(client: FlowUsClient, space_id: str, page_id: str, title: str) -> None:
    """Update a page's title."""
    operations = {
        "requestId": generate_uuid(),
        "transactions": [
            {
                "id": generate_uuid(),
                "spaceId": space_id,
                "operations": [
                    {
                        "id": page_id,
                        "path": ["data"],
                        "command": "update",
                        "table": "block",
                        "args": {
                            "segments": [{"type": 0, "text": title, "enhancer": {}}],
                        },
                    },
                ],
            }
        ],
    }

    data = client.request("POST", BLOCKS_TRANSACTIONS_API, data=operations, timeout=30)
    result = json.loads(data.decode("utf-8", errors="replace"))
    if result.get("code") != 200:
        raise FlowUsError(f"更新页面标题失败：{_api_error_msg(result)}")


def upload_html_content(client: FlowUsClient, html_content: str) -> str:
    """Upload HTML content as a temp file. Returns ossName."""
    payload = {
        "content": html_content,
        "extName": "html",
    }

    url = f"{IMPORT_TEMP_FILE_API}?source=wandao"
    data = client.request("POST", url, data=payload, timeout=120)
    result = json.loads(data.decode("utf-8", errors="replace"))
    if result.get("code") != 200:
        raise FlowUsError(f"上传临时文件失败：{_api_error_msg(result)}")

    oss_name = result.get("data", {}).get("ossName", "")
    if not oss_name:
        raise FlowUsError("上传临时文件成功但未返回 ossName")

    return oss_name


def enqueue_import_task(client: FlowUsClient, block_id: str, space_id: str, oss_name: str) -> str:
    """Enqueue an import task. Returns task ID."""
    payload = {
        "eventName": "import",
        "request": {
            "blockId": block_id,
            "spaceId": space_id,
            "importOptions": {
                "type": "html",
                "ossName": oss_name,
            },
        },
    }

    data = client.request("POST", ENQUEUE_TASK_API, data=payload, timeout=30)
    result = json.loads(data.decode("utf-8", errors="replace"))
    if result.get("code") != 200:
        raise FlowUsError(f"导入任务创建失败：{_api_error_msg(result)}")

    task_id = result.get("data", {}).get("taskId", "")
    if not task_id:
        raise FlowUsError("导入任务创建成功但未返回 taskId")

    return task_id


def poll_task_result(client: FlowUsClient, task_id: str, timeout: int = 120, interval: float = 3.0) -> dict[str, Any]:
    """Poll for task completion. Returns task result."""
    start = time.time()

    while True:
        elapsed = time.time() - start
        if elapsed > timeout:
            raise FlowUsError(f"导入任务超时（{timeout}秒）：{task_id}")

        payload = {"taskIds": [task_id]}
        data = client.request("POST", GET_TASKS_API, data=payload, timeout=30)
        result = json.loads(data.decode("utf-8", errors="replace"))

        if result.get("code") != 200:
            raise FlowUsError(f"查询任务状态失败：{_api_error_msg(result)}")

        results = result.get("data", {}).get("results", {})
        task_result = results.get(task_id)

        if not task_result:
            time.sleep(interval)
            continue

        status = task_result.get("status", "")
        if status == "success":
            inner = task_result.get("result", {})
            if inner.get("status") == "success":
                return task_result
            msg = inner.get("msg", "未知错误")
            raise FlowUsError(f"导入任务失败：{msg}")
        elif status in ("failed", "error"):
            msg = task_result.get("result", {}).get("msg", "未知错误")
            raise FlowUsError(f"导入任务失败：{msg}")

        # Still processing, wait
        time.sleep(interval)


# ---------------------------------------------------------------------------
# Image upload
# ---------------------------------------------------------------------------

def get_upload_info(client: FlowUsClient, space_id: str, file_name: str, file_size: int = 0, file_type: str = "file") -> dict[str, Any]:
    """Get upload credentials from FlowUs.

    Returns dict with ossName, fileSecret, and token (accessKeyId, accessKeySecret, securityToken).
    """
    payload = {
        "fileName": file_name,
        "spaceId": space_id,
        "type": file_type,
        "size": file_size,
    }

    data = client.request("POST", UPLOAD_INFO_API, data=payload, timeout=30)
    result = json.loads(data.decode("utf-8", errors="replace"))
    if result.get("code") != 200:
        raise FlowUsError(f"获取上传凭证失败：{_api_error_msg(result)}")

    return result.get("data", {})


def extract_appid_from_token(token: str) -> str:
    """Extract appid (key field) from JWT token."""
    try:
        # JWT format: header.payload.signature
        parts = token.split(".")
        if len(parts) >= 2:
            # Decode payload (base64url)
            payload_b64 = parts[1]
            # Add padding if needed
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            payload_bytes = base64.urlsafe_b64decode(payload_b64)
            payload = json.loads(payload_bytes)
            emit("JWT payload decoded successfully", level="debug")
            return str(payload.get("key", ""))
    except Exception as e:
        emit(f"Failed to parse JWT: {e}", level="debug")
    return ""


def upload_to_cos(upload_info: dict[str, Any], file_path: Path, appid: str = "", timeout: int = 60) -> str:
    """Upload a file to Tencent COS using temporary credentials.

    Returns the ossName of the uploaded file.
    """
    import urllib.request
    import urllib.error

    oss_name = upload_info.get("ossName", "")
    token = upload_info.get("token", {})
    access_key_id = token.get("accessKeyId", "")
    access_key_secret = token.get("accessKeySecret", "")
    security_token = token.get("securityToken", "")

    if not oss_name or not access_key_id:
        raise FlowUsError("上传凭证不完整")

    # Read file content
    file_content = file_path.read_bytes()

    # Parse ossName - format: oss/{uuid}/{filename}
    bucket = FLOWUS_COS_BUCKET
    key = oss_name

    # Try multiple regions, starting with the most likely
    regions = ["ap-beijing", "ap-nanjing", "ap-guangzhou", "ap-shanghai", "ap-chengdu"]
    key_options = [key]
    last_error: Exception | None = None

    for region in regions:
        for current_key in key_options:
            cos_host = f"{bucket}.cos.{region}.myqcloud.com"
            upload_url = f"https://{cos_host}/{current_key}"

            emit(f"COS upload尝试: region={region}", level="debug")

            # Generate timestamp
            now = int(time.time())
            start_time = now
            end_time = now + 3600
            key_time = f"{start_time};{end_time}"

            # Build COS signature using v1 algorithm
            sign_key = hmac.new(
                access_key_secret.encode("utf-8"),
                key_time.encode("utf-8"),
                hashlib.sha1
            ).hexdigest()

            http_method = "put"
            uri_pathname = f"/{current_key}"
            http_parameters = ""
            http_headers = f"host={cos_host}"

            http_string = f"{http_method}\n{uri_pathname}\n{http_parameters}\n{http_headers}\n"
            http_string_sha1 = hashlib.sha1(http_string.encode("utf-8")).hexdigest()

            string_to_sign = f"sha1\n{key_time}\n{http_string_sha1}\n"

            signature = hmac.new(
                sign_key.encode("utf-8"),
                string_to_sign.encode("utf-8"),
                hashlib.sha1
            ).hexdigest()

            auth = (
                f"q-sign-algorithm=sha1"
                f"&q-ak={access_key_id}"
                f"&q-sign-time={key_time}"
                f"&q-key-time={key_time}"
                f"&q-header-list=host"
                f"&q-url-param-list="
                f"&q-signature={signature}"
            )

            headers = {
                "Authorization": auth,
                "Content-Type": "application/octet-stream",
                "Host": cos_host,
                "x-cos-security-token": security_token,
            }

            req = urllib.request.Request(upload_url, data=file_content, headers=headers, method="PUT")

            try:
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    if response.status in (200, 204):
                        emit(f"COS 上传成功: region={region}", level="debug")
                        return oss_name
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                if exc.code == 404 and "NoSuchBucket" in detail:
                    emit(f"Bucket {bucket} 在 region {region} 不存在，尝试下一个...", level="debug")
                    continue
                emit(f"COS 上传失败: region={region}, HTTP {exc.code} - {detail[:200]}", level="debug")
                continue
            except urllib.error.URLError as exc:
                last_error = exc
                emit(f"COS 上传网络错误: region={region}, {_friendly_network_error(exc.reason)}", level="debug")
                continue

    raise FlowUsError(f"COS 上传失败：{_friendly_network_error(last_error)}" if last_error else "COS 上传失败：所有 region 都无法连接")


def search_resource(client: FlowUsClient, file_path: Path) -> str | None:
    """Check if a file exists in FlowUs by SHA256 hash.

    Returns ossName if found, None otherwise.
    """
    file_content = file_path.read_bytes()
    sha256 = hashlib.sha256(file_content).hexdigest()
    file_size = len(file_content)

    payload = {
        "sha256": sha256,
        "size": file_size,
    }
    data = client.request("POST", SEARCH_RESOURCE_API, data=payload, timeout=30)
    result = json.loads(data.decode("utf-8", errors="replace"))
    emit(f"  search/resource: code={result.get('code')}", level="debug")

    if result.get("code") != 200:
        return None

    found_oss = result.get("data", {}).get("ossName") if isinstance(result.get("data"), dict) else None
    return found_oss


def create_signed_urls(client: FlowUsClient, block_id: str, oss_name: str, is_public: bool = False, max_retries: int = 3) -> str:
    """Get a signed CDN URL for an uploaded file.

    Calls /api/file/create_urls (same as FlowUs web client after COS upload).
    Returns the signed CDN URL with TTL token.
    Retries up to max_retries times with delays (COS propagation may take time).
    """
    payload = {
        "batch": [
            {
                "blockId": block_id,
                "ossName": oss_name,
                "isPublic": is_public,
            }
        ]
    }

    for attempt in range(1, max_retries + 1):
        data = client.request("POST", CREATE_URLS_API, data=payload, timeout=30)
        result = json.loads(data.decode("utf-8", errors="replace"))
        emit(f"  create_urls (attempt {attempt}): code={result.get('code')}", level="debug")

        if not isinstance(result, dict):
            raise FlowUsError(f"获取签名 URL 失败：响应格式异常 (code={result.get('code', '?')})")

        if result.get("code") != 200:
            raise FlowUsError(f"获取签名 URL 失败：{_api_error_msg(result)}")

        urls = result.get("data")
        if isinstance(urls, list) and urls and isinstance(urls[0], dict) and urls[0].get("url"):
            return urls[0]["url"]

        # data is [null] — file not yet propagated, retry
        if attempt < max_retries:
            delay = 3 * attempt
            emit(f"  create_urls 返回空，{delay}秒后重试...", level="debug")
            time.sleep(delay)

    raise FlowUsError("获取签名 URL 失败：多次重试后仍返回空")


def upload_local_image(client: FlowUsClient, space_id: str, image_path: Path, timeout: int = 60) -> tuple[str, str]:
    """Upload a local image to FlowUs.

    Tries import_temp_file API first (simpler, returns registered ossName).
    Falls back to COS upload if import_temp_file doesn't support images.

    Returns (oss_name, file_secret) for use in FlowUs image blocks.
    """
    if not image_path.exists():
        raise FlowUsError(f"图片文件不存在：{image_path}")

    file_name = image_path.name
    ext = image_path.suffix.lstrip(".").lower()
    if not ext:
        ext = "png"

    # Try import_temp_file API with base64-encoded content
    try:
        file_bytes = image_path.read_bytes()
        b64_content = base64.b64encode(file_bytes).decode("ascii")
        payload = {
            "content": b64_content,
            "extName": ext,
            "fileName": file_name,
        }
        url = f"{IMPORT_TEMP_FILE_API}?source=wandao"
        data = client.request("POST", url, data=payload, timeout=120)
        result = json.loads(data.decode("utf-8", errors="replace"))
        emit(f"  import_temp_file: code={result.get('code')}", level="debug")

        if result.get("code") == 200:
            oss_name = result.get("data", {}).get("ossName", "")
            if oss_name:
                emit(f"图片上传成功(import_temp_file): {file_name}", level="debug")
                return oss_name, ""
    except Exception:
        emit("  import_temp_file 失败，回退到 COS", level="debug")

    # Fallback: COS upload
    file_size = image_path.stat().st_size
    upload_info = get_upload_info(client, space_id, file_name, file_size, file_type="file")
    oss_name = upload_info.get("ossName", "")
    file_secret = upload_info.get("fileSecret", "")
    if not oss_name:
        raise FlowUsError("获取上传凭证成功但未返回 ossName")

    token = upload_info.get("token", {})
    appid = extract_appid_from_token(token.get("accessKeyId", ""))
    upload_to_cos(upload_info, image_path, appid=appid, timeout=timeout)

    emit(f"图片上传成功(COS): {file_name}", level="debug")
    return oss_name, file_secret


def extract_local_images(md_content: str, source_dir: Path) -> list[tuple[str, Path]]:
    """Extract local image references from markdown content.

    Returns list of (original_path, absolute_path) tuples.
    """
    # Match markdown images: ![alt](path)
    image_pattern = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
    images = []
    resolved_source = source_dir.resolve()

    for match in image_pattern.finditer(md_content):
        path_str = match.group(2).strip()

        # Skip URLs (http/https)
        if path_str.startswith(("http://", "https://")):
            continue

        # Reject absolute paths (potential directory escape)
        if path_str.startswith("/"):
            continue

        # Resolve relative path and check boundary
        abs_path = (source_dir / path_str).resolve()
        if not abs_path.is_relative_to(resolved_source):
            continue

        if abs_path.exists() and abs_path.is_file():
            images.append((path_str, abs_path))

    return images


def build_image_url(oss_name: str) -> str:
    """Build FlowUs CDN URL from ossName."""
    if oss_name.startswith("http"):
        return oss_name
    return f"https://cdn2.flowus.cn/{oss_name}"


# ---------------------------------------------------------------------------
# Image block creation (FlowUs type 14)
# ---------------------------------------------------------------------------

DOCS_API = f"{FLOWUS_API_BASE}/docs/{{doc_id}}"


def fetch_page_blocks(client: FlowUsClient, page_id: str) -> dict[str, Any]:
    """Fetch page blocks from FlowUs API."""
    url = DOCS_API.format(doc_id=page_id)
    referer = f"https://flowus.cn/{page_id}"
    data = client.get_json(url, referer=referer)
    if data.get("code") != 200:
        raise FlowUsError(f"获取页面 blocks 失败：{data.get('msg', data)}")
    return data.get("data", {}).get("blocks", {})


def create_image_block(
    client: FlowUsClient,
    space_id: str,
    page_id: str,
    block_id: str,
    oss_name: str,
    file_secret: str = "",
    after_id: str | None = None,
) -> None:
    """Create an image block (type 14) and add it to the page."""
    operations = {
        "requestId": generate_uuid(),
        "transactions": [
            {
                "id": generate_uuid(),
                "spaceId": space_id,
                "operations": [
                    {
                        "id": block_id,
                        "path": [],
                        "command": "set",
                        "table": "block",
                        "args": {
                            "uuid": block_id,
                            "spaceId": space_id,
                            "parentId": page_id,
                            "type": 14,
                            "status": 1,
                            "data": {
                                "ossName": oss_name,
                                "fileSecret": file_secret,
                                "segments": [],
                            },
                        },
                    },
                    {
                        "id": page_id,
                        "command": "listAfter",
                        "path": ["subNodes"],
                        "table": "block",
                        "args": {
                            "uuid": block_id,
                            **({"after": after_id} if after_id else {}),
                        },
                    },
                ],
            }
        ],
    }

    data = client.request("POST", BLOCKS_TRANSACTIONS_API, data=operations, timeout=30)
    result = json.loads(data.decode("utf-8", errors="replace"))
    if result.get("code") != 200:
        raise FlowUsError(f"创建图片块失败：{_api_error_msg(result)}")


def update_block_ossname(client: FlowUsClient, space_id: str, block_id: str, signed_url: str) -> None:
    """Update an image block's ossName to a signed CDN URL."""
    operations = {
        "requestId": generate_uuid(),
        "transactions": [
            {
                "id": generate_uuid(),
                "spaceId": space_id,
                "operations": [
                    {
                        "id": block_id,
                        "path": ["data", "ossName"],
                        "command": "set",
                        "table": "block",
                        "args": signed_url,
                    },
                ],
            }
        ],
    }

    data = client.request("POST", BLOCKS_TRANSACTIONS_API, data=operations, timeout=30)
    result = json.loads(data.decode("utf-8", errors="replace"))
    if result.get("code") != 200:
        raise FlowUsError(f"更新图片块 ossName 失败：{_api_error_msg(result)}")


def parse_markdown_image_positions(md_content: str) -> list[dict[str, Any]]:
    """Parse markdown to extract ordered content elements with image positions.

    Returns a list of dicts describing each content element:
    - {"type": "text", "text": "..."} for text/heading lines
    - {"type": "image", "oss_name": "...", "alt": "..."} for images
    - {"type": "skip"} for empty lines
    """
    lines = md_content.split("\n")
    elements: list[dict[str, Any]] = []

    in_code_block = False
    for line in lines:
        # Code blocks
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            if not in_code_block:
                elements.append({"type": "text", "text": line.strip()})
            continue

        if in_code_block:
            continue

        # Empty line
        if not line.strip():
            elements.append({"type": "skip"})
            continue

        # Standalone image line: ![alt](path)
        img_match = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", line)
        if img_match:
            alt = img_match.group(1)
            path = img_match.group(2).strip()
            elements.append({"type": "image", "path": path, "alt": alt})
            continue

        # Text line (may contain inline images - we skip those for now)
        elements.append({"type": "text", "text": line.strip()})

    return elements


def insert_image_blocks_after_import(
    client: FlowUsClient,
    space_id: str,
    page_id: str,
    md_content: str,
    image_oss_mapping: dict[str, tuple[str, str]],
) -> int:
    """Insert image blocks into the page after HTML import.

    Args:
        client: FlowUs client
        space_id: Space ID
        page_id: Page ID (already has text content from HTML import)
        md_content: Original markdown content
        image_oss_mapping: Mapping of original image path -> (ossName, fileSecret)

    Returns:
        Number of image blocks created
    """
    if not image_oss_mapping:
        return 0

    # Fetch current page blocks
    blocks = fetch_page_blocks(client, page_id)
    if not blocks:
        return 0

    # Get the page block and its subNodes
    page_block = blocks.get(page_id)
    if not page_block:
        return 0

    sub_nodes = page_block.get("subNodes", [])
    if not sub_nodes:
        return 0

    # Parse markdown to find image positions
    elements = parse_markdown_image_positions(md_content)

    # Build ordered list of (text_key, is_image, image_info) for matching
    # We need to match text blocks to markdown elements to find insertion points
    text_block_queue: list[str] = list(sub_nodes)
    text_block_idx = 0

    created = 0
    last_inserted_after: str | None = None

    for elem in elements:
        if elem["type"] == "skip":
            continue

        if elem["type"] == "text":
            # Advance to next text block
            if text_block_idx < len(text_block_queue):
                last_inserted_after = text_block_queue[text_block_idx]
                text_block_idx += 1
            continue

        if elem["type"] == "image":
            img_path = elem["path"]
            mapping = image_oss_mapping.get(img_path)
            if not mapping:
                continue
            oss_name, file_secret = mapping

            # Determine where to insert: after the last text block we processed
            after_id = last_inserted_after

            # Create image block
            img_block_id = generate_uuid()
            try:
                # Step 1: Create block with raw ossName
                create_image_block(
                    client, space_id, page_id, img_block_id, oss_name, file_secret, after_id
                )

                # Step 2: Get signed CDN URL (block must exist first)
                try:
                    signed_url = create_signed_urls(client, img_block_id, oss_name)
                    # Step 3: Update block with signed URL
                    update_block_ossname(client, space_id, img_block_id, signed_url)
                except (FlowUsError, Exception) as url_exc:
                    emit(f"  获取签名 URL 失败（图片可能显示为空）: {url_exc}", level="warn")
                created += 1

                # Add to our tracking list so subsequent images can be placed after this one
                text_block_queue.insert(text_block_idx, img_block_id)
                last_inserted_after = img_block_id
                text_block_idx += 1

                emit(f"  图片块创建成功: {elem.get('alt', 'image')}", level="debug")
            except (FlowUsError, Exception) as exc:
                emit(f"  图片块创建失败: {exc}", level="warn")

    return created


# ---------------------------------------------------------------------------
# Markdown processing
# ---------------------------------------------------------------------------

def _resolve_image_src(raw_url: str, source_dir: Path | None = None) -> str:
    """Resolve image URL to a displayable src.

    Remote URLs (http/https/data) are returned as-is.
    Local file paths are converted to base64 data URLs.
    """
    if not raw_url or raw_url.startswith(("http://", "https://", "data:")):
        return raw_url

    if source_dir is None:
        return raw_url

    # Reject absolute paths (potential directory escape)
    if raw_url.startswith("/"):
        return raw_url

    # Resolve relative path and check boundary
    image_path = (source_dir / raw_url).resolve()
    if not image_path.is_relative_to(source_dir.resolve()):
        return raw_url

    if not image_path.exists() or not image_path.is_file():
        return raw_url

    try:
        from mimetypes import guess_type
        mime_type = guess_type(str(image_path))[0] or "application/octet-stream"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"
    except Exception:
        return raw_url


def markdown_to_html(md_content: str, title: str = "", source_dir: Path | None = None) -> str:
    """Convert Markdown to HTML for FlowUs import.

    Local images are inlined as base64 data URLs (like Feishu approach).
    Remote images are kept as-is.

    Args:
        md_content: Markdown content
        title: Document title
        source_dir: Directory to resolve relative image paths
    """
    lines = md_content.split("\n")
    html_parts: list[str] = []

    in_code_block = False
    code_lang = ""
    code_lines: list[str] = []

    for line in lines:
        # Code blocks
        if line.strip().startswith("```"):
            if in_code_block:
                escaped = html.escape("\n".join(code_lines))
                html_parts.append(f"<pre><code class=\"language-{code_lang}\">{escaped}</code></pre>")
                in_code_block = False
                code_lines = []
                code_lang = ""
            else:
                in_code_block = True
                code_lang = line.strip()[3:].strip()
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        # Standalone image line: ![alt](path)
        img_match = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", line)
        if img_match:
            alt = img_match.group(1)
            src = _resolve_image_src(img_match.group(2).strip(), source_dir)
            html_parts.append(f'<p><img src="{src}" alt="{html.escape(alt)}"></p>')
            continue

        # Headings
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2)
            html_parts.append(f"<h{level}>{_inline_to_html(text, source_dir)}</h{level}>")
            continue

        # Horizontal rule
        if re.match(r"^---+\s*$", line):
            html_parts.append("<hr>")
            continue

        # Unordered list
        list_match = re.match(r"^(\s*)[-*+]\s+(.+)$", line)
        if list_match:
            text = list_match.group(2)
            html_parts.append(f"<li>{_inline_to_html(text, source_dir)}</li>")
            continue

        # Ordered list
        ol_match = re.match(r"^(\s*)\d+\.\s+(.+)$", line)
        if ol_match:
            text = ol_match.group(2)
            html_parts.append(f"<li>{_inline_to_html(text, source_dir)}</li>")
            continue

        # Empty line
        if not line.strip():
            html_parts.append("")
            continue

        # Normal paragraph
        html_parts.append(f"<p>{_inline_to_html(line, source_dir)}</p>")

    # Close unclosed code block
    if in_code_block and code_lines:
        escaped = html.escape("\n".join(code_lines))
        html_parts.append(f"<pre><code class=\"language-{code_lang}\">{escaped}</code></pre>")

    body = "\n".join(html_parts)

    return f"""<!DOCTYPE html>
<html>
  <head>
    <title>{html.escape(title)}</title>
  </head>
  <body>
{body}
  </body>
</html>"""


def _inline_to_html(text: str, source_dir: Path | None = None) -> str:
    """Convert inline Markdown formatting to HTML."""
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Italic
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    # Code
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    # Strikethrough
    text = re.sub(r"~~(.+?)~~", r"<del>\1</del>", text)
    # Links (but not images)
    text = re.sub(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    # Inline images -> <img> tags with resolved src
    def _replace_inline_img(m: re.Match[str]) -> str:
        alt = m.group(1)
        src = _resolve_image_src(m.group(2).strip(), source_dir)
        return f'<img src="{src}" alt="{html.escape(alt)}">'
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _replace_inline_img, text)
    return text


def scan_markdown_docs(source_dir: Path) -> list[dict[str, Any]]:
    """Scan a directory for Markdown files, preserving directory structure."""
    docs: list[dict[str, Any]] = []

    if not source_dir.exists():
        raise FlowUsError(f"源目录不存在：{source_dir}")

    resolved_source = source_dir.resolve()

    for md_path in sorted(source_dir.rglob("*")):
        if not md_path.is_file():
            continue
        if md_path.suffix.lower() not in MARKDOWN_DOC_EXTENSIONS:
            continue

        # Symlink / boundary check: reject files that resolve outside source_dir
        if not md_path.resolve().is_relative_to(resolved_source):
            emit(f"跳过目录外文件（符号链接逃逸）: {md_path}", level="warn")
            continue

        # Calculate relative path for folder structure
        rel = md_path.relative_to(source_dir)
        folders = list(rel.parent.parts) if rel.parent != Path(".") else []

        try:
            content = md_path.read_text(encoding="utf-8")
        except Exception as exc:
            emit(f"读取文件失败 {md_path}: {exc}", level="warn")
            continue

        # Extract title from first heading or filename
        title = ""
        title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if title_match:
            title = title_match.group(1).strip()
        if not title:
            title = md_path.stem

        docs.append({
            "path": str(md_path),
            "relative": str(rel),
            "title": title,
            "folders": folders,
            "content": content,
            "size": md_path.stat().st_size,
        })

    return docs


# ---------------------------------------------------------------------------
# Main import function
# ---------------------------------------------------------------------------

def import_flowus(args: argparse.Namespace) -> dict[str, Any]:
    """Main import function."""
    auth_file = auth_path_from_args(args)
    client = FlowUsClient(auth_file, args)

    source_dir = Path(args.source_dir).expanduser().resolve()
    space_id = args.space_id
    parent_id = args.parent_id

    if not source_dir.exists():
        raise FlowUsError(f"源目录不存在：{source_dir}")

    # Get available targets
    targets = list_import_targets(client)
    if not targets:
        raise FlowUsError("没有找到可写入的页面。请先点击'列出可导入目标'确认有写入权限。")

    # Auto-detect space_id from parent_id if not specified
    if parent_id and not space_id:
        matching = [t for t in targets if t["id"] == parent_id]
        if matching:
            space_id = matching[0]["spaceId"]
            emit("从目标页面自动检测空间 ID")
        else:
            raise FlowUsError(
                f"页面 {parent_id} 不在可写入目标列表中。"
                "请先点击'列出可导入目标'确认有写入权限。"
            )

    if not space_id:
        # Default to the first target's space
        space_id = targets[0]["spaceId"]
        emit(f"自动选择空间: {targets[0]['spaceName']}")

    # If no parent_id specified, find a valid parent from the targets
    if not parent_id:
        space_targets = [t for t in targets if t.get("spaceId") == space_id]
        if not space_targets:
            raise FlowUsError(
                f"在空间 {space_id} 中没有找到可写入的页面。"
                "请先点击'列出可导入目标'确认有写入权限，或手动指定 --parent-id。"
            )
        parent_id = space_targets[0]["id"]
        emit(f"自动选择目标页面: {space_targets[0]['name']} ({parent_id})")
    else:
        # Validate parent_id is in the targets
        matching = [t for t in targets if t["id"] == parent_id]
        if not matching:
            emit(f"警告: 页面 {parent_id} 不在可写入目标列表中，导入可能会失败", level="warn")
        else:
            emit(f"目标页面: {matching[0]['name']} ({parent_id})")

    # Scan markdown files
    docs = scan_markdown_docs(source_dir)
    if not docs:
        raise FlowUsError(f"源目录中没有找到 Markdown 文件：{source_dir}")

    emit(f"开始导入到 FlowUs：共 {len(docs)} 篇文档", event="task.started", totals={"documents": len(docs)})

    # Open checkpoint
    checkpoint = open_checkpoint_from_args(args, "xiliu", "import")

    if checkpoint:
        checkpoint.start_task({
            "spaceId": space_id,
            "parentId": parent_id,
            "sourceDir": str(source_dir),
            "totalDocs": len(docs),
            "resume": bool(getattr(args, "resume", False)),
            "retryFailed": bool(getattr(args, "retry_failed", False)),
        })

    # Filter for resume / retry-failed mode
    is_resume = bool(getattr(args, "resume", False))
    is_retry_failed = bool(getattr(args, "retry_failed", False))
    if checkpoint and is_retry_failed:
        docs = [d for d in docs if checkpoint.item_status(f"xiliu:import:{d['relative']}") == "failed"]
        emit(f"只重试失败项：剩余 {len(docs)} 篇文档")
    elif checkpoint and is_resume:
        docs = [d for d in docs if checkpoint.item_status(f"xiliu:import:{d['relative']}") != "completed"]
        emit(f"断点续跑：跳过已完成项，剩余 {len(docs)} 篇文档")

    # Import each document
    imported = 0
    skipped = 0
    failed = 0
    total = len(docs)

    for i, doc in enumerate(docs, 1):
        item_key = f"xiliu:import:{doc['relative']}"
        title = doc["title"]

        try:
            # Check resume / retry-failed
            if checkpoint and (is_resume or is_retry_failed) and checkpoint.item_status(item_key) == "completed":
                skipped += 1
                continue

            if checkpoint:
                checkpoint.start_item(item_key, "content")

            # Count local images for logging
            doc_dir = Path(doc["path"]).parent
            local_images = extract_local_images(doc["content"], doc_dir)
            image_count = len(local_images)
            if image_count:
                emit(f"[{i}/{total}] 含 {image_count} 张本地图片（将内嵌到 HTML 中）...")

            # Convert markdown to HTML with images inlined as base64 data URLs
            html_content = markdown_to_html(doc["content"], title, source_dir=doc_dir)

            # Create empty page
            page_id = create_empty_page(client, space_id, parent_id, title)

            # Upload HTML content
            oss_name = upload_html_content(client, html_content)

            # Enqueue import task
            task_id = enqueue_import_task(client, page_id, space_id, oss_name)

            # Poll for completion
            task_timeout = int(getattr(args, "task_timeout", 120) or 120)
            poll_task_result(client, task_id, timeout=task_timeout, interval=3.0)

            # Update title (import task may overwrite it)
            update_page_title(client, space_id, page_id, title)

            imported += 1
            if image_count > 0:
                emit(f"[{i}/{total}] 导入成功: {title} (含 {image_count} 张图片)")
            else:
                emit(f"[{i}/{total}] 导入成功: {title}")

            if checkpoint:
                checkpoint.complete_item(item_key, metadata={"pageId": page_id, "title": title})

        except FlowUsError as exc:
            failed += 1
            emit(f"[{i}/{total}] 导入失败 {title}: {exc}", level="error")
            if checkpoint:
                checkpoint.fail_item(item_key, str(exc))

        except Exception as exc:
            failed += 1
            emit(f"[{i}/{total}] 导入失败 {title}: {exc}", level="error")
            if checkpoint:
                checkpoint.fail_item(item_key, str(exc))

    result = {
        "provider": "flowus-import",
        "spaceId": space_id,
        "parentId": parent_id,
        "sourceDir": str(source_dir),
        "totalDocs": total,
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
        "failureCount": failed,
    }

    # Finalize checkpoint
    if checkpoint:
        checkpoint.complete_task(result)
        checkpoint.close()

    finalize_report(result, provider="flowus-import")

    emit(
        f"FlowUs 导入完成：成功 {imported}, 跳过 {skipped}, 失败 {failed}",
        event="task.completed",
        level="success" if failed == 0 else "warn",
        stats={
            "importedDocs": imported,
            "skippedDocs": skipped,
            "failureCount": failed,
        },
    )

    emit(f"导入完成: 成功 {imported}, 跳过 {skipped}, 未通过 {failed}")
    return result


def scan_targets(args: argparse.Namespace) -> dict[str, Any]:
    """List available import targets."""
    auth_file = auth_path_from_args(args)
    client = FlowUsClient(auth_file, args)

    targets = list_import_targets(client)

    return {
        "provider": "flowus-import",
        "targetCount": len(targets),
        "targets": targets,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导入 Markdown 文档到 FlowUs (息流)")
    parser.add_argument("--login", action="store_true", help="打开浏览器登录并保存 Token")
    parser.add_argument("--login-wait-seconds", type=float, default=0.0, help="等待指定秒数后自动保存登录凭证")
    parser.add_argument("--scan-targets", action="store_true", help="列出可导入的目标空间/页面")
    parser.add_argument("--source-dir", help="本地 Markdown 目录")
    parser.add_argument("--space-id", help="目标空间 ID")
    parser.add_argument("--parent-id", help="父页面 ID（默认为空间根目录）")
    parser.add_argument("--auth-file", help=f"登录凭证文件，默认 {default_auth_path()}")
    parser.add_argument("--profile-dir", help=f"浏览器配置目录，默认 {default_profile_path()}")
    parser.add_argument("--browser-path", help="可选 Chrome/Edge/Chromium 可执行文件路径")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Chrome DevTools 调试端口")
    parser.add_argument("--close-started-chrome", action="store_true", help="任务结束后关闭本脚本启动的浏览器")
    parser.add_argument("--progress-every", type=int, default=1, help="每处理多少篇输出一次进度")
    parser.add_argument("--request-delay", type=float, default=0.8, help="每次请求前固定等待秒数")
    parser.add_argument("--request-jitter", type=float, default=0.4, help="每次请求额外随机等待秒数")
    parser.add_argument("--retry", type=int, default=2, help="网络请求失败时的重试次数")
    parser.add_argument("--task-timeout", type=int, default=120, help="导入任务超时秒数")
    add_checkpoint_args(parser)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if not argv:
        print("用法: import_flowus.py --source-dir <dir> [--space-id <id>] [--auth-file <file>]", file=sys.stderr)
        return 2
    try:
        if args.login:
            result = login_and_save_auth(args)
        elif args.scan_targets:
            result = scan_targets(args)
        else:
            if not args.source_dir:
                raise FlowUsError("--source-dir is required")
            result = import_flowus(args)
    except KeyboardInterrupt:
        emit("FlowUs 导入已停止。", event="task.stopped", level="warn")
        print("Interrupted.", file=sys.stderr)
        return 130
    except (FlowUsError, ) as exc:
        emit(
            f"FlowUs 导入失败：{exc}",
            event="task.failed",
            level="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        emit(
            f"FlowUs 导入失败：{exc}",
            event="task.failed",
            level="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
