#!/usr/bin/env python3
"""WPS document original-file exporter.

The provider reads downloadable WPS cloud documents, including Smart Documents, while excluding device/local documents. It deliberately keeps
the desktop contract small: login, scan, export, and clear-auth. Browser cookies
stay in an isolated profile and every command writes exactly one JSON result to
stdout; human-readable prompts/errors use stderr.
"""
from __future__ import annotations

import argparse
import base64
import binascii
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
    emit,
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
DRIVE_API_HOST = "drive.wps.cn"
WPS_DOCUMENT_ROOT_ID = "smart-documents"
WPS_DOCUMENT_SEARCH_PATH = "/3rd/drive/api/v6/search/files"
WPS_DOCUMENT_DOWNLOAD_PATH = "/api/v3/office/file/{file_id}/download"
WPS_GROUP_DOWNLOAD_PATH = "/api/v3/groups/{group_id}/files/{file_id}/download"
WPS_DOCUMENT_EXECUTE_PATH = "/api/v3/office/file/{file_id}/core/execute"
AUTH_SESSION_COOKIE_NAMES = frozenset({"wps_sid", "kso_sid"})
AUTH_REQUEST_COOKIE_NAMES = frozenset({"csrf"})
AUTH_COOKIE_NAMES = AUTH_SESSION_COOKIE_NAMES | AUTH_REQUEST_COOKIE_NAMES
AUTH_COOKIE_FIELDS = frozenset({
    "name", "value", "domain", "path", "secure", "httpOnly", "sameSite", "expires"
})
API_HOSTS = frozenset({"365.kdocs.cn", "drive.kdocs.cn", "drive.wps.cn", "www.kdocs.cn", "docs.wps.cn", "account.kdocs.cn", "account.wps.cn"})
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
    group_id: str | None = None
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
    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        retry_after: float | None = None,
        result_code: str = "",
    ) -> None:
        super().__init__(safe_error_text(message))
        self.status = int(status)
        self.retry_after = retry_after
        self.result_code = safe_error_text(result_code)[:100]


class WPSAuthExpiredError(WPSApiError):
    pass


class WPSRateLimitError(WPSApiError):
    pass


class WPSDownloadUnavailableError(WPSApiError):
    def __init__(self, message: str, *, allow_content_query: bool = False, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.allow_content_query = bool(allow_content_query)


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


def disconnect_browser(cdp: CDPClient | None) -> None:
    """Detach from CDP without terminating the WPS browser window."""
    if cdp is None:
        return
    try:
        cdp.close()
    except Exception:
        pass


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
              const method = {json.dumps(method)};
              const headers = {{'Accept': 'application/json'}};
              const options = {{method, credentials:'include', redirect:'follow', cache:'no-store', headers}};
              if (method === 'POST') {{
                const csrf = document.cookie.split(';').map(part => part.trim()).find(part => /^(csrf|csrf-rand|x-csrf-rand)=/i.test(part));
                let csrfValue = '';
                if (csrf) {{
                  const rawValue = csrf.substring(csrf.indexOf('=') + 1);
                  try {{ csrfValue = decodeURIComponent(rawValue); }} catch (_) {{ csrfValue = rawValue; }}
                }}
                if (csrfValue) headers['x-csrf-rand'] = csrfValue;
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

    def _url(self, path: str, *, host: str = API_HOST) -> str:
        if not path.startswith("/") or "?" in path or "#" in path:
            raise ValueError("WPS API path must be explicit and query-free")
        return validate_api_url(f"https://{host}{path}")

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
        raw_status = raw.get("status")
        try:
            status = 200 if raw_status is None else int(raw_status)
        except (TypeError, ValueError) as exc:
            raise WPSApiError("WPS API returned an invalid HTTP status") from exc
        headers = raw.get("headers") if isinstance(raw.get("headers"), Mapping) else {}
        if status == 0:
            raise WPSApiError("WPS API 网络请求失败", status=0)
        retry = _retry_after(headers.get("Retry-After"))
        raw_payload = raw.get("payload", raw.get("data", {}))
        payload = raw_payload if isinstance(raw_payload, Mapping) else {"data": raw_payload}
        result_code = _response_error_code(payload)
        if status == 401:
            raise WPSAuthExpiredError("WPS 登录会话已过期", status=status, result_code=result_code)
        if status == 403:
            raise WPSApiError("WPS API 拒绝了当前请求", status=status, result_code=result_code)
        if status == 429:
            raise WPSRateLimitError("WPS 请求频率受限", status=status, retry_after=retry, result_code=result_code)
        if status < 200 or status >= 300:
            raise WPSApiError(
                f"WPS API 请求失败 HTTP {status}",
                status=status,
                retry_after=retry,
                result_code=result_code,
            )
        return payload

    def _raise_forbidden_download(self, exc: WPSApiError) -> None:
        """Distinguish an expired session from a file-specific 403 response."""
        try:
            self._request(
                "GET",
                self._url(WPS_DOCUMENT_SEARCH_PATH),
                {"offset": 0, "count": 1, "searchname": ""},
            )
        except WPSAuthExpiredError:
            raise
        except WPSApiError as verify_exc:
            if verify_exc.status in {401, 403}:
                raise WPSAuthExpiredError("WPS 登录会话已过期", status=verify_exc.status) from verify_exc
            raise WPSDownloadUnavailableError(
                "WPS 暂时无法确认当前文件的下载权限，请稍后重试",
                status=exc.status,
            ) from exc
        raise WPSDownloadUnavailableError(
            "WPS 当前文件没有下载权限或暂不支持原始文件导出",
            status=exc.status,
        ) from exc

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
        try:
            payload = self._request("GET", self._url(WPS_DOCUMENT_SEARCH_PATH), params)
        except WPSApiError as exc:
            if exc.status == 403:
                raise WPSAuthExpiredError("WPS 登录会话已过期", status=exc.status) from exc
            raise
        raw_items = _items(payload)
        items: list[dict[str, Any]] = []
        for item in raw_items:
            if _is_exportable_document(item):
                items.append(_normalize_document_item(item, parent_id))
        next_data = payload.get("next") if isinstance(payload.get("next"), Mapping) else {}
        raw_next = payload.get("next_offset", next_data.get("offset"))
        has_more = payload.get("has_more", next_data.get("has_more"))
        try:
            next_offset = int(raw_next) if raw_next not in (None, "") else None
        except (TypeError, ValueError):
            next_offset = None
        if next_offset is None and raw_items:
            try:
                total = int(payload.get("total"))
            except (TypeError, ValueError):
                total = 0
            candidate = offset + len(raw_items)
            if total > candidate:
                next_offset = candidate
        if next_offset is None or next_offset <= offset or has_more is False:
            return items, None
        return items, _encode_cursor({"offset": next_offset})

    def open_download(self, file_id: str, group_id: str | None = None) -> str:
        safe_id = urllib.parse.quote(str(file_id), safe="")
        try:
            payload = _unwrap(self._request(
                "GET",
                self._url(WPS_DOCUMENT_DOWNLOAD_PATH.format(file_id=safe_id)),
                {"isblocks": "false"},
            ))
        except WPSAuthExpiredError:
            raise
        except WPSApiError as exc:
            code = exc.result_code.casefold()
            if exc.status == 403 and code == "unsupport":
                if not group_id:
                    raise WPSDownloadUnavailableError(
                        "WPS 智慧文档没有可下载的原始文件，将尝试导出为 Markdown",
                        status=exc.status,
                        allow_content_query=True,
                    ) from exc
                return self._open_group_download(safe_id, group_id)
            if exc.status == 403 and code == "fileuploadnotcomplete":
                raise WPSDownloadUnavailableError(
                    "WPS 文件尚未上传完成，暂时无法导出",
                    status=exc.status,
                ) from exc
            if exc.status == 403:
                self._raise_forbidden_download(exc)
            if type(exc) is WPSApiError and exc.status in {400, 404, 405, 422}:
                raise WPSDownloadUnavailableError(
                    "WPS 文档没有可下载的原始文件，将尝试导出为 Markdown",
                    status=exc.status,
                    allow_content_query=True,
                ) from exc
            raise
        return _download_url_from_payload(payload)

    def _open_group_download(self, safe_file_id: str, group_id: str) -> str:
        safe_group_id = urllib.parse.quote(str(group_id), safe="")
        try:
            payload = _unwrap(self._request(
                "GET",
                self._url(
                    WPS_GROUP_DOWNLOAD_PATH.format(group_id=safe_group_id, file_id=safe_file_id),
                    host=DRIVE_API_HOST,
                ),
            ))
        except WPSAuthExpiredError:
            raise
        except WPSApiError as exc:
            code = exc.result_code.casefold()
            if exc.status == 403 and code == "notallowtype":
                raise WPSDownloadUnavailableError(
                    "WPS 智慧文档没有可下载的原始文件，将尝试导出为 Markdown",
                    status=exc.status,
                    allow_content_query=True,
                ) from exc
            if exc.status == 403 and code == "errforbiddownloadlinkfile":
                raise WPSDownloadUnavailableError(
                    "WPS 在线智能表格暂不支持原始文件导出",
                    status=exc.status,
                ) from exc
            if exc.status == 403 and code in {"errnotsupportckt", "fileuploadnotcomplete"}:
                raise WPSDownloadUnavailableError(
                    "WPS 文件上传未完成或当前类型不支持原始文件导出",
                    status=exc.status,
                ) from exc
            if exc.status == 403:
                self._raise_forbidden_download(exc)
            raise
        return _download_url_from_payload(payload)

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


def _decode_smart_document_result(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the decoded AirPage block result without exposing raw response data."""
    current: Any = _unwrap(payload)
    detail = current.get("detail") if isinstance(current, Mapping) else None
    if isinstance(detail, Mapping) and detail.get("result") not in (None, ""):
        current = detail.get("result")
    elif isinstance(current, Mapping) and current.get("result") not in (None, "", "ok"):
        current = current.get("result")

    if isinstance(current, str):
        encoded = current.strip()
        try:
            encoded += "=" * (-len(encoded) % 4)
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
            current = json.loads(decoded)
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WPSApiError("WPS 智慧文档内容解码失败") from exc

    for _ in range(4):
        if not isinstance(current, Mapping):
            break
        if isinstance(current.get("blocks"), list):
            return current
        nested = current.get("data") or current.get("result")
        if not isinstance(nested, Mapping):
            break
        current = nested
    raise WPSApiError("WPS 智慧文档未返回可识别的内容块")


def _inline_markdown(nodes: Any) -> str:
    if not isinstance(nodes, list):
        return ""
    output: list[str] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        node_type = str(node.get("type") or "")
        attrs = node.get("attrs") if isinstance(node.get("attrs"), Mapping) else {}
        if node_type == "text":
            value = str(node.get("content") or "")
            if attrs.get("code") or attrs.get("inlineCode"):
                value = "`" + value.replace("`", "\\`") + "`"
            if attrs.get("bold"):
                value = f"**{value}**"
            if attrs.get("italic"):
                value = f"*{value}*"
            if attrs.get("strike") or attrs.get("strikethrough"):
                value = f"~~{value}~~"
            link = attrs.get("link") or attrs.get("href") or attrs.get("url")
            if isinstance(link, str) and link.startswith(("https://", "http://")):
                value = f"[{value}]({link.replace(')', '%29')})"
            output.append(value)
        elif node_type == "emoji":
            output.append(str(attrs.get("emoji") or node.get("content") or ""))
        elif node_type == "br":
            output.append("  \n")
        elif node_type == "latex":
            output.append(f"${attrs.get('latexStr') or ''}$")
        elif node_type in {"linkView", "WPSDocument"}:
            label = str(attrs.get("title") or attrs.get("wpsDocumentName") or "链接")
            url = attrs.get("url") or attrs.get("wpsDocumentLink")
            output.append(f"[{label}]({str(url).replace(')', '%29')})" if isinstance(url, str) and url.startswith(("https://", "http://")) else label)
        elif node_type == "WPSUser":
            output.append("@" + str(attrs.get("name") or "用户"))
        elif isinstance(node.get("content"), str):
            output.append(str(node.get("content")))
    return "".join(output)


_CODE_LANGUAGES = {
    1: "text", 2: "css", 3: "go", 4: "python", 5: "shell", 7: "markdown",
    12: "typescript", 13: "sql", 15: "http", 16: "java", 17: "json",
    19: "javascript", 26: "csharp", 27: "dockerfile", 32: "rust", 43: "yaml",
}


def _block_markdown(
    block: Mapping[str, Any],
    resource_renderer: Callable[[Mapping[str, Any]], str | None] | None = None,
) -> list[str]:
    block_type = str(block.get("type") or "")
    attrs = block.get("attrs") if isinstance(block.get("attrs"), Mapping) else {}
    content = block.get("content")
    inline = _inline_markdown(content)

    if block_type == "doc":
        return _blocks_markdown(content, resource_renderer)
    if block_type == "title":
        return [f"# {inline}" if inline else ""]
    if block_type == "heading":
        try:
            level = min(6, max(1, int(attrs.get("level", 1))))
        except (TypeError, ValueError):
            level = 1
        return [f"{'#' * level} {inline}" if inline else ""]
    if block_type == "paragraph":
        list_attrs = attrs.get("listAttrs") if isinstance(attrs.get("listAttrs"), Mapping) else {}
        try:
            level = max(0, int(list_attrs.get("level", 0)))
        except (TypeError, ValueError):
            level = 0
        prefix = ""
        if list_attrs:
            list_type = list_attrs.get("type")
            if list_type == 2:
                prefix = "1. "
            elif list_type == 3:
                prefix = "- [x] " if list_attrs.get("styleType") == 8 else "- [ ] "
            else:
                prefix = "- "
        return [f"{'  ' * level}{prefix}{inline}" if inline or prefix else ""]
    if block_type == "blockQuote":
        return ["\n".join(f"> {line}" for line in inline.splitlines()) if inline else ">"]
    if block_type == "codeBlock":
        try:
            language = _CODE_LANGUAGES.get(int(attrs.get("lang", 1)), "text")
        except (TypeError, ValueError):
            language = "text"
        return [f"```{language}\n{inline}\n```"]
    if block_type == "hr":
        return ["---"]
    if block_type in {"picture", "image", "attachment", "file", "thirdResource"}:
        rendered_resource = resource_renderer(block) if resource_renderer else None
        if rendered_resource:
            return [rendered_resource]
        if block_type in {"picture", "image"}:
            return [f"[图片：{attrs.get('caption') or attrs.get('title') or '未命名图片'}]"]
        return [f"[{attrs.get('title') or attrs.get('name') or '嵌入内容'}]"]
    if block_type in {"video", "audio", "spreadsheet", "dbsheet", "processon"}:
        return [f"[{attrs.get('title') or '嵌入内容'}]"]
    if isinstance(content, list):
        nested = _blocks_markdown(content, resource_renderer)
        if nested:
            return nested
    return [inline] if inline else []


def _blocks_markdown(
    blocks: Any,
    resource_renderer: Callable[[Mapping[str, Any]], str | None] | None = None,
) -> list[str]:
    if not isinstance(blocks, list):
        return []
    rendered: list[str] = []
    for block in blocks:
        if isinstance(block, Mapping):
            rendered.extend(part for part in _block_markdown(block, resource_renderer) if part != "")
    return rendered


_RESOURCE_URL_KEYS = (
    "downloadUrl", "download_url", "originalUrl", "original_url", "originUrl",
    "imageUrl", "image_url", "fileUrl", "file_url", "src", "url",
)
_RESOURCE_CONTAINER_KEYS = ("image", "file", "attachment", "resource", "source", "data", "meta")
_RESOURCE_TITLE_KEYS = ("caption", "title", "fileName", "filename", "name", "displayName")


def _resource_value(mapping: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _find_resource_url(value: Any, depth: int = 0) -> str | None:
    if depth > 3 or not isinstance(value, Mapping):
        return None
    for key in _RESOURCE_URL_KEYS:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip().startswith("https://"):
            return candidate.strip()
    for key in _RESOURCE_CONTAINER_KEYS:
        nested = value.get(key)
        if isinstance(nested, Mapping):
            candidate = _find_resource_url(nested, depth + 1)
            if candidate:
                return candidate
        elif isinstance(nested, list):
            for item in nested:
                candidate = _find_resource_url(item, depth + 1)
                if candidate:
                    return candidate
    return None


_IMAGE_RESOURCE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif"}


def _resource_suffix(url: str, title: str, kind: str) -> str:
    url_name = ""
    try:
        url_name = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
    except ValueError:
        pass
    candidates = [url_name, title] if kind == "image" else [title, url_name]
    for candidate in candidates:
        suffix = Path(candidate).suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
            continue
        if kind != "image" or suffix in _IMAGE_RESOURCE_SUFFIXES:
            return suffix
    return ".png" if kind == "image" else ".bin"


def _markdown_resource_label(value: str) -> str:
    normalized = re.sub(r"\s+", " ", str(value)).strip()
    return normalized.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


class SmartDocumentResourceWriter:
    def __init__(
        self,
        markdown_target: Path,
        failures: list[dict[str, str]],
        downloader: Callable[[str, Path], Path],
    ) -> None:
        self.markdown_target = Path(markdown_target).resolve()
        self.failures = failures
        self.downloader = downloader
        self.counts = {"image": 0, "attachment": 0}

    def render(self, block: Mapping[str, Any]) -> str | None:
        block_type = str(block.get("type") or "")
        attrs = block.get("attrs") if isinstance(block.get("attrs"), Mapping) else {}
        kind = "image" if block_type in {"picture", "image"} else "attachment"
        title = _resource_value(attrs, _RESOURCE_TITLE_KEYS) or ("未命名图片" if kind == "image" else "未命名附件")
        url = _find_resource_url(attrs) or _find_resource_url(block)
        if not url:
            return None

        self.counts[kind] += 1
        index = self.counts[kind]
        suffix = _resource_suffix(url, title, kind)
        assets_name = f"{self.markdown_target.stem}.assets"
        filename = f"{kind}-{index:03d}{suffix}"
        target = self.markdown_target.parent / assets_name / filename
        try:
            self.downloader(url, target)
        except Exception as exc:
            error = safe_error_text(exc)
            self.failures.append({
                "kind": kind,
                "file": self.markdown_target.name,
                "resource": title,
                "error": error,
            })
            label = "图片" if kind == "image" else "附件"
            return f"[{label}下载失败：{title}]"

        relative = urllib.parse.quote(f"{assets_name}/{filename}", safe="/")
        markdown_title = _markdown_resource_label(title)
        return f"![{markdown_title}]({relative})" if kind == "image" else f"[{markdown_title}]({relative})"


def smart_document_to_markdown(
    payload: Mapping[str, Any],
    resource_renderer: Callable[[Mapping[str, Any]], str | None] | None = None,
) -> str:
    result = _decode_smart_document_result(payload)
    parts = _blocks_markdown(result.get("blocks"), resource_renderer)
    if not parts:
        raise WPSApiError("WPS 智慧文档内容为空或暂不支持")
    return "\n\n".join(parts).rstrip() + "\n"


def write_smart_document(
    payload: Mapping[str, Any],
    target: Path,
    *,
    resource_failures: list[dict[str, str]] | None = None,
    downloader: Callable[[str, Path], Path] | None = None,
) -> Path:
    target = Path(target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    failures = resource_failures if resource_failures is not None else []
    writer = SmartDocumentResourceWriter(target, failures, downloader or download_original_file)
    temporary = target.with_name(target.name + ".part")
    try:
        temporary.write_text(
            smart_document_to_markdown(payload, writer.render),
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def _can_fallback_to_smart_content(exc: WPSApiError) -> bool:
    return isinstance(exc, WPSDownloadUnavailableError) and exc.allow_content_query


def _response_error_code(payload: Mapping[str, Any]) -> str:
    current: Any = payload
    for _ in range(3):
        if not isinstance(current, Mapping):
            break
        for key in ("result", "code", "error", "errno"):
            value = current.get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                return safe_error_text(value)[:100]
        current = current.get("data")
    return ""


def _download_url_from_payload(payload: Mapping[str, Any]) -> str:
    fileinfo = payload.get("fileinfo") if isinstance(payload.get("fileinfo"), Mapping) else {}
    url = (
        payload.get("url")
        or payload.get("download_url")
        or payload.get("downloadUrl")
        or fileinfo.get("url")
        or fileinfo.get("static_url")
        or fileinfo.get("download_url")
    )
    if not isinstance(url, str) or not url.strip():
        raise WPSDownloadUnavailableError("WPS 文档没有可下载的原始文件")
    return validate_download_url(url)


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
    """Return whether a search result belongs to the personal cloud tree.

    Folder records are retained so exported files can preserve the parent chain.
    Device/local, team, temporary-upload, and recycled entries remain excluded.
    """
    identifier = item.get("fileid") or item.get("file_id") or item.get("id")
    if identifier is None or not str(identifier).strip():
        return False

    values = {
        str(item.get(key) or "").strip().lower()
        for key in (
            "ftype", "filetype", "file_type", "sub_type", "subType",
            "kind", "type", "location", "space_type", "spaceType",
            "status",
        )
    }
    if values.intersection({
        "device", "my_device", "my-device", "local",
        "team", "team_space", "team-space", "shared_team",
        "tmp", "temporary", "auto_upload", "auto-upload",
        "trash", "recycle", "recycle_bin", "recycle-bin",
    }):
        return False
    if any(item.get(key) is True for key in ("is_device", "is_local", "is_team", "is_tmp", "in_trash")):
        return False
    if item.get("device_info"):
        return False
    return True


def _normalize_document_item(item: Mapping[str, Any], parent_id: str | None) -> dict[str, Any]:
    identifier = _text(item, "fileid", "file_id", "id")
    title = _title(item, "未命名文档", identifier)
    raw_types = {
        str(item.get(key) or "").strip().lower()
        for key in ("ftype", "filetype", "file_type", "type", "kind")
    }
    folder = bool(item.get("is_folder", item.get("is_dir", item.get("folder")))) or bool(
        raw_types.intersection({"folder", "directory", "dir"})
    )
    raw_parent = item.get(
        "parent_id",
        item.get("parentid", item.get("parentId", item.get("parent"))),
    )
    if isinstance(raw_parent, Mapping):
        raw_parent = raw_parent.get("id") or raw_parent.get("fileid")
    normalized_parent = parent_id if raw_parent in (None, "", 0, "0") else str(raw_parent)
    raw_size = item.get("size", item.get("file_size", item.get("fsize")))
    try:
        size = None if raw_size in (None, "") else int(raw_size)
    except (TypeError, ValueError):
        size = None
    return {
        "id": str(identifier),
        "file_id": "" if folder else str(identifier),
        "title": title,
        "parent_id": str(normalized_parent) if normalized_parent not in (None, "") else None,
        "type": "folder" if folder else "file",
        "group_id": str(item.get("groupid") or item.get("group_id") or "") or None,
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
        "group_id": str(item.get("groupid") or item.get("group_id") or "") or None,
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


def parent_folder_parts(
    node: WPSNode,
    by_id: Mapping[str, WPSNode],
    max_depth: int = 64,
) -> list[str]:
    chain: list[str] = []
    seen: set[str] = set()
    current = node.parent_id
    while current and current in by_id and current not in seen and len(seen) < max_depth:
        seen.add(current)
        parent = by_id[current]
        if parent.type == "folder":
            chain.append(parent.title)
        current = parent.parent_id
    return list(reversed(chain))


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
    def __init__(
        self,
        source: WPSDocumentDataSource,
        output: Path,
        *,
        checkpoint=None,
        report_file: Path | None = None,
        args: argparse.Namespace | None = None,
    ) -> None:
        self.source = source
        self.output = Path(output).resolve()
        self.checkpoint = checkpoint
        self.report_file = report_file or self.output / "00-导出报告.json"
        self.args = args

    def scan(self) -> list[WPSNode]:
        return scan_tree(self.source, self.source.get_root())

    def _emit(self, message: str, *, event: str, level: str = "info", **fields: Any) -> None:
        if self.args is None:
            return
        emit(self.args, message, event=event, level=level, **fields)

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
        image_failures: list[dict[str, str]] = []
        attachment_failures: list[dict[str, str]] = []
        total = len(files)
        progress_every = max(1, int(getattr(self.args, "progress_every", 1) or 1))
        self._emit(
            f"开始导出 WPS 文档：共 {total} 篇。",
            event="task.started",
            totals={"documents": total},
            output=str(self.output),
        )
        try:
            for index, node in enumerate(files, start=1):
                check_stopped(None)
                doc_fields = {"id": node.file_id, "title": node.title, "index": index}
                self._emit(
                    f"开始导出 WPS 文档：{node.title}",
                    event="document.export.started",
                    doc=doc_fields,
                )
                if self.checkpoint:
                    status = self.checkpoint.item_status(node.file_id)
                    if status == "completed" or (retry_failed and status != "failed"):
                        skipped += 1
                        self._emit(
                            f"WPS 文档导出跳过：{node.title}",
                            event="document.export.completed",
                            doc=doc_fields,
                            result={"status": "skipped"},
                        )
                        if index % progress_every == 0 or index == total:
                            self._emit(
                                f"progress {index}/{total} exported={success} skipped={skipped} failures={len(failures)}",
                                event="task.progress",
                                progress={"current": index, "total": total},
                                stats={
                                    "exportedDocs": success,
                                    "skippedDocs": skipped,
                                    "failureCount": len(failures),
                                },
                            )
                        continue
                    self.checkpoint.start_item(node.file_id, "download")
                parents = parent_folder_parts(node, by_id)
                target = safe_target(self.output, [*parents, node.title], used)
                doc_fields = {**doc_fields, "path": str(target)}
                try:
                    try:
                        url = self.source.open_download(node.file_id, node.group_id)
                    except WPSApiError as exc:
                        if not _can_fallback_to_smart_content(exc):
                            raise
                        markdown_name = f"{Path(node.title).stem or node.title}.md"
                        target = safe_target(self.output, [*parents, markdown_name], used)
                        doc_fields = {**doc_fields, "path": str(target)}
                        if target.exists():
                            skipped += 1
                            if self.checkpoint:
                                self.checkpoint.complete_item(node.file_id, str(target))
                            self._emit(
                                f"WPS 文档导出跳过：{node.title}",
                                event="document.export.completed",
                                doc=doc_fields,
                                result={"status": "skipped"},
                            )
                            continue
                        resource_failures: list[dict[str, str]] = []
                        write_smart_document(
                            self.source.query_content(node.file_id),
                            target,
                            resource_failures=resource_failures,
                        )
                        for resource_failure in resource_failures:
                            if resource_failure.get("kind") == "image":
                                image_failures.append(resource_failure)
                            else:
                                attachment_failures.append(resource_failure)
                    else:
                        if target.exists():
                            skipped += 1
                            if self.checkpoint:
                                self.checkpoint.complete_item(node.file_id, str(target))
                            self._emit(
                                f"WPS 文档导出跳过：{node.title}",
                                event="document.export.completed",
                                doc=doc_fields,
                                result={"status": "skipped"},
                            )
                            continue
                        download_original_file(url, target)
                    success += 1
                    if self.checkpoint:
                        self.checkpoint.complete_item(node.file_id, str(target))
                    self._emit(
                        f"WPS 文档导出完成：{node.title}",
                        event="document.export.completed",
                        doc=doc_fields,
                        result={"status": "exported"},
                    )
                except Exception as exc:
                    message = safe_error_text(exc)
                    failures.append({"file": node.title, "error": message})
                    if self.checkpoint:
                        self.checkpoint.fail_item(node.file_id, message)
                    self._emit(
                        f"WPS 文档导出失败：{node.title}：{message}",
                        event="document.export.failed",
                        level="error",
                        doc=doc_fields,
                        error={"type": type(exc).__name__, "message": message},
                    )
                finally:
                    if index % progress_every == 0 or index == total:
                        self._emit(
                            f"progress {index}/{total} exported={success} skipped={skipped} failures={len(failures)}",
                            event="task.progress",
                            progress={"current": index, "total": total},
                            stats={
                                "exportedDocs": success,
                                "skippedDocs": skipped,
                                "failureCount": len(failures),
                            },
                        )
            report = {
                "totalDocs": total,
                "successCount": success,
                "skippedCount": skipped,
                "failureCount": len(failures),
                "failures": failures,
                "imageFailures": image_failures,
                "attachmentFailures": attachment_failures,
                "output": str(self.output),
            }
            if self.checkpoint:
                self.checkpoint.complete_task(report)
            return finalize_report(report, provider="wps-export", mode="export", report_file=self.report_file, output=self.output)
        except ExportStopped:
            report = {
                "totalDocs": total,
                "successCount": success,
                "skippedCount": skipped,
                "failureCount": len(failures),
                "failures": failures,
                "imageFailures": image_failures,
                "attachmentFailures": attachment_failures,
                "stopped": True,
                "output": str(self.output),
            }
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
        disconnect_browser(cdp)


def scan_wps(args: argparse.Namespace) -> dict[str, Any]:
    cdp, process = connect_wps_browser(args)
    try:
        load_auth_state(cdp, getattr(args, "auth_file", None))
        source = WPSDocumentDataSource(transport=CDPJSONTransport(cdp), request_delay=args.request_delay, sleep=time.sleep)
        nodes = []
        for node in scan_tree(source, source.get_root()):
            public_node = asdict(node)
            public_node.pop("group_id", None)
            nodes.append(public_node)
        return {"nodes": nodes}
    finally:
        disconnect_browser(cdp)


def export_wps(args: argparse.Namespace) -> dict[str, Any]:
    output = Path(args.output).expanduser().resolve()
    checkpoint = open_checkpoint_from_args(args, "wps-export", "export")
    cdp = process = None
    try:
        cdp, process = connect_wps_browser(args)
        load_auth_state(cdp, getattr(args, "auth_file", None))
        source = WPSDocumentDataSource(transport=CDPJSONTransport(cdp), request_delay=args.request_delay, sleep=time.sleep)
        task = WPSExportTask(source, output, checkpoint=checkpoint, args=args)
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
    parser.add_argument("--progress-every", type=int, default=1, help="每处理多少篇刷新一次进度")
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
