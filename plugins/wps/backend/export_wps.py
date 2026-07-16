#!/usr/bin/env python3
"""WPS document original-file exporter.

The provider reads downloadable WPS cloud documents, including Smart Documents, while excluding device/local documents. It deliberately keeps
the desktop contract small: login, scan, export, and clear-auth. Browser cookies
stay in an isolated profile and every command writes exactly one JSON result to
stdout; human-readable prompts/errors use stderr.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

if __package__ in {None, ""}:
    root = str(Path(__file__).resolve().parents[3])
    if root not in sys.path:
        sys.path.insert(0, root)

from wandao_core.browser import (
    CDPClient,
    ExportError,
    ExportStopped,
    check_stopped,
    chrome_debug_available,
    default_data_dir,
    http_json,
    open_tab,
    start_chrome,
    wait_for_debug_port,
)
from wandao_core.checkpoint import add_checkpoint_args, open_checkpoint_from_args
from wandao_core.credentials import write_private_json
from wandao_core.report import finalize_report

WPS_DOCUMENT_URL = "https://365.kdocs.cn"
WPS_HOME_URL = WPS_DOCUMENT_URL
WPS_DEBUG_PORT = 9237
API_HOST = "365.kdocs.cn"
WPS_DOCUMENT_ROOT_ID = "smart-documents"
WPS_DOCUMENT_SEARCH_PATH = "/3rd/drive/api/v6/search/files"
WPS_DOCUMENT_DOWNLOAD_PATH = "/api/v3/office/file/{file_id}/download"
WPS_DOCUMENT_EXECUTE_PATH = "/api/v3/office/file/{file_id}/core/execute"
AUTH_SESSION_COOKIE_NAMES = frozenset({"wps_sid", "kso_sid"})
AUTH_REQUEST_COOKIE_NAMES = frozenset({"csrf"})
AUTH_COOKIE_NAMES = AUTH_SESSION_COOKIE_NAMES | AUTH_REQUEST_COOKIE_NAMES
AUTH_COOKIE_FIELDS = frozenset({
    "name", "value", "domain", "path", "secure", "httpOnly", "sameSite", "expires"
})
API_HOSTS = frozenset({"365.kdocs.cn", "drive.kdocs.cn", "www.kdocs.cn", "docs.wps.cn", "account.kdocs.cn", "account.wps.cn"})
DOWNLOAD_SUFFIXES = ("kdocs.cn", "wps.cn", "wpscdn.cn")
FORBIDDEN_PATH_CHARS = '<>:"/\\|?*'
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


@dataclass(frozen=True)
class WPSNode:
    id: str
    file_id: str
    title: str
    parent_id: str | None
    type: str
    size: int | None = None
    mtime: int | float | str | None = None


class WPSJSONTransport(Protocol):
    def request_json(
        self,
        method: str,
        url: str,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...


class WPSApiError(ExportError):
    def __init__(self, message: str, *, status: int = 0, retry_after: float | None = None) -> None:
        super().__init__(safe_error_text(message))
        self.status = int(status)
        self.retry_after = retry_after


class WPSAuthExpiredError(WPSApiError):
    pass


class WPSRateLimitError(WPSApiError):
    pass


def safe_error_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"https?://[^\s]+", "[REDACTED_URL]", text)
    text = re.sub(r"(?i)cookie\s*:\s*[^\s]+", "Cookie: [REDACTED]", text)
    text = re.sub(r"(?i)authorization\s*:\s*[^\s]+", "Authorization: [REDACTED]", text)
    text = re.sub(r"(?i)(token|signature|access_token|refresh_token)=[^&\s]+", r"\1=[REDACTED]", text)
    return text[:1000]


def redact_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(str(value))
        return urllib.parse.urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))
    except ValueError:
        return "[REDACTED_URL]"


def _official_host(host: str, allowed: Sequence[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == item or host.endswith("." + item) for item in allowed)


def validate_api_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(str(url))
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in API_HOSTS:
        raise ValueError("WPS API URL is outside the official HTTPS allowlist")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("WPS API URL contains forbidden credentials or fragment")
    path = parsed.path or "/"
    if not (path.startswith("/api/") or path.startswith("/3rd/drive/api/")):
        raise ValueError("WPS API path is outside the read-only API allowlist")
    return urllib.parse.urlunsplit(("https", parsed.hostname.lower(), path, parsed.query, ""))


def validate_download_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(str(url))
    if parsed.scheme != "https" or not _official_host(parsed.hostname or "", DOWNLOAD_SUFFIXES):
        raise ValueError("WPS download URL is outside the official HTTPS allowlist")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("WPS download URL contains forbidden credentials or fragment")
    return str(url)


def wps_data_root() -> Path:
    root = default_data_dir()
    return root if root.name.casefold() == "wps" else root / "wps"


def default_auth_path() -> Path:
    return wps_data_root() / "auth.json"


def default_profile_path() -> Path:
    return wps_data_root() / "browser-profile"


def filter_auth_cookies(cookies: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for cookie in cookies:
        if not isinstance(cookie, Mapping) or str(cookie.get("name") or "") not in AUTH_COOKIE_NAMES:
            continue
        domain = str(cookie.get("domain") or "").lower().lstrip(".")
        if not _official_host(domain, ("kdocs.cn", "wps.cn")):
            continue
        item = {key: cookie[key] for key in AUTH_COOKIE_FIELDS if key in cookie and cookie[key] is not None}
        if item.get("value"):
            filtered.append(item)
    return filtered


def _page_is_wps(page: Mapping[str, Any]) -> bool:
    try:
        parsed = urllib.parse.urlsplit(str(page.get("url") or ""))
    except ValueError:
        return False
    return parsed.scheme == "https" and _official_host(parsed.hostname or "", ("kdocs.cn", "wps.cn"))


def _wait_for_wps_page_ready(cdp: CDPClient, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + max(0.1, float(timeout))
    expression = """
        ({
          href: String(location.href || ""),
          readyState: String(document.readyState || "")
        })
    """
    while True:
        try:
            state = cdp.evaluate(expression, timeout=5)
        except Exception:
            state = None
        if isinstance(state, Mapping):
            try:
                parsed = urllib.parse.urlsplit(str(state.get("href") or ""))
            except ValueError:
                parsed = None
            ready_state = str(state.get("readyState") or "").lower()
            if (
                parsed is not None
                and parsed.scheme == "https"
                and _official_host(parsed.hostname or "", ("kdocs.cn", "wps.cn"))
                and ready_state in {"interactive", "complete"}
            ):
                return
        if time.monotonic() >= deadline:
            raise ExportError("WPS 页面尚未准备好，请稍后重试。")
        time.sleep(0.1)


def connect_wps_browser(args: argparse.Namespace, initial_url: str = WPS_HOME_URL) -> tuple[CDPClient, subprocess.Popen[Any] | None]:
    port = int(getattr(args, "port", WPS_DEBUG_PORT) or WPS_DEBUG_PORT)
    profile = Path(getattr(args, "profile_dir", "") or default_profile_path()).expanduser().resolve()
    process: subprocess.Popen[Any] | None = None
    if not chrome_debug_available(port):
        process = start_chrome(port, profile, initial_url, getattr(args, "browser_path", None))
        wait_for_debug_port(port, timeout=30)
    pages = http_json(f"http://127.0.0.1:{port}/json/list", timeout=5)
    page = next((p for p in pages if p.get("type") == "page" and p.get("webSocketDebuggerUrl") and _page_is_wps(p)), None)
    if page is None:
        open_tab(port, initial_url)
        pages = http_json(f"http://127.0.0.1:{port}/json/list", timeout=5)
        page = next((p for p in pages if p.get("type") == "page" and p.get("webSocketDebuggerUrl") and _page_is_wps(p)), None)
    if page is None:
        close_owned_browser(None, process)
        raise ExportError("未找到或无法创建 WPS 文档页面。")
    cdp = CDPClient(str(page["webSocketDebuggerUrl"]))
    cdp.connect()
    cdp.send("Runtime.enable")
    cdp.send("Page.enable")
    _wait_for_wps_page_ready(cdp)
    return cdp, process


def close_owned_browser(cdp: CDPClient | None, process: Any) -> None:
    """Close CDP and only terminate a browser process started by this command."""
    if cdp is not None:
        try:
            cdp.close()
        except Exception:
            pass
    if process is None:
        return
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except (subprocess.TimeoutExpired, TimeoutError):
                process.kill()
                process.wait(timeout=5)
    except Exception:
        # Cleanup must not replace the actual task result or error.
        pass


def verify_cloud_session(cdp: CDPClient) -> bool:
    """Navigate to WPS Documents and verify the authenticated read-only API."""
    try:
        cdp.send("Page.navigate", {"url": WPS_HOME_URL}, timeout=30)
        _wait_for_wps_page_ready(cdp)
        result = cdp.evaluate(
            f"""
            (async () => {{
              const target = new URL({json.dumps(f"https://{API_HOST}{WPS_DOCUMENT_SEARCH_PATH}")});
              target.searchParams.set('offset', '0');
              target.searchParams.set('count', '1');
              target.searchParams.set('searchname', '');
              try {{
                const response = await fetch(target.toString(), {{method:'GET', credentials:'include', redirect:'follow', cache:'no-store', headers:{{'Accept':'application/json'}}}});
                const contentType = response.headers.get('content-type') || '';
                let payload = null;
                if (/application\/json/i.test(contentType)) {{
                  try {{ payload = await response.json(); }} catch (_) {{}}
                }}
                return {{status: response.status, contentType, payload}};
              }} catch (_) {{
                return {{status: 0, contentType: '', payload: null}};
              }}
            }})()
            """,
            timeout=30,
        )
    except Exception:
        return False
    if not isinstance(result, Mapping):
        return False
    status = int(result.get("status") or 0)
    content_type = str(result.get("contentType") or "")
    payload = result.get("payload")
    return 200 <= status < 300 and "application/json" in content_type.lower() and isinstance(payload, Mapping)


def save_auth_state(cdp: CDPClient, auth_file: str | Path | None = None) -> dict[str, Any]:
    cdp.send("Network.enable")
    response = cdp.send("Network.getAllCookies", timeout=20)
    cookies = filter_auth_cookies(response.get("result", {}).get("cookies", []))
    cookie_names = {str(cookie.get("name") or "") for cookie in cookies}
    if not cookie_names.intersection(AUTH_SESSION_COOKIE_NAMES):
        raise ExportError("未找到 WPS 登录凭证，请确认已在登录浏览器中进入“WPS 文档”后重试。")
    if not verify_cloud_session(cdp):
        raise ExportError("WPS 登录会话未通过文档接口验证，请确认登录完成后重试。")
    target = Path(auth_file).expanduser().resolve() if auth_file else default_auth_path()
    write_private_json(target, {"version": 1, "cookies": cookies})
    return {"cookieCount": len(cookies), "authFile": str(target)}


def load_auth_state(cdp: CDPClient, auth_file: str | Path | None = None) -> int:
    target = Path(auth_file).expanduser().resolve() if auth_file else default_auth_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExportError("无法读取 WPS 登录状态，请重新登录。") from exc
    cookies = filter_auth_cookies(payload.get("cookies", []))
    if not cookies:
        raise ExportError("WPS 登录状态中没有受支持的认证 Cookie。")
    cdp.send("Network.enable")
    cdp.send("Network.setCookies", {"cookies": cookies}, timeout=30)
    if not verify_cloud_session(cdp):
        raise ExportError("WPS 登录状态已失效，请重新登录。")
    return len(cookies)


def _inside_wps_data(path: Path) -> Path:
    root = wps_data_root().resolve()
    target = path.expanduser().resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("拒绝清理 WPS 插件数据目录之外的路径") from exc
    if target == root:
        raise ValueError("拒绝清理整个 WPS 插件数据目录")
    return target


def clear_auth_state(auth_file: str | Path | None = None, profile_dir: str | Path | None = None) -> dict[str, Any]:
    auth = _inside_wps_data(Path(auth_file) if auth_file else default_auth_path())
    profile = _inside_wps_data(Path(profile_dir) if profile_dir else default_profile_path())
    removed = False
    if auth.exists():
        auth.unlink()
        removed = True
    if profile.exists():
        try:
            shutil.rmtree(profile)
        except PermissionError as exc:
            raise ExportError("WPS 浏览器配置目录仍被占用，请关闭相关浏览器后重试。") from exc
        removed = True
    return {"cleared": removed}


class CDPJSONTransport:
    def __init__(self, cdp: CDPClient) -> None:
        self.cdp = cdp

    def request_json(
        self,
        method: str,
        url: str,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        method = method.upper()
        if method not in {"GET", "POST"}:
            raise ValueError("WPS 数据源只允许 GET 或只读查询 POST 请求")
        target = validate_api_url(url)
        encoded_url = json.dumps(target, ensure_ascii=False)
        encoded_params = json.dumps(dict(params or {}), ensure_ascii=False)
        encoded_body = json.dumps(dict(body or {}), ensure_ascii=False)
        result = self.cdp.evaluate(
            f"""
            (async () => {{
              const target = new URL({encoded_url});
              for (const [key, value] of Object.entries({encoded_params})) if (value !== null && value !== undefined && value !== '') target.searchParams.set(key, String(value));
              const csrf = document.cookie.split(';').map(part => part.trim()).find(part => /^(csrf|csrf-rand|x-csrf-rand)=/i.test(part));
              const csrfValue = csrf ? decodeURIComponent(csrf.substring(csrf.indexOf('=') + 1)) : '';
              const headers = {{'Accept': 'application/json'}};
              if (csrfValue) headers['x-csrf-rand'] = csrfValue;
              const options = {{method: {json.dumps(method)}, credentials:'include', redirect:'follow', cache:'no-store', headers}};
              if ({json.dumps(method)} === 'POST') {{
                headers['Content-Type'] = 'application/json';
                options.body = JSON.stringify({encoded_body});
              }}
              try {{
                const response = await fetch(target.toString(), options);
                let payload = {{}}; try {{ payload = await response.json(); }} catch (_) {{}}
                return {{status: response.status, headers: {{'Retry-After': response.headers.get('Retry-After') || ''}}, payload}};
              }} catch (_) {{ return {{status: 0, headers: {{}}, payload: {{}}}}; }}
            }})()
            """,
            timeout=60,
        )
        if not isinstance(result, Mapping):
            raise WPSApiError("WPS API 返回了无效响应")
        return result


class WPSDocumentDataSource:
    """Read-only data source for downloadable WPS cloud documents."""

    source_label = "WPS 文档"

    def __init__(self, *, transport: WPSJSONTransport, request_delay: float = 0.0, sleep: Callable[[float], None] | None = None) -> None:
        self.transport = transport
        self.request_delay = max(0.0, float(request_delay))
        self.sleep = sleep

    def _url(self, path: str) -> str:
        if not path.startswith("/") or "?" in path or "#" in path:
            raise ValueError("WPS API path must be explicit and query-free")
        return validate_api_url(f"https://{API_HOST}{path}")

    def _request(
        self,
        method: str,
        url: str,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        if self.request_delay and self.sleep:
            self.sleep(self.request_delay)
        raw = self.transport.request_json(method, url, params, body)
        if not isinstance(raw, Mapping):
            raise WPSApiError("WPS API 返回了无效响应")
        status = int(raw.get("status") or 200)
        headers = raw.get("headers") if isinstance(raw.get("headers"), Mapping) else {}
        retry = _retry_after(headers.get("Retry-After"))
        if status in (401, 403):
            raise WPSAuthExpiredError("WPS 登录会话已过期", status=status)
        if status == 429:
            raise WPSRateLimitError("WPS 请求频率受限", status=status, retry_after=retry)
        if status < 200 or status >= 300:
            raise WPSApiError(f"WPS API 请求失败 HTTP {status}", status=status, retry_after=retry)
        payload = raw.get("payload", raw.get("data", {}))
        return payload if isinstance(payload, Mapping) else {"data": payload}

    def get_root(self) -> dict[str, Any]:
        return {
            "id": WPS_DOCUMENT_ROOT_ID,
            "file_id": "",
            "title": "WPS 文档",
            "parent_id": None,
            "type": "folder",
        }

    def list_children(self, parent_id: str, cursor: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
        if parent_id != WPS_DOCUMENT_ROOT_ID:
            return [], None
        state = _decode_cursor(cursor)
        offset = int(state.get("offset", 0) or 0)
        params: dict[str, Any] = {
            "offset": offset,
            "count": 100,
            "sort_by": "modify_time",
            "order": "desc",
            "searchname": "",
        }
        payload = self._request("GET", self._url(WPS_DOCUMENT_SEARCH_PATH), params)
        items: list[dict[str, Any]] = []
        for item in _items(payload):
            if _is_exportable_document(item):
                items.append(_normalize_document_item(item, parent_id))
        next_data = payload.get("next") if isinstance(payload.get("next"), Mapping) else {}
        raw_next = payload.get("next_offset", next_data.get("offset"))
        has_more = payload.get("has_more", next_data.get("has_more"))
        try:
            next_offset = int(raw_next) if raw_next not in (None, "") else None
        except (TypeError, ValueError):
            next_offset = None
        if next_offset is None or next_offset <= offset or has_more is False:
            return items, None
        return items, _encode_cursor({"offset": next_offset})

    def open_download(self, file_id: str) -> str:
        safe_id = urllib.parse.quote(str(file_id), safe="")
        payload = _unwrap(self._request(
            "GET",
            self._url(WPS_DOCUMENT_DOWNLOAD_PATH.format(file_id=safe_id)),
            {"isblocks": "false"},
        ))
        url = payload.get("url") or payload.get("download_url") or payload.get("downloadUrl")
        if not isinstance(url, str) or not url.strip():
            raise WPSApiError("WPS 文档未返回可下载的原始文件地址")
        return validate_download_url(url)

    def query_content(self, file_id: str) -> Mapping[str, Any]:
        safe_id = urllib.parse.quote(str(file_id), safe="")
        return self._request(
            "POST",
            self._url(WPS_DOCUMENT_EXECUTE_PATH.format(file_id=safe_id)),
            body={
                "command": "http.otl.query",
                "param": {"name": "block.query", "params": {"blockIds": ["doc"]}},
            },
        )


# Kept as a private compatibility alias for callers importing the old class name;
# all production paths now use the Smart Document source above.
WPSApiDataSource = WPSDocumentDataSource
WPSSmartDocumentDataSource = WPSDocumentDataSource


def _retry_after(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _encode_cursor(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode_cursor(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    result = json.loads(value)
    if not isinstance(result, Mapping):
        raise ValueError("WPS 分页游标无效")
    return dict(result)


def _unwrap(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    current: Any = payload
    for _ in range(3):
        if isinstance(current, Mapping) and isinstance(current.get("data"), Mapping):
            current = current["data"]
        else:
            break
    return current if isinstance(current, Mapping) else {}


def _items(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    current = _unwrap(payload)
    for key in ("files", "items", "nodes", "children"):
        value = current.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
    return []


def _text(item: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    raise ValueError(f"remote item is missing {keys[0]}")


def _title(item: Mapping[str, Any], fallback: str, identifier: str) -> str:
    for key in ("title", "name", "filename", "file_name", "fname", "display_name", "displayName"):
        value = item.get(key)
        if isinstance(value, Mapping):
            value = value.get("text") or value.get("value")
        if value is not None and str(value).strip():
            return str(value).strip()
    return f"{fallback}-{identifier}" if identifier else fallback


def _is_exportable_document(item: Mapping[str, Any]) -> bool:
    """Return whether a search result is a downloadable cloud document.

    The search endpoint can mix files, folders, device-space entries, and
    recycled items. Only records with a file identifier and no explicit local
    or non-exportable marker are allowed into the export tree.
    """
    identifier = item.get("fileid") or item.get("file_id") or item.get("id")
    if identifier is None or not str(identifier).strip():
        return False

    values = {
        str(item.get(key) or "").strip().lower()
        for key in (
            "filetype", "file_type", "sub_type", "subType",
            "kind", "type", "location", "space_type", "spaceType",
            "status",
        )
    }
    if values.intersection({
        "folder", "directory", "dir",
        "device", "my_device", "my-device", "local",
        "trash", "recycle", "recycle_bin", "recycle-bin",
    }):
        return False
    if any(item.get(key) is True for key in ("is_folder", "is_dir", "folder", "is_device", "is_local", "in_trash")):
        return False
    return True


def _normalize_document_item(item: Mapping[str, Any], parent_id: str | None) -> dict[str, Any]:
    identifier = _text(item, "fileid", "file_id", "id")
    title = _title(item, "未命名文档", identifier)
    raw_size = item.get("size", item.get("file_size"))
    try:
        size = None if raw_size in (None, "") else int(raw_size)
    except (TypeError, ValueError):
        size = None
    return {
        "id": str(identifier),
        "file_id": str(identifier),
        "title": title,
        "parent_id": str(parent_id) if parent_id not in (None, "") else None,
        "type": "file",
        "size": size,
        "mtime": item.get("mtime", item.get("modify_time", item.get("modified_time"))),
    }


def _normalize_api_item(item: Mapping[str, Any], parent_id: str | None) -> dict[str, Any]:
    identifier = _text(item, "id", "file_id", "fileid")
    raw_type = str(item.get("type") or item.get("kind") or "").lower()
    folder = bool(item.get("is_folder", item.get("is_dir", item.get("folder")))) or raw_type in {"folder", "directory", "dir", "special"}
    parent = item.get("parent_id", item.get("parentid", item.get("parent"))) or parent_id
    raw_size = item.get("size", item.get("file_size"))
    try:
        size = None if raw_size in (None, "") else int(raw_size)
    except (TypeError, ValueError):
        size = None
    return {
        "id": str(identifier),
        "file_id": "" if folder else str(item.get("file_id") or item.get("fileid") or identifier),
        "title": _title(item, "未命名文件", str(identifier)),
        "parent_id": str(parent) if parent not in (None, "") else None,
        "type": "folder" if folder else "file",
        "size": size,
        "mtime": item.get("mtime", item.get("modified_time")),
    }


def normalize_remote_item(item: Mapping[str, Any], parent_id: str | None) -> WPSNode:
    normalized = _normalize_api_item(item, parent_id)
    return WPSNode(**normalized)


def scan_tree(source: WPSDocumentDataSource, root: Mapping[str, Any], max_depth: int = 64) -> list[WPSNode]:
    result: list[WPSNode] = []
    seen: set[str] = set()

    def visit(raw: Mapping[str, Any], depth: int) -> None:
        if depth > max_depth:
            raise ValueError("WPS 扫描深度超过限制")
        node = normalize_remote_item(raw, raw.get("parent_id")) if depth == 0 else normalize_remote_item(raw, raw.get("parent_id"))
        if node.id in seen:
            raise ValueError("WPS 返回了重复的文件 ID")
        seen.add(node.id)
        result.append(node)
        if node.type != "folder":
            return
        cursor = None
        while True:
            children, cursor = source.list_children(node.id, cursor)
            for child in children:
                visit(child, depth + 1)
            if cursor is None:
                return

    visit(root, 0)
    return result


def sanitize_component(value: str, fallback: str = "未命名文件") -> str:
    cleaned = "".join("_" if ord(ch) < 32 or ch in FORBIDDEN_PATH_CHARS else ch for ch in str(value))
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(". ")
    if not cleaned:
        cleaned = fallback
    if cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        cleaned = "_" + cleaned
    return cleaned[:120].rstrip(". ") or fallback


def safe_target(root: Path, parts: Sequence[str], used: set[str]) -> Path:
    root = root.resolve()
    safe_parts = [sanitize_component(part) for part in parts]
    candidate = root.joinpath(*safe_parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("WPS 目标路径不安全") from exc
    key = str(candidate).casefold()
    if key in used or candidate.exists():
        stem, suffix = candidate.stem, candidate.suffix
        digest = hashlib.sha256("/".join(parts).encode("utf-8")).hexdigest()[:8]
        candidate = candidate.with_name(f"{stem}-{digest}{suffix}")
    used.add(str(candidate).casefold())
    return candidate


def download_original_file(url: str, target: Path) -> Path:
    checked = validate_download_url(url)
    target = Path(target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".part")
    try:
        request = urllib.request.Request(checked, method="GET", headers={"Accept": "*/*"})
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as handle:
            shutil.copyfileobj(response, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


class WPSExportTask:
    def __init__(self, source: WPSDocumentDataSource, output: Path, *, checkpoint=None, report_file: Path | None = None) -> None:
        self.source = source
        self.output = Path(output).resolve()
        self.checkpoint = checkpoint
        self.report_file = report_file or self.output / "00-导出报告.json"

    def scan(self) -> list[WPSNode]:
        return scan_tree(self.source, self.source.get_root())

    def export(self, nodes: Sequence[WPSNode], selected: Sequence[str] | None = None, retry_failed: bool = False) -> dict[str, Any]:
        self.output.mkdir(parents=True, exist_ok=True)
        by_id = {node.id: node for node in nodes}
        files = [node for node in nodes if node.type == "file" and node.file_id]
        wanted = {str(value) for value in (selected or []) if str(value)}
        if wanted:
            files = [node for node in files if node.file_id in wanted]
        if self.checkpoint:
            self.checkpoint.start_task({"source": "WPS 文档", "outputDir": str(self.output)})
            for node in files:
                self.checkpoint.upsert_item(node.file_id, title=node.title, source_id=node.file_id, parent_key=node.parent_id or "")
        used: set[str] = set()
        success = skipped = 0
        failures: list[dict[str, str]] = []
        try:
            for node in files:
                check_stopped(None)
                if self.checkpoint:
                    status = self.checkpoint.item_status(node.file_id)
                    if status == "completed" or (retry_failed and status != "failed"):
                        skipped += 1
                        continue
                    self.checkpoint.start_item(node.file_id, "download")
                parents: list[str] = []
                current = node.parent_id
                chain: list[str] = []
                while current and current in by_id:
                    parent = by_id[current]
                    if parent.type == "folder":
                        chain.append(parent.title)
                    current = parent.parent_id
                parents.extend(reversed(chain))
                target = safe_target(self.output, [*parents, node.title], used)
                try:
                    url = self.source.open_download(node.file_id)
                    if target.exists():
                        skipped += 1
                        if self.checkpoint:
                            self.checkpoint.complete_item(node.file_id, str(target))
                        continue
                    download_original_file(url, target)
                    success += 1
                    if self.checkpoint:
                        self.checkpoint.complete_item(node.file_id, str(target))
                except Exception as exc:
                    message = safe_error_text(exc)
                    failures.append({"file": node.title, "error": message})
                    if self.checkpoint:
                        self.checkpoint.fail_item(node.file_id, message)
            report = {"totalDocs": len(files), "successCount": success, "skippedCount": skipped, "failureCount": len(failures), "failures": failures, "output": str(self.output)}
            if self.checkpoint:
                self.checkpoint.complete_task(report)
            return finalize_report(report, provider="wps-export", mode="export", report_file=self.report_file, output=self.output)
        except ExportStopped:
            report = {"totalDocs": len(files), "successCount": success, "skippedCount": skipped, "failureCount": len(failures), "failures": failures, "stopped": True, "output": str(self.output)}
            if self.checkpoint:
                self.checkpoint.fail_task("任务因停止请求而停止", status="stopped")
            return finalize_report(report, provider="wps-export", mode="export", report_file=self.report_file, output=self.output)


def _write_report(report: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def login_wps(args: argparse.Namespace) -> dict[str, Any]:
    cdp, process = connect_wps_browser(args)
    try:
        print("WPS 登录页面已打开。", file=sys.stderr, flush=True)
        print("请在浏览器中完成登录，确认回到“WPS 文档”后按 Enter。", file=sys.stderr, flush=True)
        sys.stdin.readline()
        saved = save_auth_state(cdp, getattr(args, "auth_file", None))
        return {"provider": "wps-export", "status": "authenticated", "cookieCount": int(saved["cookieCount"])}
    finally:
        close_owned_browser(cdp, process)


def scan_wps(args: argparse.Namespace) -> dict[str, Any]:
    cdp, process = connect_wps_browser(args)
    try:
        load_auth_state(cdp, getattr(args, "auth_file", None))
        source = WPSDocumentDataSource(transport=CDPJSONTransport(cdp), request_delay=args.request_delay, sleep=time.sleep)
        return {"nodes": [asdict(node) for node in scan_tree(source, source.get_root())]}
    finally:
        close_owned_browser(cdp, process)


def export_wps(args: argparse.Namespace) -> dict[str, Any]:
    output = Path(args.output).expanduser().resolve()
    checkpoint = open_checkpoint_from_args(args, "wps-export", "export")
    cdp = process = None
    try:
        cdp, process = connect_wps_browser(args)
        load_auth_state(cdp, getattr(args, "auth_file", None))
        source = WPSDocumentDataSource(transport=CDPJSONTransport(cdp), request_delay=args.request_delay, sleep=time.sleep)
        task = WPSExportTask(source, output, checkpoint=checkpoint)
        result = task.export(task.scan(), args.selected_file_ids, args.retry_failed)
        _write_report(result, task.report_file)
        return result
    finally:
        close_owned_browser(cdp, process)
        if checkpoint:
            checkpoint.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export original files from WPS Smart Documents.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--login", action="store_true")
    modes.add_argument("--scan-toc", action="store_true")
    modes.add_argument("--clear-auth", action="store_true")
    parser.add_argument("--port", type=int, default=WPS_DEBUG_PORT, help=argparse.SUPPRESS)
    parser.add_argument("--profile-dir", help=argparse.SUPPRESS)
    parser.add_argument("--browser-path", help=argparse.SUPPRESS)
    parser.add_argument("--auth-file", help=argparse.SUPPRESS)
    parser.add_argument("--output", default="exports/wps")
    parser.add_argument("--file-id", dest="selected_file_ids", action="append", default=[])
    parser.add_argument("--request-delay", type=float, default=0.1)
    add_checkpoint_args(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.clear_auth:
            result = clear_auth_state(args.auth_file, args.profile_dir)
        elif args.login:
            result = login_wps(args)
        elif args.scan_toc:
            result = scan_wps(args)
        else:
            result = export_wps(args)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except ExportStopped:
        print(json.dumps({"stopped": True}, ensure_ascii=False), file=sys.stdout)
        return 130
    except Exception as exc:
        print(f"Error: {safe_error_text(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
