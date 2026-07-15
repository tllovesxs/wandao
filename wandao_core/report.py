#!/usr/bin/env python3
"""Small helpers for Wandao task reports.

Provider scripts can keep their platform-specific fields. ``finalize_report``
only fills the common fields that the desktop task center relies on.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


REPORT_SCHEMA_VERSION = 1
TASK_RESULT_KIND = "wandao.result"


def _number(*values: Any) -> int:
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return 0


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def resource_failures(report: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key, resource_type in (("resourceFailures", "resource"), ("imageFailures", "image"), ("attachmentFailures", "attachment")):
        for item in _list(report.get(key)):
            if isinstance(item, dict):
                items.append({"type": resource_type, **item})
    return items


def _failure_count(report: dict[str, Any]) -> int:
    return max(
        _number(report.get("failureCount")),
        len(_list(report.get("failures"))),
    )


def _resource_failure_count(report: dict[str, Any]) -> int:
    return max(
        _number(
            report.get("resourceFailureCount"),
            report.get("imageFailureCount"),
            report.get("attachmentFailureCount"),
        ),
        len(resource_failures(report)),
    )


def derive_outcome(report: dict[str, Any]) -> str:
    """Return the user-visible terminal outcome for a finalized task report.

    A successful process exit is not sufficient evidence of a successful task:
    document or required-resource failures make the outcome partial.  Process
    crashes are represented by the process result layer and therefore do not
    need to be inferred here.
    """

    if report.get("stopped"):
        return "stopped"
    if _failure_count(report) or _resource_failure_count(report):
        return "partial"
    return "completed"


def success_count(report: dict[str, Any]) -> int:
    return _number(
        report.get("successCount"),
        report.get("exportedDocs"),
        report.get("importedDocs"),
        report.get("importedCount"),
        report.get("importedFiles"),
        report.get("exported"),
        report.get("imported"),
        _number(report.get("createdDocs")) + _number(report.get("updatedDocs")),
    )


def finalize_report(
    report: dict[str, Any],
    *,
    provider: str = "",
    mode: str = "",
    report_file: str | Path | None = None,
    output: str | Path | None = None,
) -> dict[str, Any]:
    finalized = dict(report or {})
    finalized["kind"] = TASK_RESULT_KIND
    finalized["schemaVersion"] = REPORT_SCHEMA_VERSION
    finalized.setdefault("runId", os.environ.get("WANDAO_RUN_ID") or os.environ.get("WANDAO_TASK_ID", ""))
    finalized.setdefault("jobId", os.environ.get("WANDAO_JOB_ID", ""))
    finalized.setdefault("parentRunId", os.environ.get("WANDAO_PARENT_RUN_ID", ""))
    if "reportSchemaVersion" not in finalized:
        finalized["reportSchemaVersion"] = REPORT_SCHEMA_VERSION
    if provider and not finalized.get("provider"):
        finalized["provider"] = provider
    if not finalized.get("provider") and finalized.get("platform"):
        finalized["provider"] = finalized["platform"]
    if mode and not finalized.get("mode"):
        finalized["mode"] = mode
    if report_file and not finalized.get("reportFile"):
        finalized["reportFile"] = str(report_file)
    if output and not finalized.get("output"):
        finalized["output"] = str(output)
    finalized.setdefault(
        "totalDocs",
        _number(
            finalized.get("totalDocs"),
            finalized.get("total"),
            finalized.get("docCount"),
            finalized.get("fileCount"),
            finalized.get("selectedDocs"),
            finalized.get("sourceDocCount"),
            finalized.get("selectedFiles"),
            finalized.get("sourceFileCount"),
        ),
    )
    finalized.setdefault("successCount", success_count(finalized))
    finalized["failureCount"] = _failure_count(finalized)
    finalized.setdefault("failures", [])
    finalized.setdefault("resourceFailures", resource_failures(finalized))
    finalized["outcome"] = derive_outcome(finalized)
    return finalized
