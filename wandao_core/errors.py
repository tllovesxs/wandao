"""Versioned, redacted error protocol shared by Wandao providers.

The protocol is deliberately additive.  Providers may continue returning a
plain ``error`` string while newer providers also attach ``errorInfo``.  The
desktop renderer performs the same normalization for legacy results.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Any


ERROR_KIND = "wandao.error"
ERROR_SCHEMA_VERSION = 1

_SENSITIVE_TEXT = re.compile(
    r"(cookie|token|secret|password|authorization|signature|access[_-]?key|api[_-]?key)"
    r"\s*([:=])\s*[^\s,&;)]+",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)


_RULES: tuple[tuple[str, str, str, str, bool, re.Pattern[str]], ...] = (
    (
        "CDP_CONNECTION_LOST",
        "browser",
        "浏览器连接中断，任务已停止",
        "请重新运行任务；已完成文档会保留，剩余文档可从断点继续。",
        True,
        re.compile(r"CDPConnectionError|CDP.*(?:连接已断开|连接中断|connection (?:lost|closed|reset))", re.IGNORECASE),
    ),
    (
        "TIMEOUT",
        "network",
        "网络请求超时",
        "请检查网络或代理设置，等待后重试。",
        True,
        re.compile(r"timeout|timed out|超时|等待.*超时|ETIMEDOUT", re.IGNORECASE),
    ),
    (
        "NETWORK_ERROR",
        "network",
        "网络连接失败",
        "请检查网络、代理或 DNS 设置后重试。",
        True,
        re.compile(r"ECONN|EHOSTUNREACH|ENETUNREACH|connection refused|connection reset|连接失败|网络错误", re.IGNORECASE),
    ),
    (
        "AUTH_REQUIRED",
        "auth",
        "登录状态可能已失效",
        "请重新登录，确认浏览器中可以打开目标页面后再重试。",
        True,
        re.compile(r"未登录|登录失效|登录凭证|cookie|cookies|login required|unauthorized|HTTP\s+401|会话.*失效", re.IGNORECASE),
    ),
    (
        "RATE_LIMITED",
        "rate_limit",
        "请求过于频繁，平台暂时限流",
        "请等待一段时间，调大请求间隔后再继续任务。",
        True,
        re.compile(r"HTTP\s+429|too many requests|rate limit|限流|请求频率|请求过快", re.IGNORECASE),
    ),
    (
        "PERMISSION_DENIED",
        "permission",
        "当前账号或应用没有访问权限",
        "请确认账号能访问目标内容，并开通平台要求的权限。",
        False,
        re.compile(r"HTTP\s+403|forbidden|permission denied|access denied|无权限|权限不足|拒绝访问", re.IGNORECASE),
    ),
    (
        "RESOURCE_DOWNLOAD_FAILED",
        "resource",
        "图片或附件处理失败",
        "正文可能已完成；可以在任务中心单独重试失败资源。",
        True,
        re.compile(
            r"图片下载失败|附件下载失败|下载.*(?:图片|附件|image|attachment)"
            r"|(?:图片|附件|资源).{0,40}(?:HTTP\s+40[134]|响应|下载失败|上传失败|处理失败|失败|错误)"
            r"|(?:image|attachment|resource).{0,40}(?:HTTP\s+40[134]|response|download|upload|fail|error)"
            r"|上传附件失败",
            re.IGNORECASE,
        ),
    ),
    (
        "NOT_FOUND",
        "not_found",
        "目标内容不存在或当前账号不可见",
        "请在浏览器确认链接有效，并检查当前账号是否仍有访问权限。",
        False,
        re.compile(r"HTTP\s+404|not found|不存在|已删除|无效.*链接", re.IGNORECASE),
    ),
    (
        "BROWSER_UNAVAILABLE",
        "browser",
        "没有成功连接到可控制的浏览器",
        "请在设置中检测并选择 Chrome、Edge 或 Chromium，再重试。",
        True,
        re.compile(r"DevTools|调试端口|remote debugging|browser executable|找不到.*浏览器|浏览器.*调试", re.IGNORECASE),
    ),
    (
        "LOCAL_FILE_ERROR",
        "local_file",
        "本地文件或目录有问题",
        "请检查输入目录、输出目录和文件权限。",
        False,
        re.compile(r"ENOENT|EACCES|EPERM|EISDIR|ENOTDIR|file not found|path not found|目录不存在|文件不存在", re.IGNORECASE),
    ),
)


def _mask_text(value: Any) -> str:
    text = str(value or "")
    text = _BEARER.sub(r"\1***", text)
    return _SENSITIVE_TEXT.sub(lambda match: f"{match.group(1)}{match.group(2)}***", text)


def _mask_details(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "***" if re.search(r"cookie|token|secret|password|authorization|signature|access[_-]?key|api[_-]?key", str(key), re.I)
            else _mask_details(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_mask_details(item) for item in value]
    return _mask_text(value) if isinstance(value, str) else value


def _raw_error_text(error: Any) -> str:
    if isinstance(error, BaseException):
        return f"{type(error).__name__}: {error}"
    if isinstance(error, dict):
        return str(
            error.get("technicalMessage")
            or error.get("message")
            or error.get("error")
            or error.get("userMessage")
            or "未知错误"
        )
    return str(error or "未知错误")


def _rule_for(text: str) -> tuple[str, str, str, str, bool] | None:
    for code, category, user_message, recovery, retryable, pattern in _RULES:
        if pattern.search(text):
            return code, category, user_message, recovery, retryable
    return None


def normalize_error(
    error: Any,
    *,
    code: str = "",
    category: str = "",
    user_message: str = "",
    recovery: str = "",
    retryable: bool | None = None,
    correlation_id: str = "",
    provider: str = "",
    operation: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a redacted ``wandao.error`` object.

    Existing protocol objects are preserved where possible.  Unknown errors
    intentionally remain retryable: the task layer can still offer a safe
    retry, while a provider may explicitly set ``retryable`` to ``False``.
    """

    source = error if isinstance(error, dict) else {}
    nested = source.get("errorInfo") if isinstance(source.get("errorInfo"), dict) else source
    raw = _raw_error_text(nested if nested is not source else error)
    rule = _rule_for(raw)
    inferred = rule or ("UNKNOWN_ERROR", "unknown", "任务执行失败", "请查看详细日志，确认输入后重试或提交错误报告。", True)
    selected_code = str(code or nested.get("code") or inferred[0]).strip().upper().replace(" ", "_")
    selected_category = str(category or nested.get("category") or inferred[1]).strip() or inferred[1]
    selected_user_message = str(user_message or nested.get("userMessage") or inferred[2]).strip() or inferred[2]
    selected_recovery = str(recovery or nested.get("recovery") or inferred[3]).strip()
    selected_retryable = bool(nested.get("retryable")) if retryable is None and "retryable" in nested else (inferred[4] if retryable is None else bool(retryable))
    selected_correlation = str(
        correlation_id
        or nested.get("correlationId")
        or os.environ.get("WANDAO_CORRELATION_ID")
        or uuid.uuid4().hex[:16]
    )
    result: dict[str, Any] = {
        "kind": ERROR_KIND,
        "schemaVersion": ERROR_SCHEMA_VERSION,
        "code": selected_code[:64] or "UNKNOWN_ERROR",
        "category": selected_category,
        "userMessage": selected_user_message,
        # ``message`` keeps integrations that only know the short field alive.
        "message": selected_user_message,
        "recovery": selected_recovery,
        "retryable": selected_retryable,
        "correlationId": selected_correlation,
        "technicalMessage": _mask_text(raw),
    }
    for key, value in (("provider", provider), ("operation", operation)):
        selected = str(value or nested.get(key) or "").strip()
        if selected:
            result[key] = selected
    selected_status = nested.get("status")
    if selected_status is not None:
        result["status"] = selected_status
    selected_details = details if details is not None else nested.get("details")
    if isinstance(selected_details, dict) and selected_details:
        result["details"] = _mask_details(selected_details)
    return result


def error_result(error: Any, *, legacy_message: str = "", **kwargs: Any) -> dict[str, Any]:
    """Build the common failed-command envelope without breaking ``error``."""

    info = normalize_error(error, **kwargs)
    return {
        "success": False,
        "error": _mask_text(legacy_message or info["technicalMessage"] or info["userMessage"]),
        "errorInfo": info,
    }


error_info = normalize_error


__all__ = [
    "ERROR_KIND",
    "ERROR_SCHEMA_VERSION",
    "error_info",
    "error_result",
    "normalize_error",
]
