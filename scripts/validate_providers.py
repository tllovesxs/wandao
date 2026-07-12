#!/usr/bin/env python3
"""Validate Wandao file providers and notice-center metadata.

The validator intentionally checks structure and safety boundaries without
forcing every platform into the same workflow. Community providers may add
custom fields and capabilities, but paths, ids and action contracts must remain
predictable for the desktop app.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$", re.I)
FIELD_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

PROVIDER_TYPES = {"automation", "guide", "hybrid"}
PROVIDER_GROUPS = {"export", "import", "guide"}
TRUST_LEVELS = {"official", "community", "local", "experimental", "guide"}
STATUSES = {"stable", "beta", "experimental"}
FIELD_TYPES = {"text", "password", "directory", "file", "checkbox", "select", "number", "textarea", "notice"}
ACTION_KINDS = {"login", "scan", "export", "import", "plan", "check", "custom"}
NOTICE_TYPES = {"announcement", "tutorial"}


@dataclass(frozen=True)
class ValidationIssue:
    path: Path
    message: str

    def format(self, repo_root: Path) -> str:
        try:
            rel = self.path.relative_to(repo_root)
        except ValueError:
            rel = self.path
        return f"{rel.as_posix()}: {self.message}"


def load_json(path: Path) -> tuple[dict[str, Any] | None, list[ValidationIssue]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - validator must report all config errors clearly.
        return None, [ValidationIssue(path, f"JSON 读取失败：{exc}")]
    if not isinstance(data, dict):
        return None, [ValidationIssue(path, "JSON 根节点必须是对象")]
    return data, []


def is_inside(root: Path, candidate: Path) -> bool:
    root = root.resolve()
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_safe(root: Path, rel_path: str, path: Path, label: str) -> tuple[Path | None, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    if not isinstance(rel_path, str) or not rel_path.strip():
        return None, [ValidationIssue(path, f"{label} 不能为空")]
    if Path(rel_path).is_absolute():
        return None, [ValidationIssue(path, f"{label} 不能使用绝对路径：{rel_path}")]
    resolved = (root / rel_path).resolve()
    if not is_inside(root, resolved):
        return None, [ValidationIssue(path, f"{label} 不能跳出 provider 目录：{rel_path}")]
    return resolved, issues


def validate_required_strings(data: dict[str, Any], path: Path, keys: list[str]) -> list[ValidationIssue]:
    issues = []
    for key in keys:
        if not isinstance(data.get(key), str) or not data.get(key, "").strip():
            issues.append(ValidationIssue(path, f"缺少必填字符串字段：{key}"))
    return issues


def validate_capabilities(data: dict[str, Any], path: Path) -> list[ValidationIssue]:
    capabilities = data.get("capabilities")
    if not isinstance(capabilities, dict):
        return [ValidationIssue(path, "capabilities 必须是对象")]
    issues = []
    for key, value in capabilities.items():
        if not isinstance(key, str) or not key.strip():
            issues.append(ValidationIssue(path, "capabilities 的键必须是非空字符串"))
        if not isinstance(value, bool):
            issues.append(ValidationIssue(path, f"capabilities.{key} 必须是布尔值"))
    if capabilities.get("retryFailures"):
        retry = data.get("retryFailures")
        if not isinstance(retry, dict):
            issues.append(ValidationIssue(path, "capabilities.retryFailures 为 true 时需要声明 retryFailures 对象"))
        else:
            retry_arg = retry.get("arg")
            if not isinstance(retry_arg, str) or not retry_arg.startswith("--"):
                issues.append(ValidationIssue(path, "retryFailures.arg 必须是以 -- 开头的脚本参数"))
    return issues


def validate_fields(data: dict[str, Any], path: Path) -> list[ValidationIssue]:
    fields = data.get("fields", [])
    if fields is None:
        return []
    if not isinstance(fields, list):
        return [ValidationIssue(path, "fields 必须是数组")]
    issues: list[ValidationIssue] = []
    seen = set()
    for index, field in enumerate(fields):
        if not isinstance(field, dict):
            issues.append(ValidationIssue(path, f"fields[{index}] 必须是对象"))
            continue
        name = field.get("name")
        field_type = field.get("type", "text")
        if not isinstance(name, str) or not FIELD_NAME_RE.match(name):
            issues.append(ValidationIssue(path, f"fields[{index}].name 不合法：{name!r}"))
        elif name in seen:
            issues.append(ValidationIssue(path, f"fields[{index}].name 重复：{name}"))
        else:
            seen.add(name)
        if field_type not in FIELD_TYPES:
            issues.append(ValidationIssue(path, f"fields[{index}].type 不支持：{field_type!r}"))
        if field_type != "notice" and not isinstance(field.get("label"), str):
            issues.append(ValidationIssue(path, f"fields[{index}].label 必须是字符串"))
        if "arg" in field and not isinstance(field["arg"], str):
            issues.append(ValidationIssue(path, f"fields[{index}].arg 必须是字符串"))
        if "required" in field and not isinstance(field["required"], bool):
            issues.append(ValidationIssue(path, f"fields[{index}].required 必须是布尔值"))
        for action_key in ("actions", "includeActions", "excludeActions", "skipActions"):
            if action_key in field:
                value = field[action_key]
                if isinstance(value, str):
                    continue
                if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                    issues.append(ValidationIssue(path, f"fields[{index}].{action_key} 必须是字符串或字符串数组"))
        if field_type == "select" and "options" in field and not isinstance(field["options"], list):
            issues.append(ValidationIssue(path, f"fields[{index}].options 必须是数组"))
    return issues


def validate_toc(data: dict[str, Any], path: Path) -> list[ValidationIssue]:
    toc = data.get("toc", {})
    if not toc:
        return []
    if not isinstance(toc, dict):
        return [ValidationIssue(path, "toc 必须是对象")]
    issues = []
    for key in ("itemsPath", "idKey", "titleKey", "typeKey"):
        if key in toc and not isinstance(toc[key], str):
            issues.append(ValidationIssue(path, f"toc.{key} 必须是字符串"))
    if "selectionArg" in toc and not isinstance(toc["selectionArg"], str):
        issues.append(ValidationIssue(path, "toc.selectionArg 必须是字符串"))
    if "selectableTypes" in toc and (
        not isinstance(toc["selectableTypes"], list)
        or any(not isinstance(value, (str, int, float)) for value in toc["selectableTypes"])
    ):
        issues.append(ValidationIssue(path, "toc.selectableTypes 必须是字符串或数字数组"))
    return issues


def validate_actions(data: dict[str, Any], provider_root: Path, path: Path) -> list[ValidationIssue]:
    actions = data.get("actions", [])
    provider_type = data.get("type")
    if actions is None:
        actions = []
    if not isinstance(actions, list):
        return [ValidationIssue(path, "actions 必须是数组")]
    if provider_type != "guide" and not actions:
        return [ValidationIssue(path, "非教程型 provider 至少需要声明一个 action")]

    issues: list[ValidationIssue] = []
    seen = set()
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            issues.append(ValidationIssue(path, f"actions[{index}] 必须是对象"))
            continue
        action_id = action.get("id")
        if not isinstance(action_id, str) or not FIELD_NAME_RE.match(action_id):
            issues.append(ValidationIssue(path, f"actions[{index}].id 不合法：{action_id!r}"))
        elif action_id in seen:
            issues.append(ValidationIssue(path, f"actions[{index}].id 重复：{action_id}"))
        else:
            seen.add(action_id)
        if not isinstance(action.get("label"), str) or not action.get("label", "").strip():
            issues.append(ValidationIssue(path, f"actions[{index}].label 必须是非空字符串"))
        kind = action.get("kind")
        if kind is not None and kind not in ACTION_KINDS:
            issues.append(ValidationIssue(path, f"actions[{index}].kind 不支持：{kind!r}"))
        script = action.get("script") or data.get("script")
        if not script:
            issues.append(ValidationIssue(path, f"actions[{index}].script 不能为空"))
        else:
            script_path, script_issues = resolve_safe(provider_root, str(script), path, f"actions[{index}].script")
            issues.extend(script_issues)
            if script_path and (not script_path.exists() or script_path.suffix.lower() != ".py"):
                issues.append(ValidationIssue(path, f"actions[{index}].script 必须指向存在的 Python 文件：{script}"))
        args = action.get("args", [])
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            issues.append(ValidationIssue(path, f"actions[{index}].args 必须是字符串数组"))
        if "includeSelection" in action and not isinstance(action["includeSelection"], bool):
            issues.append(ValidationIssue(path, f"actions[{index}].includeSelection 必须是布尔值"))
    capabilities = data.get("capabilities") if isinstance(data.get("capabilities"), dict) else {}
    if data.get("type") != "guide" and capabilities.get("scanToc"):
        has_scan_action = any(
            isinstance(action, dict) and (action.get("kind") == "scan" or action.get("id") == "scan")
            for action in actions
        )
        if not has_scan_action:
            issues.append(ValidationIssue(path, "capabilities.scanToc 为 true 时需要提供 kind/id 为 scan 的 action"))
    return issues


def validate_provider_manifest(path: Path, repo_root: Path) -> list[ValidationIssue]:
    data, issues = load_json(path)
    if data is None:
        return issues
    provider_root = path.parent
    is_template = provider_root.name.startswith("_")

    issues.extend(validate_required_strings(data, path, ["id", "name", "title", "description", "type", "group", "trustLevel", "status"]))
    if "$schema" in data and not isinstance(data["$schema"], str):
        issues.append(ValidationIssue(path, "$schema 必须是字符串"))
    provider_id = data.get("id", "")
    if isinstance(provider_id, str) and not PROVIDER_ID_RE.match(provider_id):
        issues.append(ValidationIssue(path, f"id 不合法：{provider_id!r}"))
    if not is_template and isinstance(provider_id, str) and provider_id != provider_root.name:
        issues.append(ValidationIssue(path, f"公开 provider 目录名必须和 id 一致：目录={provider_root.name}，id={provider_id}"))
    if data.get("schemaVersion") != 1:
        issues.append(ValidationIssue(path, "schemaVersion 必须是 1"))
    if data.get("type") not in PROVIDER_TYPES:
        issues.append(ValidationIssue(path, f"type 不支持：{data.get('type')!r}"))
    if data.get("group") not in PROVIDER_GROUPS:
        issues.append(ValidationIssue(path, f"group 不支持：{data.get('group')!r}"))
    if data.get("trustLevel") not in TRUST_LEVELS:
        issues.append(ValidationIssue(path, f"trustLevel 不支持：{data.get('trustLevel')!r}"))
    if data.get("status") not in STATUSES:
        issues.append(ValidationIssue(path, f"status 不支持：{data.get('status')!r}"))

    issues.extend(validate_capabilities(data, path))
    issues.extend(validate_fields(data, path))
    issues.extend(validate_toc(data, path))
    issues.extend(validate_actions(data, provider_root, path))

    guide = data.get("guide") or data.get("guidePath")
    if data.get("type") in {"guide", "hybrid"} and not guide:
        issues.append(ValidationIssue(path, "guide/hybrid provider 需要声明 guide 或 guidePath"))
    if guide:
        guide_path, guide_issues = resolve_safe(provider_root, str(guide), path, "guide")
        issues.extend(guide_issues)
        if guide_path and (not guide_path.exists() or not guide_path.is_file()):
            issues.append(ValidationIssue(path, f"guide 文件不存在：{guide}"))

    homepage = data.get("homepage")
    if homepage and not isinstance(homepage, str):
        issues.append(ValidationIssue(path, "homepage 必须是字符串"))
    tags = data.get("tags", [])
    if tags and (not isinstance(tags, list) or not all(isinstance(item, str) for item in tags)):
        issues.append(ValidationIssue(path, "tags 必须是字符串数组"))

    requirements = data.get("requirements", {})
    if requirements and not isinstance(requirements, dict):
        issues.append(ValidationIssue(path, "requirements 必须是对象"))
    if not is_inside(repo_root, path):
        issues.append(ValidationIssue(path, "provider.json 必须位于仓库内"))
    return issues


def provider_manifest_paths(repo_root: Path) -> list[Path]:
    providers_dir = repo_root / "providers"
    if not providers_dir.exists():
        return []
    return sorted(providers_dir.glob("*/provider.json"))


def validate_notice_manifest(repo_root: Path) -> list[ValidationIssue]:
    path = repo_root / "docs" / "tutorial-announcements.json"
    data, issues = load_json(path)
    if data is None:
        return issues
    if data.get("version") != 1:
        issues.append(ValidationIssue(path, "version 必须是 1"))
    items = data.get("items")
    if not isinstance(items, list):
        return issues + [ValidationIssue(path, "items 必须是数组")]
    seen = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            issues.append(ValidationIssue(path, f"items[{index}] 必须是对象"))
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not FIELD_NAME_RE.match(item_id):
            issues.append(ValidationIssue(path, f"items[{index}].id 不合法：{item_id!r}"))
        elif item_id in seen:
            issues.append(ValidationIssue(path, f"items[{index}].id 重复：{item_id}"))
        else:
            seen.add(item_id)
        if item.get("type") not in NOTICE_TYPES:
            issues.append(ValidationIssue(path, f"items[{index}].type 必须是 announcement 或 tutorial"))
        for key in ("title", "summary", "date", "path"):
            if not isinstance(item.get(key), str) or not item.get(key, "").strip():
                issues.append(ValidationIssue(path, f"items[{index}].{key} 必须是非空字符串"))
        if isinstance(item.get("date"), str) and not DATE_RE.match(item["date"]):
            issues.append(ValidationIssue(path, f"items[{index}].date 必须是 YYYY-MM-DD"))
        if "pinned" in item and not isinstance(item["pinned"], bool):
            issues.append(ValidationIssue(path, f"items[{index}].pinned 必须是布尔值"))
        if "tags" in item and (not isinstance(item["tags"], list) or not all(isinstance(tag, str) for tag in item["tags"])):
            issues.append(ValidationIssue(path, f"items[{index}].tags 必须是字符串数组"))
        doc_path = item.get("path")
        if isinstance(doc_path, str) and doc_path.strip():
            resolved = (repo_root / doc_path).resolve()
            if not is_inside(repo_root, resolved):
                issues.append(ValidationIssue(path, f"items[{index}].path 不能跳出仓库：{doc_path}"))
            elif not resolved.exists() or not resolved.is_file():
                issues.append(ValidationIssue(path, f"items[{index}].path 文件不存在：{doc_path}"))
    return issues


def validate_repository(repo_root: Path) -> list[ValidationIssue]:
    repo_root = repo_root.resolve()
    issues: list[ValidationIssue] = []
    for path in provider_manifest_paths(repo_root):
        issues.extend(validate_provider_manifest(path, repo_root))
    issues.extend(validate_notice_manifest(repo_root))
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Wandao provider manifests and notice metadata.")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    issues = validate_repository(repo_root)
    if issues:
        for issue in issues:
            print(issue.format(repo_root), file=sys.stderr)
        print(f"Provider validation failed: {len(issues)} issue(s).", file=sys.stderr)
        return 1
    print("Provider validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
