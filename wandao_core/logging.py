#!/usr/bin/env python3
"""Structured runtime logging helpers for Wandao provider scripts.

The desktop app listens for lines prefixed with ``@@WANDAO_LOG@@`` and parses the
JSON payload. CLI usage keeps the original human-readable output unless
``WANDAO_STRUCTURED_LOGS=1`` is set.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any


LOG_PREFIX = "@@WANDAO_LOG@@"
SENSITIVE_KEYS = re.compile(r"(cookie|token|secret|password|authorization|signature|access[_-]?key|api[_-]?key)", re.I)
SIGNATURE_QUERY_RE = re.compile(r"([?&](?:Signature|signature|token|access_token|Authorization)=)[^&\s)]+")
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"\b(cookie|token|secret|password|authorization|signature|access[_-]?key|api[_-]?key)\s*([:=])\s*[^\s,&;)]+",
    re.I,
)


def structured_logs_enabled() -> bool:
    return os.environ.get("WANDAO_STRUCTURED_LOGS") == "1"


def current_task_id() -> str:
    return os.environ.get("WANDAO_RUN_ID") or os.environ.get("WANDAO_TASK_ID", "")


def current_job_id() -> str:
    return os.environ.get("WANDAO_JOB_ID", "")


def current_parent_run_id() -> str:
    return os.environ.get("WANDAO_PARENT_RUN_ID", "")


def current_provider_id(default: str = "python") -> str:
    return os.environ.get("WANDAO_PROVIDER_ID") or default


def mask_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        masked: dict[str, Any] = {}
        for key, item in value.items():
            if SENSITIVE_KEYS.search(str(key)):
                masked[str(key)] = "***"
            else:
                masked[str(key)] = mask_sensitive(item)
        return masked
    if isinstance(value, list):
        return [mask_sensitive(item) for item in value]
    if isinstance(value, str):
        text = SIGNATURE_QUERY_RE.sub(r"\1***", value)
        text = re.sub(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1***", text, flags=re.I)
        text = SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}***", text)
        return text
    return value


def error_payload(error: BaseException | str | None) -> dict[str, Any]:
    if error is None:
        return {}
    if isinstance(error, BaseException):
        payload: dict[str, Any] = {
            "type": type(error).__name__,
            "message": str(error),
        }
        status = getattr(error, "code", None) or getattr(error, "status", None)
        if status:
            payload["status"] = status
        return mask_sensitive(payload)
    return {"message": str(mask_sensitive(str(error)))}


class WandaoLogger:
    def __init__(self, provider: str = "", task_id: str | None = None) -> None:
        self.provider = provider
        self.task_id = task_id if task_id is not None else current_task_id()

    def event(
        self,
        event: str,
        message: str = "",
        *,
        level: str = "info",
        **fields: Any,
    ) -> None:
        if not structured_logs_enabled():
            if message:
                print_text(message)
            return
        payload = {
            "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "level": level,
            "event": event,
            "provider": self.provider,
            "taskId": self.task_id,
            "runId": self.task_id,
            "jobId": current_job_id(),
            "parentRunId": current_parent_run_id(),
            "message": message,
            **fields,
        }
        print(LOG_PREFIX + json.dumps(mask_sensitive(payload), ensure_ascii=False, separators=(",", ":")), flush=True)

    def info(self, event: str, message: str = "", **fields: Any) -> None:
        self.event(event, message, level="info", **fields)

    def warn(self, event: str, message: str = "", **fields: Any) -> None:
        self.event(event, message, level="warn", **fields)

    def error(self, event: str, message: str = "", *, error: BaseException | str | None = None, **fields: Any) -> None:
        payload = dict(fields)
        if error is not None:
            payload["error"] = error_payload(error)
        self.event(event, message, level="error", **payload)

    def progress(self, current: int, total: int, message: str = "", **fields: Any) -> None:
        self.event(
            fields.pop("event", "task.progress"),
            message,
            level="info",
            progress={"current": current, "total": total},
            **fields,
        )


def emit_log(
    provider: str,
    event: str,
    message: str,
    *,
    level: str = "info",
    task_id: str | None = None,
    **fields: Any,
) -> None:
    WandaoLogger(provider=provider, task_id=task_id).event(event, message, level=level, **fields)


def emit_legacy(
    provider: str,
    message: str,
    *,
    event: str = "log.message",
    level: str = "info",
    **fields: Any,
) -> None:
    """Compatibility adapter for existing scripts with ``emit(message)``.

    In Electron it becomes a structured event. In CLI it remains plain text.
    """
    if structured_logs_enabled():
        WandaoLogger(provider=current_provider_id(provider)).event(event, message, level=level, **fields)
    else:
        print_text(message)


def print_text(message: str) -> None:
    print(message, flush=True)
    try:
        sys.stdout.flush()
    except Exception:
        pass
