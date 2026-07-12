#!/usr/bin/env python3
# Author: tllovesxs
"""
Import local Markdown documents into a Feishu Wiki space.

The tool supports both a browser-assisted probe flow and an OpenAPI write flow:

- save browser login cookies for Wiki probing;
- inspect the target Wiki space/node;
- scan a local Markdown directory and estimate the import plan;
- upload Markdown through Feishu OpenAPI, move imported docs into Wiki, and
  repair local-image placeholders with real uploaded images.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import base64
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from mimetypes import guess_type
from pathlib import Path
from typing import Any, Callable

from wandao_core.browser import (
    CDPClient,
    DEFAULT_PORT,
    ExportError,
    ExportStopped,
    check_stopped,
    default_data_dir,
    default_state_path,
    emit,
    find_chrome,
    http_json,
    sanitize_filename,
    stop_requested,
    wait_for_debug_port,
)
from export_feishu import (
    connect_wiki_browser,
    default_auth_path,
    default_profile_path,
    load_auth_state,
    load_wiki_tree,
    login_and_save_auth as export_login_and_save_auth,
    open_tab,
    order_tree,
    parse_wiki_url,
    start_chrome,
    wait_for_wiki_ready,
)
from wandao_core.report import finalize_report
from wandao_core.credentials import write_private_json
from wandao_core.checkpoint import add_checkpoint_args, open_checkpoint_from_args


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR
OPENAPI_BASE = "https://open.feishu.cn/open-apis"
FEISHU_DEVELOPER_CONSOLE_URL = "https://open.feishu.cn/app"
FEISHU_BOT_SETUP_URL_TEMPLATE = "https://open.feishu.cn/app/{app_id}/bot"
FEISHU_IMPORT_REQUIRED_SCOPES = (
    "drive:drive",
    "drive:file:upload",
    "docs:permission.member:create",
    "docs:document:import",
    "docx:document",
    "docx:document:write_only",
    "wiki:wiki",
)
FEISHU_SCOPE_FALLBACK_PRIORITY = (
    "docx:document:write_only",
    "drive:file:upload",
    "drive:drive",
    "docs:permission.member:create",
    "docs:document:import",
    "docx:document",
    "wiki:wiki",
)
FEISHU_PERMISSION_URL_RE = re.compile(r"https://open\.feishu\.cn/app/[^\s\"'<>，。]+")
DEFAULT_WIKI_URL = ""
DEFAULT_SOURCE_DIR = default_data_dir() / "exports" / "feishu"
DEFAULT_CONFIG_FILE = default_state_path(".feishu_import_config.json")
DEFAULT_OPENAPI_PROFILE = ".feishu-openapi-profile"
DEFAULT_IMAGE_MAX_WIDTH = 1460
MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^\n)]*)\)")
HTML_IMAGE_RE = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", re.I)

FEISHU_ADD_DOC_APP_JS = r"""
async (appName) => {
  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const visible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  };
  const textOf = (el) => normalize(el && (el.innerText || el.textContent));
  const all = (selector) => [...document.querySelectorAll(selector)].filter(visible);
  const hasText = (el, keyword) => textOf(el).includes(keyword);
  const byExactText = (selector, keyword) => all(selector).find((el) => textOf(el) === keyword);
  const byText = (selector, keyword) => all(selector).find((el) => hasText(el, keyword));
  const click = async (el) => {
    if (!el) throw new Error("click target is missing");
    el.scrollIntoView({ block: "center", inline: "center" });
    await delay(80);
    for (const type of ["pointerover", "mouseover", "mouseenter", "mousemove", "pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
    }
    await delay(700);
  };
  const setInputValue = (input, value) => {
    const setter = Object.getOwnPropertyDescriptor(input.__proto__ || HTMLInputElement.prototype, "value")?.set;
    if (setter) setter.call(input, value);
    else input.value = value;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const waitFor = async (fn, timeoutMs = 12000) => {
    const deadline = Date.now() + timeoutMs;
    let lastError = "";
    while (Date.now() < deadline) {
      try {
        const value = fn();
        if (value) return value;
      } catch (error) {
        lastError = error && error.message ? error.message : String(error);
      }
      await delay(300);
    }
    throw new Error(lastError || "等待飞书页面元素超时");
  };
  const docAppDialog = () =>
    all('[role="dialog"], .suite-modal, .modal, .ud__modal, .larkc-modal, body')
      .find((el) => hasText(el, "文档应用"));
  const isAdded = () => {
    const dialog = docAppDialog();
    return Boolean(dialog && hasText(dialog, "已添加应用") && hasText(dialog, appName));
  };

  if (isAdded()) {
    return { status: "already-added", appName };
  }

  if (!docAppDialog()) {
    await waitFor(() => byText('button,[role="button"]', "分享") || byText(document.body ? "body" : "div", "分享"), 20000);
    const topActionButtons = () => {
      const buttons = all('button,[role="button"]').filter((el) => {
        const rect = el.getBoundingClientRect();
        const label = textOf(el);
        return rect.top < 90
          && rect.left > window.innerWidth * 0.55
          && !label.includes("分享")
          && !label.includes("编辑")
          && !label.includes("目录")
          && !label.includes("搜索");
      });
      const byLeft = buttons.sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
      const preferred = [];
      if (byLeft.length >= 1) preferred.push(byLeft[0]);
      if (byLeft.length >= 2) preferred.push(byLeft[1]);
      if (byLeft.length >= 3) preferred.push(byLeft[2]);
      for (const button of byLeft) {
        if (!preferred.includes(button)) preferred.push(button);
      }
      return preferred;
    };
    let openedMenu = false;
    for (const button of topActionButtons().slice(0, 5)) {
      await click(button);
      if (byExactText('[role="menuitem"], li, div, button', "更多") || byText('[role="menuitem"], li, div, button', "更多")) {
        openedMenu = true;
        break;
      }
      await delay(250);
    }
    if (!openedMenu) {
      let moreButton = document.elementFromPoint(window.innerWidth - 82, 32);
      moreButton = moreButton && moreButton.closest('button,[role="button"]');
      await click(moreButton);
    }

    const moreItem = await waitFor(
      () => byExactText('[role="menuitem"], li, div, button', "更多") || byText('[role="menuitem"], li, div, button', "更多")
    );
    await click(moreItem);

    const addAppItem = await waitFor(
      () => byExactText('[role="menuitem"], li, div, button', "添加文档应用") || byText('[role="menuitem"], li, div, button', "添加文档应用")
    );
    await click(addAppItem);
  }

  const dialog = await waitFor(docAppDialog);
  if (isAdded()) {
    return { status: "already-added", appName };
  }

  const searchInput = await waitFor(() =>
    [...dialog.querySelectorAll("input, textarea")]
      .filter(visible)
      .find((el) => (el.getAttribute("placeholder") || el.getAttribute("aria-label") || "").includes("搜索应用名称"))
  );
  searchInput.focus();
  setInputValue(searchInput, appName);

  await waitFor(() => hasText(dialog, appName), 15000);
  if (isAdded()) {
    return { status: "already-added", appName };
  }

  const resultRow = await waitFor(() => {
    const candidates = [...dialog.querySelectorAll("p, div, span")]
      .filter(visible)
      .filter((el) => textOf(el) === appName);
    for (const candidate of candidates) {
      let row = candidate;
      for (let i = 0; i < 6 && row; i += 1) {
        const rect = row.getBoundingClientRect();
        const rowText = textOf(row);
        if (rect.width > 180 && rect.height >= 30 && rect.height < 130 && rowText.includes(appName)) {
          return row;
        }
        row = row.parentElement;
      }
    }
    return null;
  }, 15000);
  await click(resultRow);

  const confirmButton = await waitFor(() =>
    byExactText('button,[role="button"]', "添加") || byText('button,[role="button"]', "添加")
  );
  await click(confirmButton);

  await waitFor(() => isAdded() || hasText(document.body, "添加应用成功"), 15000);
  return { status: "added", appName };
}
"""

FEISHU_DOC_APP_STATUS_JS = r"""
async (payload) => {
  const objToken = payload.objToken;
  const objType = payload.objType || 22;
  const appName = payload.appName || "";
  const appId = payload.appId || "";
  const getJson = async (url) => {
    const response = await fetch(url, { credentials: "include" });
    const text = await response.text();
    let data = {};
    try {
      data = JSON.parse(text);
    } catch (error) {
      data = { raw: text.slice(0, 500) };
    }
    return { ok: response.ok, status: response.status, data };
  };
  const listResult = await getJson(
    `/space/api/suite/permission/members/applist/?token=${encodeURIComponent(objToken)}&type=${encodeURIComponent(String(objType))}`
  );
  const apps = (listResult.data && listResult.data.data && listResult.data.data.apps) || [];
  const alreadyAdded = apps.find((app) => app.owner_name === appName || (appId && app.app_id === appId));

  let searchResult = null;
  let searchError = "";
  try {
    const url = "https://internal-api.feishu.cn/lark/app_explorer/api/GetCCMAppList"
      + `?query=${encodeURIComponent(appName)}&locale=zh_cn&cursorInfo%5Bcount%5D=100`;
    const result = await getJson(url);
    const data = result.data && result.data.data || {};
    const candidates = [
      ...((data.availableAppList || []).map((item) => ({ ...item, available: true }))),
      ...((data.unavailableAppList || []).map((item) => ({ ...item, available: false }))),
    ];
    searchResult = candidates.find((item) => item.appId === appId || item.name === appName) || candidates[0] || null;
  } catch (error) {
    searchError = error && error.message ? error.message : String(error);
  }

  return {
    objToken,
    objType,
    appName,
    appId,
    appCount: apps.length,
    alreadyAdded: Boolean(alreadyAdded),
    existingApp: alreadyAdded || null,
    searchApp: searchResult,
    searchError,
    rawListCode: listResult.data && listResult.data.code,
    rawListStatus: listResult.status,
  };
}
"""


class FeishuPermissionError(ExportError):
    def __init__(self, message: str, *, permission_url: str = "", scopes: list[str] | None = None) -> None:
        super().__init__(message)
        self.permission_url = permission_url
        self.scopes = scopes or []


class FeishuWikiNodePermissionError(ExportError):
    pass


class FeishuUploadForbiddenError(FeishuPermissionError):
    pass


def auth_path_from_args(args: argparse.Namespace) -> Path:
    return Path(args.auth_file).resolve() if args.auth_file else default_auth_path().resolve()


def config_path_from_args(args: argparse.Namespace | None = None) -> Path:
    if args and getattr(args, "config_file", None):
        return Path(args.config_file).resolve()
    return DEFAULT_CONFIG_FILE.resolve()


def default_openapi_profile_path() -> Path:
    return default_data_dir() / DEFAULT_OPENAPI_PROFILE


def load_import_config(config_file: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_file).resolve() if config_file else DEFAULT_CONFIG_FILE.resolve()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ExportError(f"飞书导入配置文件不是合法 JSON：{path}") from exc
    if not isinstance(data, dict):
        raise ExportError(f"飞书导入配置文件根节点必须是对象：{path}")
    return data


def save_import_config(config_file: str | Path, data: dict[str, Any]) -> Path:
    path = Path(config_file).resolve()
    return write_private_json(path, data)


def save_import_config_from_args(args: argparse.Namespace) -> dict[str, Any]:
    app_id = str(args.app_id or "").strip()
    app_secret = str(args.app_secret or os.environ.get("FEISHU_APP_SECRET") or "").strip()
    if not app_id or not app_secret:
        raise ExportError("保存配置前请填写飞书 App ID 和 App Secret。")
    data = {
        "app_id": app_id,
        "app_secret": app_secret,
        "drive_folder_token": str(args.drive_folder_token or "").strip(),
        "import_mount_key": str(args.import_mount_key or "").strip(),
        "space_id": str(args.space_id or "").strip(),
        "parent_wiki_token": str(args.parent_wiki_token or "").strip(),
        "obj_type": str(args.obj_type or "docx").strip() or "docx",
    }
    path = save_import_config(config_path_from_args(args), data)
    return {"saved": True, "configFile": str(path), "appId": app_id}


def build_feishu_permission_url(app_id: str, scopes: list[str] | tuple[str, ...] | None = None) -> str:
    scope_list = [scope for scope in (scopes or FEISHU_IMPORT_REQUIRED_SCOPES) if scope]
    query = urllib.parse.urlencode(
        {
            "q": ",".join(scope_list),
            "op_from": "openapi",
            "token_type": "tenant",
        }
    )
    return f"{FEISHU_DEVELOPER_CONSOLE_URL}/{urllib.parse.quote(app_id.strip())}/auth?{query}"


def extract_feishu_permission_url(text: str) -> str:
    match = FEISHU_PERMISSION_URL_RE.search(text or "")
    return match.group(0).rstrip(".,，。") if match else ""


def extract_permission_scopes(data: dict[str, Any], text: str = "") -> list[str]:
    scopes: list[str] = []
    error = data.get("error") if isinstance(data.get("error"), dict) else {}
    for item in error.get("permission_violations") or []:
        if isinstance(item, dict) and item.get("subject"):
            scopes.append(str(item["subject"]))
    if not scopes:
        for scope in re.findall(r"\b(?:drive|docx|docs|wiki|sheets|base):[A-Za-z0-9_.:-]+", text or ""):
            if scope not in scopes:
                scopes.append(scope)
    return normalize_feishu_scopes(scopes)


def normalize_feishu_scopes(scopes: list[str] | tuple[str, ...]) -> list[str]:
    """Prefer the least broad Wandao-related scope when Feishu returns alternatives."""
    unique = [scope for index, scope in enumerate(scopes) if scope and scope not in scopes[:index]]
    if "docx:document:write_only" in unique:
        unique = [
            scope
            for scope in unique
            if scope not in {"sheets:spreadsheet:write_only", "base:app:update"}
        ]
    priority = {scope: index for index, scope in enumerate(FEISHU_SCOPE_FALLBACK_PRIORITY)}
    return sorted(unique, key=lambda scope: (priority.get(scope, 999), scope))


def build_wiki_node_permission_error(action: str, http_code: int, text: str) -> FeishuWikiNodePermissionError | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if str(data.get("code")) != "131006":
        return None
    message = data.get("msg") or data.get("message") or text[:500]
    return FeishuWikiNodePermissionError(
        f"{action} HTTP {http_code}：目标 Wiki 父节点没有给当前飞书应用写入权限。\n"
        f"飞书返回：{message}\n"
        "这不是 OpenAPI scope 没开，而是目标知识库页面/空间的数据权限问题。\n"
        "处理方式：在目标飞书 Wiki 右上角选择“... -> 更多 -> 添加文档应用”，"
        "把当前企业自建应用添加为可编辑文档应用；或直接点击万能导里的“授权目标 Wiki 文档应用”。"
    )


def build_permission_error(action: str, http_code: int, text: str) -> FeishuPermissionError | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if str(data.get("code")) != "99991672":
        return None
    message = data.get("msg") or data.get("message") or "飞书应用缺少开放平台权限"
    scopes = extract_permission_scopes(data, text)
    permission_url = extract_feishu_permission_url(text)
    lines = [
        f"{action} HTTP {http_code}：飞书应用缺少开放平台权限。",
        f"飞书返回：{message}",
    ]
    if scopes:
        lines.append(f"缺失权限：{', '.join(scopes)}")
    if permission_url:
        lines.append(f"权限申请链接：{permission_url}")
    lines.append("开通权限后，请在飞书开放平台发布应用新版本；如果企业需要审批，请完成审批后再重试。")
    return FeishuPermissionError("\n".join(lines), permission_url=permission_url, scopes=scopes)


def build_upload_forbidden_error(action: str, http_code: int, text: str) -> FeishuUploadForbiddenError | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if str(data.get("code")) != "1061004":
        return None
    message = data.get("msg") or data.get("message") or text[:500]
    scopes = ["drive:file:upload", "drive:drive"]
    return FeishuUploadForbiddenError(
        "\n".join(
            [
                f"{action} HTTP {http_code}：飞书拒绝上传文件。",
                f"飞书返回：{message}",
                "常见原因：当前企业自建应用没有上传文件权限，权限开通后没有发布新版本，"
                "或者配置里的云空间文件夹 token 不属于当前应用可写范围。",
                f"建议权限：{', '.join(scopes)}",
                "处理方式：点击“初始化开放平台权限”确认权限并发布版本；如果仍失败，清空云空间文件夹 token 让工具自动获取。",
            ]
        ),
        scopes=scopes,
    )


def maybe_open_permission_page(args: argparse.Namespace, exc: Exception) -> None:
    if getattr(args, "no_auto_open_permission", False):
        return
    text = str(exc)
    if not isinstance(exc, FeishuPermissionError) and "99991672" not in text and "Access denied" not in text:
        return
    permission_url = getattr(exc, "permission_url", "") or extract_feishu_permission_url(text)
    scopes = getattr(exc, "scopes", None) or extract_permission_scopes({}, text) or list(FEISHU_IMPORT_REQUIRED_SCOPES)
    app_id = ""
    try:
        app_id = get_config_value(args, "app_id", "FEISHU_APP_ID")
    except Exception:
        app_id = ""
    if app_id and scopes:
        permission_url = build_feishu_permission_url(app_id, scopes)
    if permission_url:
        try:
            webbrowser.open(permission_url)
            print(f"已自动打开飞书权限申请页：{permission_url}", file=sys.stderr)
        except Exception as open_exc:
            print(f"自动打开飞书权限申请页失败：{open_exc}\n请手动打开：{permission_url}", file=sys.stderr)


def wait_for_page_text(cdp: CDPClient, expected: str, timeout: float = 60) -> str:
    deadline = time.time() + timeout
    last_text = ""
    while time.time() < deadline:
        try:
            text = cdp.evaluate("document.body ? document.body.innerText : ''", timeout=5) or ""
            last_text = str(text)
            if expected in last_text:
                return last_text
        except Exception:
            pass
        time.sleep(1)
    return last_text


def setup_openapi_permissions(args: argparse.Namespace) -> dict[str, Any]:
    app_id = get_config_value(args, "app_id", "FEISHU_APP_ID")
    if not app_id:
        raise ExportError("请先填写飞书 App ID，再初始化开放平台权限。")

    permission_url = build_feishu_permission_url(app_id, list(FEISHU_IMPORT_REQUIRED_SCOPES))
    profile_dir = Path(getattr(args, "profile_dir", None) or default_openapi_profile_path()).resolve()
    port = int(getattr(args, "port", DEFAULT_PORT) or DEFAULT_PORT)
    chrome_proc = start_chrome(port, profile_dir, permission_url, getattr(args, "browser_path", None))
    try:
        wait_for_debug_port(port, timeout=30)
        page = None
        deadline = time.time() + 30
        while time.time() < deadline and page is None:
            pages = http_json(f"http://127.0.0.1:{port}/json/list", timeout=5)
            page = next((item for item in pages if item.get("type") == "page" and app_id in item.get("url", "")), None)
            if page is None:
                time.sleep(1)
        if not page:
            raise ExportError("没有找到飞书开放平台权限页，请确认浏览器已正常打开。")
        cdp = CDPClient(page["webSocketDebuggerUrl"])
        cdp.connect()
        try:
            emit(args, "已打开飞书开放平台权限页。如果需要登录，请先在浏览器里完成登录。")
            text = wait_for_page_text(cdp, "权限管理", timeout=90)
            if "权限管理" not in text:
                emit(args, "暂未进入权限管理页，通常是飞书开放平台还未登录。请在浏览器完成登录后重新点击初始化。")
                return {
                    "provider": "feishu-import-openapi-permission-setup",
                    "appId": app_id,
                    "permissionUrl": permission_url,
                    "loginRequired": True,
                    "detectedScopes": [],
                    "missingScopes": [],
                    "nextStep": "请在打开的浏览器里登录飞书开放平台，然后重新点击初始化开放平台权限。",
                }
            missing = [scope for scope in FEISHU_IMPORT_REQUIRED_SCOPES if scope not in text]
            opened = [scope for scope in FEISHU_IMPORT_REQUIRED_SCOPES if scope in text]
            if missing:
                emit(args, f"权限页暂未检测到这些 scope：{', '.join(missing)}")
                emit(args, "请在弹窗中确认开通这些权限；如果复选框是灰色，通常表示已开通。")
                return {
                    "provider": "feishu-import-openapi-permission-setup",
                    "appId": app_id,
                    "permissionUrl": permission_url,
                    "detectedScopes": opened,
                    "missingScopes": missing,
                    "nextStep": "请在当前权限页确认开通缺失权限；完成后再次点击初始化，确认无缺失后再发布版本。",
                }
            else:
                emit(args, "已在权限页检测到导入所需 scope。若复选框为灰色，表示权限已开通。")
            version_url = f"{FEISHU_DEVELOPER_CONSOLE_URL}/{urllib.parse.quote(app_id)}/version"
            cdp.navigate(version_url)
            emit(args, "已打开版本管理与发布页。权限变更需要发布新版本后才会生效。")
            return {
                "provider": "feishu-import-openapi-permission-setup",
                "appId": app_id,
                "permissionUrl": permission_url,
                "versionUrl": version_url,
                "detectedScopes": opened,
                "missingScopes": missing,
                "nextStep": "在飞书开放平台确认开通权限，并发布应用新版本。",
            }
        finally:
            cdp.close()
    finally:
        if getattr(args, "close_started_chrome", False):
            chrome_proc.terminate()


def get_config_value(args: argparse.Namespace, key: str, env_name: str | None = None) -> str:
    direct = getattr(args, key, None)
    if direct:
        return str(direct).strip()
    if env_name and os.getenv(env_name):
        return os.getenv(env_name, "").strip()
    config = getattr(args, "_import_config", None)
    if config is None:
        config = load_import_config(getattr(args, "config_file", None))
        setattr(args, "_import_config", config)
    value = config.get(key)
    return str(value).strip() if value is not None else ""


def normalize_title_from_md(md_path: Path) -> str:
    try:
        text = md_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return sanitize_filename(md_path.stem)
    match = re.search(r"^\s*#\s+(.+?)\s*$", text, re.M)
    return sanitize_filename(match.group(1)) if match else sanitize_filename(md_path.stem)


def build_safe_import_title(md_path: Path) -> str:
    # 飞书导入任务在部分租户下遇到中文 file_name 会异步失败且不返回错误。
    # 这里使用 ASCII 临时标题，正文中的 Markdown 一级标题仍保留原文。
    return f"wandao-import-{int(time.time())}-{uuid.uuid4().hex[:8]}"


def is_remote_or_data_url(url: str) -> bool:
    lowered = url.strip().lower()
    return lowered.startswith(("http://", "https://", "data:", "mailto:", "#"))


def clean_markdown_url(url: str) -> str:
    cleaned = url.strip().strip("<>").strip()
    if "#" in cleaned:
        cleaned = cleaned.split("#", 1)[0]
    return urllib.parse.unquote(cleaned)


def markdown_image_url_candidates(raw_url: str) -> list[str]:
    raw_url = raw_url.strip()
    candidates = [raw_url]
    if raw_url.startswith("<") and raw_url.endswith(">"):
        candidates.insert(0, raw_url[1:-1].strip())
    stripped_title = re.sub(r"\s+(['\"])[^'\"]*\1\s*$", "", raw_url).strip()
    if stripped_title and stripped_title not in candidates:
        candidates.append(stripped_title)
    return candidates


def collect_local_images_from_markdown(md_path: Path) -> list[dict[str, str]]:
    text = md_path.read_text(encoding="utf-8", errors="ignore")
    images: list[dict[str, str]] = []
    for match in MARKDOWN_IMAGE_RE.finditer(text):
        for candidate in markdown_image_url_candidates(match.group(2)):
            raw_url = clean_markdown_url(candidate)
            if not raw_url or is_remote_or_data_url(raw_url):
                continue
            image_path = (md_path.parent / raw_url.replace("/", os.sep)).resolve()
            if image_path.exists() and image_path.is_file():
                images.append({"alt": match.group(1), "url": raw_url, "path": str(image_path)})
                break
    for match in HTML_IMAGE_RE.finditer(text):
        raw_url = clean_markdown_url(match.group(1))
        if not raw_url or is_remote_or_data_url(raw_url):
            continue
        image_path = (md_path.parent / raw_url.replace("/", os.sep)).resolve()
        if image_path.exists() and image_path.is_file():
            images.append({"alt": "", "url": raw_url, "path": str(image_path)})
    return images


def prepare_markdown_for_image_blocks(md_path: Path) -> tuple[Path, Path | None]:
    temp_dir: Path | None = None
    index = 0
    changed = False
    text = md_path.read_text(encoding="utf-8", errors="ignore")

    def markdown_replacer(match: re.Match[str]) -> str:
        nonlocal index, changed
        alt = match.group(1)
        for candidate in markdown_image_url_candidates(match.group(2)):
            raw_url = clean_markdown_url(candidate)
            if not raw_url or is_remote_or_data_url(raw_url):
                continue
            image_path = (md_path.parent / raw_url.replace("/", os.sep)).resolve()
            if image_path.exists() and image_path.is_file():
                index += 1
                changed = True
                suffix = image_path.suffix or ".png"
                # 飞书导入器遇到带空格/中文标点的本地路径时可能不生成图片块。
                # 这里临时换成稳定占位路径，只为让导入器创建图片块，随后再用真实本地图替换。
                return f"![{alt}](wandao-local-image-{index:04d}{suffix})"
        return match.group(0)

    rewritten = MARKDOWN_IMAGE_RE.sub(markdown_replacer, text)
    if not changed:
        return md_path, None

    temp_dir = Path(tempfile.mkdtemp(prefix="wandao-feishu-import-"))
    temp_path = temp_dir / md_path.name
    temp_path.write_text(rewritten, encoding="utf-8")
    return temp_path, temp_dir


def image_path_to_data_url(image_path: Path) -> str:
    mime_type = guess_type(str(image_path))[0] or "application/octet-stream"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def prepare_markdown_with_inlined_local_images(md_path: Path) -> tuple[Path, Path | None]:
    text = md_path.read_text(encoding="utf-8", errors="ignore")
    changed = False

    def markdown_replacer(match: re.Match[str]) -> str:
        nonlocal changed
        alt = match.group(1)
        for candidate in markdown_image_url_candidates(match.group(2)):
            raw_url = clean_markdown_url(candidate)
            if not raw_url or is_remote_or_data_url(raw_url):
                continue
            image_path = (md_path.parent / raw_url.replace("/", os.sep)).resolve()
            if image_path.exists() and image_path.is_file():
                changed = True
                return f"![{alt}]({image_path_to_data_url(image_path)})"
        return match.group(0)

    def html_replacer(match: re.Match[str]) -> str:
        nonlocal changed
        raw_url = clean_markdown_url(match.group(1))
        if not raw_url or is_remote_or_data_url(raw_url):
            return match.group(0)
        image_path = (md_path.parent / raw_url.replace("/", os.sep)).resolve()
        if not image_path.exists() or not image_path.is_file():
            return match.group(0)
        changed = True
        return match.group(0).replace(match.group(1), image_path_to_data_url(image_path), 1)

    rewritten = MARKDOWN_IMAGE_RE.sub(markdown_replacer, text)
    rewritten = HTML_IMAGE_RE.sub(html_replacer, rewritten)
    if not changed:
        return md_path, None

    temp_dir = Path(tempfile.mkdtemp(prefix="wandao-feishu-inline-images-"))
    temp_path = temp_dir / md_path.name
    temp_path.write_text(rewritten, encoding="utf-8")
    return temp_path, temp_dir


def read_local_image_size(image_path: Path) -> tuple[int, int] | None:
    try:
        data = image_path.read_bytes()
    except OSError:
        return None
    if len(data) < 24:
        return None

    # PNG: width/height live in the IHDR chunk and can be read without decoding the image.
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return (int(width), int(height)) if width and height else None

    # GIF: logical screen width/height are little-endian at bytes 6..10.
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return (int(width), int(height)) if width and height else None

    # WebP: support VP8X and VP8 lossless headers, which cover common exported images.
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP" and len(data) >= 30:
        chunk = data[12:16]
        if chunk == b"VP8X":
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
            return (width, height) if width and height else None
        if chunk == b"VP8L" and len(data) >= 25:
            bits = int.from_bytes(data[21:25], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
            return (width, height) if width and height else None

    # JPEG: walk segments until a Start Of Frame marker provides dimensions.
    if data.startswith(b"\xff\xd8"):
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            index += 2
            while marker == 0xFF and index < len(data):
                marker = data[index]
                index += 1
            if marker in (0xD8, 0xD9):
                continue
            if index + 2 > len(data):
                return None
            length = int.from_bytes(data[index:index + 2], "big")
            if length < 2 or index + length > len(data):
                return None
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                height = int.from_bytes(data[index + 3:index + 5], "big")
                width = int.from_bytes(data[index + 5:index + 7], "big")
                return (width, height) if width and height else None
            index += length

    return None


def fit_image_display_size(
    image_path: Path,
    *,
    max_width: int = DEFAULT_IMAGE_MAX_WIDTH,
) -> tuple[int, int] | None:
    size = read_local_image_size(image_path)
    if not size:
        return None
    width, height = size
    if width <= 0 or height <= 0:
        return None
    target_width = min(width, max(1, max_width))
    target_height = max(1, round(height * target_width / width))
    return target_width, target_height


def markdown_order_key(md_path: Path, source_dir: Path) -> tuple[Any, ...]:
    rel = md_path.relative_to(source_dir)
    key: list[Any] = []
    for part in rel.parts[:-1]:
        key.extend([part, 1])
    filename = rel.parts[-1]
    stem = filename[:-3] if filename.lower().endswith(".md") else Path(filename).stem
    # 让 A.md 的 key 变成 A,0；A/子文档.md 变成 A,1,子文档,0，从而父文档先导入。
    key.extend([stem, 0])
    return tuple(key)


def scan_markdown_source(source_dir: Path, limit: int = 0) -> dict[str, Any]:
    source_dir = source_dir.resolve()
    if not source_dir.exists():
        raise ExportError(f"Markdown 目录不存在：{source_dir}")
    if not source_dir.is_dir():
        raise ExportError(f"Markdown 来源必须是目录：{source_dir}")

    docs: list[dict[str, Any]] = []
    for md_path in sorted(source_dir.rglob("*.md"), key=lambda path: markdown_order_key(path, source_dir)):
        rel = md_path.relative_to(source_dir)
        if any(part.lower() == "assets" for part in rel.parts):
            continue
        title = normalize_title_from_md(md_path)
        docs.append(
            {
                "path": str(md_path),
                "relativePath": rel.as_posix(),
                "title": title,
                "size": md_path.stat().st_size,
                "level": max(0, len(rel.parts) - 1),
            }
        )
        if limit and len(docs) >= limit:
            break

    return {
        "sourceDir": str(source_dir),
        "docCount": len(docs),
        "docs": docs,
    }


def select_source_file(args: argparse.Namespace) -> Path:
    if getattr(args, "source_file", None):
        source_file = Path(args.source_file).resolve()
        if not source_file.exists():
            raise ExportError(f"测试 Markdown 文件不存在：{source_file}")
        if source_file.suffix.lower() != ".md":
            raise ExportError(f"测试文件必须是 Markdown：{source_file}")
        return source_file

    source = scan_markdown_source(Path(args.source_dir), limit=1)
    docs = source.get("docs") or []
    if not docs:
        raise ExportError("本地 Markdown 目录里没有找到可导入的 .md 文件")
    return Path(docs[0]["path"]).resolve()


def summarize_wiki_tree(tree: dict[str, Any]) -> dict[str, Any]:
    ordered = order_tree(tree)
    docs = [item for item in ordered if item.get("url")]
    return {
        "spaceId": tree.get("spaceId"),
        "spaceName": (tree.get("space") or {}).get("name") or (tree.get("space") or {}).get("space_name") or "",
        "rootCount": len(tree.get("rootList") or []),
        "nodeCount": len(tree.get("nodes") or {}),
        "docCount": len(docs),
        "sampleNodes": [
            {
                "title": item.get("title") or "",
                "wikiToken": item.get("wiki_token") or "",
                "objToken": item.get("obj_token") or "",
                "objType": item.get("obj_type"),
                "level": item.get("level", 0),
                "url": item.get("url") or "",
            }
            for item in ordered[: min(len(ordered), 12)]
        ],
    }


def probe_target_wiki(args: argparse.Namespace) -> dict[str, Any]:
    host, _origin, wiki_token, wiki_url = parse_wiki_url(args.wiki_url)
    cdp, chrome_proc = connect_wiki_browser(args, wiki_url, host, wiki_token)
    try:
        auth_file = auth_path_from_args(args)
        if auth_file.exists() and not args.skip_auth_load:
            cookie_count = load_auth_state(cdp, auth_file)
            emit(args, f"Loaded {cookie_count} auth cookies from {auth_file}")
            cdp.navigate(wiki_url)
            time.sleep(2)
        elif not auth_file.exists():
            emit(args, f"未找到飞书登录凭证文件：{auth_file}")
            emit(args, "请先点击“登录并保存凭证”，在浏览器完成飞书登录后再探测目标 Wiki。")
        try:
            tree = load_wiki_tree(cdp, wiki_url, wiki_token, args)
        except ExportError as exc:
            if "飞书页面没有加载完成" in str(exc):
                raise ExportError("目标飞书 Wiki 未登录或无权限访问。请先点击“登录并保存凭证”，完成登录后再重试。") from exc
            raise
        summary = summarize_wiki_tree(tree)
        summary.update(
            {
                "provider": "feishu-import-probe",
                "wikiUrl": wiki_url,
                "host": host,
                "targetWikiToken": wiki_token,
                "readOnly": True,
            }
        )
        return summary
    finally:
        cdp.close()
        if chrome_proc and args.close_started_chrome:
            chrome_proc.terminate()


def build_import_plan(args: argparse.Namespace) -> dict[str, Any]:
    source = scan_markdown_source(Path(args.source_dir), limit=args.limit)
    wiki = probe_target_wiki(args)
    docs = source["docs"]
    return {
        "provider": "feishu-import-plan",
        "readOnly": True,
        "wiki": wiki,
        "source": {
            "sourceDir": source["sourceDir"],
            "docCount": source["docCount"],
            "sampleDocs": docs[: min(len(docs), 12)],
        },
        "nextWritePath": [
            "upload Markdown file through Feishu Drive upload API",
            "create import task to convert md into docx",
            "move generated docx into target Wiki space",
            "repeat by local directory order after single-file test succeeds",
        ],
    }


def read_openapi_json(response: urllib.response.addinfourl, action: str) -> dict[str, Any]:
    body = response.read().decode("utf-8", errors="replace")
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ExportError(f"{action} 返回的不是 JSON：{body[:500]}") from exc
    code = data.get("code", 0)
    if code not in (0, "0"):
        message = data.get("msg") or data.get("message") or json.dumps(data, ensure_ascii=False)
        raise ExportError(f"{action} 失败：code={code} msg={message}")
    return data


def openapi_json(
    method: str,
    path: str,
    *,
    access_token: str | None = None,
    payload: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
    action: str = "调用飞书 OpenAPI",
) -> dict[str, Any]:
    url = OPENAPI_BASE + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return read_openapi_json(response, action)
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        permission_error = build_permission_error(action, exc.code, text)
        if permission_error:
            raise permission_error from exc
        upload_forbidden_error = build_upload_forbidden_error(action, exc.code, text)
        if upload_forbidden_error:
            raise upload_forbidden_error from exc
        wiki_node_permission_error = build_wiki_node_permission_error(action, exc.code, text)
        if wiki_node_permission_error:
            raise wiki_node_permission_error from exc
        raise ExportError(f"{action} HTTP {exc.code}：{text[:800]}") from exc


def encode_multipart(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"----WandaoBoundary{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    content = file_path.read_bytes()
    chunks.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{file_path.name}"\r\n'
            ).encode("utf-8"),
            b"Content-Type: text/markdown\r\n\r\n",
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    return b"".join(chunks), boundary


def openapi_multipart_upload(
    path: str,
    *,
    access_token: str,
    fields: dict[str, str],
    file_path: Path,
    action: str,
) -> dict[str, Any]:
    body, boundary = encode_multipart(fields, "file", file_path)
    request = urllib.request.Request(
        OPENAPI_BASE + path,
        data=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return read_openapi_json(response, action)
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        permission_error = build_permission_error(action, exc.code, text)
        if permission_error:
            raise permission_error from exc
        upload_forbidden_error = build_upload_forbidden_error(action, exc.code, text)
        if upload_forbidden_error:
            raise upload_forbidden_error from exc
        wiki_node_permission_error = build_wiki_node_permission_error(action, exc.code, text)
        if wiki_node_permission_error:
            raise wiki_node_permission_error from exc
        raise ExportError(f"{action} HTTP {exc.code}：{text[:800]}") from exc


def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    data = openapi_json(
        "POST",
        "/auth/v3/tenant_access_token/internal",
        payload={"app_id": app_id, "app_secret": app_secret},
        action="获取 tenant_access_token",
    )
    token = data.get("tenant_access_token") or (data.get("data") or {}).get("tenant_access_token")
    if not token:
        raise ExportError("获取 tenant_access_token 成功但返回里没有 token")
    return str(token)


def check_app_setup(args: argparse.Namespace) -> dict[str, Any]:
    app_id = get_config_value(args, "app_id", "FEISHU_APP_ID")
    app_secret = get_config_value(args, "app_secret", "FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise ExportError("请先填写飞书 App ID / App Secret，再检查应用身份。")

    access_token = get_tenant_access_token(app_id, app_secret)
    emit(args, "已获取 tenant_access_token，开始检查机器人身份。")
    bot_url = FEISHU_BOT_SETUP_URL_TEMPLATE.format(app_id=urllib.parse.quote(app_id))
    try:
        data = openapi_json("GET", "/bot/v3/info", access_token=access_token, action="获取飞书机器人信息")
        bot = data.get("bot") if isinstance(data.get("bot"), dict) else {}
        result = {
            "provider": "feishu-import-app-check",
            "appId": app_id,
            "hasBot": True,
            "bot": {
                "appName": bot.get("app_name") or "",
                "openId": bot.get("open_id") or "",
                "activateStatus": bot.get("activate_status"),
            },
            "nextStep": "应用身份可用。若目标 Wiki 仍提示 no destination parent node permission，请点击“授权目标 Wiki 文档应用”，把当前企业自建应用添加为可编辑文档应用。",
        }
        emit(args, f"机器人已启用：{result['bot']['appName'] or result['bot']['openId']}")
        return result
    except ExportError as exc:
        text = str(exc)
        if "11205" not in text and "do not have bot" not in text:
            raise
        emit(args, "当前应用还没有机器人身份。新应用建议先启用机器人并发布版本，便于飞书开放平台返回完整应用信息。")
        webbrowser.open(bot_url)
        return {
            "provider": "feishu-import-app-check",
            "appId": app_id,
            "hasBot": False,
            "botSetupUrl": bot_url,
            "nextStep": "已打开飞书开放平台机器人配置页。请启用机器人能力并发布应用新版本；之后点击“授权目标 Wiki 文档应用”。",
        }


def get_bot_info(access_token: str) -> dict[str, Any]:
    data = openapi_json("GET", "/bot/v3/info", access_token=access_token, action="获取飞书机器人信息")
    bot = data.get("bot") if isinstance(data.get("bot"), dict) else {}
    if not bot.get("open_id"):
        raise ExportError(f"飞书应用已返回机器人信息，但缺少 open_id：{json.dumps(data, ensure_ascii=False)[:800]}")
    return bot


def grant_wiki_permission_to_app_bot(
    args: argparse.Namespace,
    access_token: str,
    *,
    wiki_token: str,
    perm: str = "edit",
) -> dict[str, Any]:
    bot = get_bot_info(access_token)
    bot_open_id = str(bot.get("open_id") or "")
    bot_name = str(bot.get("app_name") or bot_open_id)
    emit(args, f"尝试给当前应用机器人授权目标 Wiki：{bot_name}")
    data = openapi_json(
        "POST",
        f"/drive/v1/permissions/{urllib.parse.quote(wiki_token)}/members/batch_create",
        access_token=access_token,
        query={"type": "wiki"},
        payload={
            "members": [
                {
                    "member_type": "openid",
                    "member_id": bot_open_id,
                    "perm": perm,
                }
            ]
        },
        action="通过旧 OpenAPI 尝试给目标 Wiki 授权",
    )
    return {
        "wikiToken": wiki_token,
        "botName": bot_name,
        "botOpenId": bot_open_id,
        "perm": perm,
        "result": data.get("data") if isinstance(data.get("data"), dict) else data,
    }


def grant_target_wiki_permission(args: argparse.Namespace) -> dict[str, Any]:
    app_id = get_config_value(args, "app_id", "FEISHU_APP_ID")
    app_secret = get_config_value(args, "app_secret", "FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise ExportError("请填写飞书 App ID / App Secret 后再授权目标 Wiki。")
    if not args.wiki_url:
        raise ExportError("请填写目标飞书 Wiki URL 后再授权。")
    _host, _base_url, target_wiki_token, _wiki_url = parse_wiki_url(args.wiki_url)
    wiki_token = (get_config_value(args, "parent_wiki_token") or target_wiki_token).strip()
    if not wiki_token:
        raise ExportError("无法确定目标 Wiki 父节点 token。")
    access_token = get_tenant_access_token(app_id, app_secret)
    grant = grant_wiki_permission_to_app_bot(
        args,
        access_token,
        wiki_token=wiki_token,
        perm=str(getattr(args, "wiki_grant_perm", None) or "edit"),
    )
    grant["provider"] = "feishu-import-wiki-grant"
    return grant


def cdp_wait_for_value(
    cdp: CDPClient,
    expression: str,
    *,
    timeout: float = 15,
    interval: float = 0.3,
    args: argparse.Namespace | None = None,
) -> Any:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        check_stopped(args)
        try:
            value = cdp.evaluate(expression, timeout=10)
            if value:
                return value
        except Exception as exc:
            last_error = str(exc)
        time.sleep(interval)
    raise ExportError(last_error or "等待飞书页面元素超时")


def cdp_click_point(cdp: CDPClient, x: float, y: float) -> None:
    params = {"x": float(x), "y": float(y), "button": "left", "clickCount": 1}
    cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": float(x), "y": float(y)}, timeout=10)
    cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", **params}, timeout=10)
    cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", **params}, timeout=10)


def cdp_move_to_point(cdp: CDPClient, x: float, y: float) -> None:
    cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": float(x), "y": float(y)}, timeout=10)


def cdp_press_escape(cdp: CDPClient) -> None:
    cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27}, timeout=10)
    cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27}, timeout=10)


def cdp_click_rect(cdp: CDPClient, rect: dict[str, Any]) -> None:
    cdp_click_point(cdp, float(rect["x"]), float(rect["y"]))


def open_feishu_doc_app_dialog(cdp: CDPClient, args: argparse.Namespace) -> None:
    dialog_ready_js = """
(() => {
  const text = document.body && document.body.innerText || "";
  return text.includes("文档应用") && !![...document.querySelectorAll("input,textarea")]
    .find(el => ((el.getAttribute("placeholder") || el.getAttribute("aria-label") || "").includes("搜索应用名称")));
})()
"""
    if cdp.evaluate(dialog_ready_js, timeout=10):
        return
    cdp_press_escape(cdp)
    time.sleep(0.4)

    top_more_button_js = r"""
(() => {
  const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const visible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  };
  const buttons = [...document.querySelectorAll('button,[role="button"]')].filter((el) => {
    if (!visible(el)) return false;
    const rect = el.getBoundingClientRect();
    const label = normalize(el.innerText || el.textContent);
    return rect.top < 90
      && rect.left > window.innerWidth * 0.55
      && !label.includes("分享")
      && !label.includes("编辑")
      && !label.includes("目录")
      && !label.includes("搜索");
  }).sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
  const target = buttons[1] || buttons[0];
  if (!target) return null;
  const rect = target.getBoundingClientRect();
  return {x: (rect.left + rect.right) / 2, y: (rect.top + rect.bottom) / 2};
})()
"""
    more_button = cdp_wait_for_value(cdp, top_more_button_js, timeout=20, args=args)
    cdp_click_rect(cdp, more_button)

    menu_more_item_js = r"""
(() => {
  const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  };
  const item = [...document.querySelectorAll('[role="menuitem"], li, div, button')]
    .filter(visible)
    .find(el => normalize(el.innerText || el.textContent) === "更多");
  if (!item) return null;
  const rect = item.getBoundingClientRect();
  return {x: (rect.left + rect.right) / 2, y: (rect.top + rect.bottom) / 2};
})()
"""
    more_item = cdp_wait_for_value(cdp, menu_more_item_js, timeout=15, args=args)
    cdp_move_to_point(cdp, more_item["x"], more_item["y"])
    time.sleep(0.5)
    cdp_click_rect(cdp, more_item)

    add_app_item_js = r"""
(() => {
  const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  };
  const item = [...document.querySelectorAll('[role="menuitem"], li, div, button')]
    .filter(visible)
    .find(el => normalize(el.innerText || el.textContent) === "添加文档应用");
  if (!item) return null;
  const rect = item.getBoundingClientRect();
  return {x: (rect.left + rect.right) / 2, y: (rect.top + rect.bottom) / 2};
})()
"""
    add_item = cdp_wait_for_value(cdp, add_app_item_js, timeout=15, args=args)
    cdp_click_rect(cdp, add_item)
    cdp_wait_for_value(cdp, dialog_ready_js, timeout=20, args=args)


def find_wiki_node_info(tree: dict[str, Any], wiki_token: str) -> dict[str, Any]:
    nodes = tree.get("nodes") if isinstance(tree.get("nodes"), dict) else {}
    node = nodes.get(wiki_token)
    if isinstance(node, dict):
        return node
    for item in nodes.values():
        if isinstance(item, dict) and item.get("wiki_token") == wiki_token:
            return item
    return {}


def get_current_wiki_node_info(
    cdp: CDPClient,
    wiki_token: str,
    *,
    space_id: str = "",
) -> dict[str, Any]:
    payload = {"wikiToken": wiki_token, "spaceId": space_id}
    expression = r"""
async (payload) => {
  const wikiToken = payload.wikiToken;
  let spaceId = payload.spaceId || "";
  if (!spaceId) {
    const entry = performance.getEntriesByType("resource")
      .map((item) => item.name || "")
      .find((url) => url.includes("space_id=") && url.includes("/space/api/wiki/v2/"));
    if (entry) {
      try {
        spaceId = new URL(entry).searchParams.get("space_id") || "";
      } catch (_) {}
    }
  }
  if (!spaceId) {
    const text = document.documentElement ? document.documentElement.innerHTML : "";
    const match = text.match(/space_id["'=:\s]+(\d{8,})/);
    if (match) spaceId = match[1];
  }
  if (!spaceId) return { error: "missing-space-id" };
  const url = `/space/api/wiki/v2/tree/get_node/?wiki_token=${encodeURIComponent(wikiToken)}&space_id=${encodeURIComponent(spaceId)}&expand_shortcut=true&with_deleted=true`;
  const response = await fetch(url, { credentials: "include" });
  const data = await response.json();
  if (!response.ok || (data.code !== 0 && data.code !== undefined)) {
    return { error: "get-node-failed", status: response.status, data, spaceId };
  }
  return { ...(data.data || {}), space_id: spaceId };
}
"""
    result = cdp.evaluate(f"({expression})({json.dumps(payload, ensure_ascii=False)})", timeout=45)
    if not isinstance(result, dict):
        raise ExportError(f"解析目标 Wiki 节点失败：{result!r}")
    if result.get("error"):
        raise ExportError(f"解析目标 Wiki 节点失败：{json.dumps(result, ensure_ascii=False)[:800]}")
    return result


def get_target_doc_app_status(
    cdp: CDPClient,
    *,
    obj_token: str,
    obj_type: int,
    app_name: str,
    app_id: str,
) -> dict[str, Any]:
    payload = {
        "objToken": obj_token,
        "objType": obj_type,
        "appName": app_name,
        "appId": app_id,
    }
    result = cdp.evaluate(f"({FEISHU_DOC_APP_STATUS_JS})({json.dumps(payload, ensure_ascii=False)})", timeout=45)
    if not isinstance(result, dict):
        raise ExportError(f"检测目标 Wiki 文档应用授权失败：{result!r}")
    return result


def setup_target_wiki_doc_app(args: argparse.Namespace) -> dict[str, Any]:
    if not getattr(args, "yes", False):
        raise ExportError("这是目标 Wiki 授权操作。命令行执行时必须增加 --yes 明确确认。")
    app_id = get_config_value(args, "app_id", "FEISHU_APP_ID")
    app_secret = get_config_value(args, "app_secret", "FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise ExportError("请先填写飞书 App ID / App Secret，再授权目标 Wiki 文档应用。")
    if not args.wiki_url:
        raise ExportError("请先填写目标飞书 Wiki URL。")

    access_token = get_tenant_access_token(app_id, app_secret)
    bot = get_bot_info(access_token)
    app_name = str(bot.get("app_name") or "").strip()
    if not app_name:
        raise ExportError("当前应用已启用机器人，但飞书没有返回应用名称。请检查开放平台机器人配置。")

    host, _origin, wiki_token, wiki_url = parse_wiki_url(args.wiki_url)
    cdp, chrome_proc = connect_wiki_browser(args, wiki_url, host, wiki_token)
    try:
        auth_file = auth_path_from_args(args)
        current_login_ready = False
        try:
            wait_for_wiki_ready(cdp, timeout=8, args=args)
            current_login_ready = True
            emit(args, "已复用当前浏览器里的飞书登录态。")
        except ExportError:
            current_login_ready = False
        if not current_login_ready and auth_file.exists() and not args.skip_auth_load:
            cookie_count = load_auth_state(cdp, auth_file)
            emit(args, f"Loaded {cookie_count} auth cookies from {auth_file}")
            cdp.navigate(wiki_url)
            time.sleep(2)
        elif not current_login_ready and not auth_file.exists():
            emit(args, f"未找到飞书登录凭证文件：{auth_file}")
            emit(args, "会继续使用当前浏览器登录态；如果页面要求登录，请先点击“登录并保存凭证”。")
        if not current_login_ready:
            wait_for_wiki_ready(cdp, timeout=45, args=args)
        configured_space_id = get_config_value(args, "space_id")
        try:
            node = get_current_wiki_node_info(cdp, wiki_token, space_id=configured_space_id)
        except ExportError:
            tree = load_wiki_tree(cdp, wiki_url, wiki_token, args)
            node = find_wiki_node_info(tree, wiki_token)
        obj_token = str(node.get("obj_token") or "").strip()
        obj_type = int(node.get("obj_type") or 22)
        if not obj_token:
            raise ExportError("无法从目标 Wiki 页面解析真实文档 token，请先点击“探测目标 Wiki”确认页面可访问。")

        emit(args, f"开始检查目标 Wiki 文档应用授权：{app_name}")
        before_status = get_target_doc_app_status(
            cdp,
            obj_token=obj_token,
            obj_type=obj_type,
            app_name=app_name,
            app_id=app_id,
        )
        if before_status.get("alreadyAdded"):
            result = {"status": "already-added", "before": before_status}
        else:
            emit(args, "当前目标 Wiki 还没有添加这个文档应用，开始尝试打开“添加文档应用”弹窗。")
            try:
                ui_result = cdp.evaluate(f"({FEISHU_ADD_DOC_APP_JS})({json.dumps(app_name, ensure_ascii=False)})", timeout=90)
                if not isinstance(ui_result, dict):
                    ui_result = {"status": "unknown", "raw": ui_result}
            except Exception as exc:
                ui_result = {
                    "status": "manual-required",
                    "error": str(exc),
                    "nextStep": "请在已打开的目标 Wiki 右上角选择“... -> 更多 -> 添加文档应用”，搜索当前应用名称并添加为可编辑。",
                }
            after_status = get_target_doc_app_status(
                cdp,
                obj_token=obj_token,
                obj_type=obj_type,
                app_name=app_name,
                app_id=app_id,
            )
            result = {
                "status": "added" if after_status.get("alreadyAdded") else ui_result.get("status", "manual-required"),
                "before": before_status,
                "after": after_status,
                "uiResult": ui_result,
            }
            if not after_status.get("alreadyAdded"):
                result["nextStep"] = (
                    "如果弹窗没有自动完成，请在目标 Wiki 右上角选择“... -> 更多 -> 添加文档应用”，"
                    f"搜索“{app_name}”，添加为可编辑后再重试导入。"
                )
        result.update(
            {
                "provider": "feishu-import-target-wiki-doc-app",
                "wikiUrl": wiki_url,
                "targetWikiToken": wiki_token,
                "targetObjToken": obj_token,
                "targetObjType": obj_type,
                "appId": app_id,
                "appName": app_name,
            }
        )
        if result.get("status") in {"already-added", "added"}:
            emit(args, "目标 Wiki 文档应用授权已就绪。")
        else:
            emit(args, "未能自动完成目标 Wiki 文档应用授权，请按返回的 nextStep 操作。")
        return result
    finally:
        cdp.close()
        if chrome_proc and args.close_started_chrome:
            chrome_proc.terminate()


def get_root_folder_token(access_token: str) -> str:
    data = openapi_json(
        "GET",
        "/drive/explorer/v2/root_folder/meta",
        access_token=access_token,
        action="获取云空间根文件夹",
    )
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    token = root.get("token")
    if not token:
        raise ExportError(f"获取云空间根文件夹成功但没有返回 token：{json.dumps(data, ensure_ascii=False)[:800]}")
    return str(token)


def upload_markdown_file(access_token: str, md_path: Path, drive_folder_token: str) -> str:
    data = openapi_multipart_upload(
        "/drive/v1/files/upload_all",
        access_token=access_token,
        fields={
            "file_name": md_path.name,
            "parent_type": "explorer",
            "parent_node": drive_folder_token,
            "size": str(md_path.stat().st_size),
        },
        file_path=md_path,
        action="上传 Markdown 文件",
    )
    file_token = (data.get("data") or {}).get("file_token") or data.get("file_token")
    if not file_token:
        raise ExportError(f"上传成功但没有返回 file_token：{json.dumps(data, ensure_ascii=False)[:800]}")
    return str(file_token)


def upload_markdown_file_with_fallback(
    args: argparse.Namespace,
    access_token: str,
    md_path: Path,
    drive_folder_token: str,
    *,
    token_from_config: bool,
) -> tuple[str, str]:
    try:
        return upload_markdown_file(access_token, md_path, drive_folder_token), drive_folder_token
    except FeishuUploadForbiddenError:
        if not token_from_config:
            raise
        emit(args, "配置的云空间文件夹 token 不可用，自动尝试当前应用的云空间根文件夹。")
        fallback_token = get_root_folder_token(access_token)
        return upload_markdown_file(access_token, md_path, fallback_token), fallback_token


def upload_docx_image_media(access_token: str, image_path: Path, image_block_id: str) -> str:
    mime_type = guess_type(str(image_path))[0] or "application/octet-stream"
    data = openapi_multipart_upload(
        "/drive/v1/medias/upload_all",
        access_token=access_token,
        fields={
            "file_name": image_path.name,
            "parent_type": "docx_image",
            "parent_node": image_block_id,
            "size": str(image_path.stat().st_size),
        },
        file_path=image_path,
        action=f"上传 docx 图片素材({mime_type})",
    )
    file_token = (data.get("data") or {}).get("file_token") or data.get("file_token")
    if not file_token:
        raise ExportError(f"上传图片素材成功但没有返回 file_token：{json.dumps(data, ensure_ascii=False)[:800]}")
    return str(file_token)


def create_import_task(access_token: str, file_token: str, title: str, import_mount_key: str = "") -> str:
    data = openapi_json(
        "POST",
        "/drive/v1/import_tasks",
        access_token=access_token,
        payload={
            "file_extension": "md",
            "file_token": file_token,
            "type": "docx",
            "file_name": title,
            "point": {
                "mount_type": 1,
                "mount_key": import_mount_key,
            },
        },
        action="创建 Markdown 导入任务",
    )
    ticket = (data.get("data") or {}).get("ticket") or data.get("ticket")
    if not ticket:
        raise ExportError(f"创建导入任务成功但没有返回 ticket：{json.dumps(data, ensure_ascii=False)[:800]}")
    return str(ticket)


def rename_drive_file(access_token: str, file_token: str, file_type: str, title: str) -> dict[str, Any]:
    # 导入任务使用 ASCII 临时名规避飞书中文标题异步失败，导入成功后再改回原 Markdown 标题。
    return openapi_json(
        "PATCH",
        f"/drive/v1/files/{urllib.parse.quote(file_token)}",
        access_token=access_token,
        query={"type": file_type},
        payload={"new_title": title},
        action="重命名飞书文档",
    )


def list_docx_blocks(access_token: str, document_id: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    page_token = ""
    while True:
        query = {"page_size": "500"}
        if page_token:
            query["page_token"] = page_token
        data = openapi_json(
            "GET",
            f"/docx/v1/documents/{urllib.parse.quote(document_id)}/blocks",
            access_token=access_token,
            query=query,
            action="读取飞书文档块",
        )
        inner = data.get("data") if isinstance(data.get("data"), dict) else data
        items = inner.get("items") or inner.get("blocks") or []
        if isinstance(items, list):
            blocks.extend([item for item in items if isinstance(item, dict)])
        if not inner.get("has_more"):
            break
        page_token = str(inner.get("page_token") or "")
        if not page_token:
            break
    return blocks


def replace_docx_image_block(
    access_token: str,
    document_id: str,
    block_id: str,
    image_token: str,
    *,
    width: int | None = None,
    height: int | None = None,
    align: int | None = 2,
) -> dict[str, Any]:
    replace_image: dict[str, Any] = {"token": image_token}
    if width and height:
        # 飞书会保留原图片块尺寸；占位图尺寸不准时必须显式写入原图比例，避免图片被拉伸。
        replace_image.update({"width": width, "height": height})
    if align is not None:
        replace_image["align"] = align
    return openapi_json(
        "PATCH",
        f"/docx/v1/documents/{urllib.parse.quote(document_id)}/blocks/{urllib.parse.quote(block_id)}",
        access_token=access_token,
        payload={"replace_image": replace_image},
        action="替换飞书图片块",
    )


def repair_imported_local_images(
    args: argparse.Namespace,
    access_token: str,
    *,
    document_id: str,
    md_path: Path,
) -> dict[str, Any]:
    local_images = collect_local_images_from_markdown(md_path)
    if not local_images:
        return {"localImageCount": 0, "imageBlockCount": 0, "replacedCount": 0, "items": []}

    blocks = list_docx_blocks(access_token, document_id)
    image_blocks = [block for block in blocks if block.get("block_type") in (27, "27") or block.get("image")]
    replaced: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    max_width = int(getattr(args, "image_max_width", DEFAULT_IMAGE_MAX_WIDTH) or DEFAULT_IMAGE_MAX_WIDTH)
    for index, image in enumerate(local_images):
        if stop_requested(args):
            raise ExportStopped("用户停止了飞书图片补全任务")
        if index >= len(image_blocks):
            failures.append({"url": image["url"], "path": image["path"], "error": "没有对应的飞书图片块"})
            continue
        block_id = str(image_blocks[index].get("block_id") or "")
        if not block_id:
            failures.append({"url": image["url"], "path": image["path"], "error": "图片块缺少 block_id"})
            continue
        try:
            image_path = Path(image["path"])
            display_size = fit_image_display_size(image_path, max_width=max_width)
            display_width, display_height = display_size if display_size else (None, None)
            block_image = image_blocks[index].get("image") if isinstance(image_blocks[index].get("image"), dict) else {}
            align = block_image.get("align") if block_image else 2
            token = upload_docx_image_media(access_token, image_path, block_id)
            replace_docx_image_block(
                access_token,
                document_id,
                block_id,
                token,
                width=display_width,
                height=display_height,
                align=int(align) if align is not None else 2,
            )
            item = {"url": image["url"], "path": image["path"], "blockId": block_id, "token": token}
            if display_width and display_height:
                item.update({"width": str(display_width), "height": str(display_height)})
            replaced.append(item)
        except Exception as exc:
            failures.append({"url": image["url"], "path": image["path"], "blockId": block_id, "error": str(exc)})

    return {
        "localImageCount": len(local_images),
        "imageBlockCount": len(image_blocks),
        "replacedCount": len(replaced),
        "failureCount": len(failures),
        "items": replaced,
        "failures": failures,
    }


def normalize_import_result(data: dict[str, Any]) -> dict[str, Any]:
    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    result = inner.get("result") if isinstance(inner.get("result"), dict) else inner
    return result if isinstance(result, dict) else {}


def poll_import_task(args: argparse.Namespace, access_token: str, ticket: str) -> dict[str, Any]:
    deadline = time.time() + max(5.0, float(args.poll_timeout))
    started_at = time.time()
    last_result: dict[str, Any] = {}
    while time.time() < deadline:
        if stop_requested(args):
            raise ExportStopped("用户停止了飞书导入任务轮询")
        time.sleep(max(0.5, float(args.poll_interval)))
        data = openapi_json(
            "GET",
            f"/drive/v1/import_tasks/{urllib.parse.quote(ticket)}",
            access_token=access_token,
            action="查询 Markdown 导入任务",
        )
        result = normalize_import_result(data)
        last_result = result
        status = result.get("job_status", result.get("status"))
        if result.get("token") or result.get("url") or status in (0, "0", "success", "succeeded"):
            return result
        message = str(result.get("job_error_msg") or result.get("error_msg") or "").strip()
        if status in (2, "2") and not message and time.time() - started_at < 12:
            continue
        if status in (2, 3, -1, "2", "3", "-1", "failed", "error"):
            raise ExportError(f"Markdown 导入任务失败：{json.dumps(result, ensure_ascii=False)}")
    raise ExportError(f"Markdown 导入任务超时，最后状态：{json.dumps(last_result, ensure_ascii=False)}")


def move_doc_to_wiki(
    args: argparse.Namespace,
    access_token: str,
    *,
    space_id: str,
    parent_wiki_token: str,
    obj_type: str,
    obj_token: str,
) -> dict[str, Any]:
    data = openapi_json(
        "POST",
        f"/wiki/v2/spaces/{urllib.parse.quote(space_id)}/nodes/move_docs_to_wiki",
        access_token=access_token,
        payload={
            "parent_wiki_token": parent_wiki_token,
            "obj_type": obj_type,
            "obj_token": obj_token,
        },
        action="移动云文档到飞书 Wiki",
    )
    result = data.get("data") if isinstance(data.get("data"), dict) else data
    move_result = result.get("move_result") if isinstance(result, dict) else None
    if isinstance(move_result, list) and move_result:
        first = move_result[0] if isinstance(move_result[0], dict) else {}
        if first.get("status") in (0, "0", "success", "succeeded"):
            return first
    if result.get("wiki_token") or result.get("node"):
        return result
    task_id = result.get("task_id")
    if not task_id:
        return result
    deadline = time.time() + max(5.0, float(args.poll_timeout))
    last_result = result
    while time.time() < deadline:
        if stop_requested(args):
            raise ExportStopped("用户停止了飞书 Wiki 移动任务轮询")
        task_data = openapi_json(
            "GET",
            f"/wiki/v2/tasks/{urllib.parse.quote(str(task_id))}",
            access_token=access_token,
            query={"task_type": "move"},
            action="查询飞书 Wiki 移动任务",
        )
        task_result = task_data.get("data") if isinstance(task_data.get("data"), dict) else task_data
        task = task_result.get("task") if isinstance(task_result.get("task"), dict) else task_result
        last_result = task
        move_result = task.get("move_result") if isinstance(task, dict) else None
        if isinstance(move_result, list) and move_result:
            first = move_result[0] if isinstance(move_result[0], dict) else {}
            if first.get("status") in (0, "0", "success", "succeeded"):
                return first
        status = task.get("status") or task.get("task_status")
        if task.get("wiki_token") or task.get("node") or status in (0, "0", "success", "succeeded"):
            return task
        if status in (2, 3, -1, "2", "3", "-1", "failed", "error"):
            raise ExportError(f"飞书 Wiki 移动任务失败：{json.dumps(task, ensure_ascii=False)}")
        time.sleep(max(0.5, float(args.poll_interval)))
    raise ExportError(f"飞书 Wiki 移动任务超时，最后状态：{json.dumps(last_result, ensure_ascii=False)}")


def import_one_with_openapi(args: argparse.Namespace) -> dict[str, Any]:
    if not getattr(args, "yes", False):
        raise ExportError("这是写入操作。命令行执行时必须增加 --yes 明确确认。")
    app_id = get_config_value(args, "app_id", "FEISHU_APP_ID")
    app_secret = get_config_value(args, "app_secret", "FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise ExportError("请填写飞书 App ID / App Secret，或设置 FEISHU_APP_ID / FEISHU_APP_SECRET 环境变量")

    md_path = select_source_file(args)
    upload_md_path = md_path
    upload_temp_dir: Path | None = None
    title = normalize_title_from_md(md_path)
    import_title = build_safe_import_title(md_path)
    drive_folder_token = get_config_value(args, "drive_folder_token")
    drive_folder_token_from_config = bool(drive_folder_token)

    try:
        emit(
            args,
            f"准备导入单篇 Markdown：{md_path.name}",
            event="document.import.started",
            doc={"title": title, "path": str(md_path)},
            step="prepare",
        )
        if not getattr(args, "skip_image_repair", False) and collect_local_images_from_markdown(md_path):
            upload_md_path, upload_temp_dir = prepare_markdown_for_image_blocks(md_path)
            if upload_temp_dir:
                emit(args, "已生成图片占位用临时 Markdown，导入后会替换为真实本地图片")
        access_token = get_tenant_access_token(app_id, app_secret)
        emit(args, "已获取 tenant_access_token")
        if not drive_folder_token:
            emit(args, "未填写云空间文件夹 token，自动获取根文件夹 token")
            drive_folder_token = get_root_folder_token(access_token)
        file_token, drive_folder_token = upload_markdown_file_with_fallback(
            args,
            access_token,
            upload_md_path,
            drive_folder_token,
            token_from_config=drive_folder_token_from_config,
        )
        emit(args, f"已上传 Markdown，file_token={file_token}")
        import_mount_key = get_config_value(args, "import_mount_key")
        ticket = create_import_task(access_token, file_token, import_title, import_mount_key)
        emit(
            args,
            f"已创建导入任务，ticket={ticket}",
            event="api.response",
            step="create_import_task",
            doc={"title": title, "path": str(md_path)},
            result={"ticket": ticket},
        )
        import_result = poll_import_task(args, access_token, ticket)
        doc_token = str(import_result.get("token") or import_result.get("doc_token") or import_result.get("obj_token") or "")
        doc_url = str(import_result.get("url") or "")
        if not doc_token:
            raise ExportError(f"导入完成但没有返回文档 token：{json.dumps(import_result, ensure_ascii=False)}")
    finally:
        if upload_temp_dir:
            shutil.rmtree(upload_temp_dir, ignore_errors=True)
    obj_type = (get_config_value(args, "obj_type") or "docx").strip()

    rename_result: dict[str, Any] | None = None
    if title and not getattr(args, "skip_rename", False):
        emit(args, f"准备重命名飞书文档：{title}")
        rename_result = rename_drive_file(access_token, doc_token, obj_type, title)

    image_repair_result: dict[str, Any] | None = None
    image_repair_error = ""
    if not getattr(args, "skip_image_repair", False):
        local_image_count = len(collect_local_images_from_markdown(md_path))
        if local_image_count:
            emit(
                args,
                f"准备修复本地图片：{local_image_count} 张",
                event="resource.upload.started",
                doc={"title": title, "path": str(md_path)},
                stats={"localImageCount": local_image_count},
            )
            try:
                image_repair_result = repair_imported_local_images(
                    args,
                    access_token,
                    document_id=doc_token,
                    md_path=md_path,
                )
                emit(
                    args,
                    f"本地图片修复完成：成功 {image_repair_result.get('replacedCount', 0)} / {local_image_count}",
                    event="resource.upload.completed",
                    doc={"title": title, "path": str(md_path), "id": doc_token},
                    stats=image_repair_result,
                )
            except Exception as exc:
                image_repair_error = str(exc)
                emit(
                    args,
                    f"图片修复失败：{image_repair_error}",
                    event="resource.upload.failed",
                    level="error",
                    doc={"title": title, "path": str(md_path), "id": doc_token},
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
                if getattr(args, "require_image_repair", False):
                    raise

    move_result: dict[str, Any] | None = None
    wiki_grant_result: dict[str, Any] | None = None
    if getattr(args, "move_to_wiki", False):
        host, _origin, target_wiki_token, _wiki_url = parse_wiki_url(args.wiki_url)
        space_id = get_config_value(args, "space_id")
        if not space_id:
            emit(args, "未填写 spaceId，开始从目标 Wiki 探测 spaceId")
            probe = probe_target_wiki(args)
            space_id = str(probe.get("spaceId") or "")
        if not space_id:
            raise ExportError("无法获取目标 Wiki 的 spaceId")
        parent_wiki_token = (get_config_value(args, "parent_wiki_token") or target_wiki_token).strip()
        emit(args, f"准备移动到 Wiki：host={host} spaceId={space_id} parent={parent_wiki_token}")
        try:
            move_result = move_doc_to_wiki(
                args,
                access_token,
                space_id=space_id,
                parent_wiki_token=parent_wiki_token,
                obj_type=obj_type,
                obj_token=doc_token,
            )
        except FeishuWikiNodePermissionError as exc:
            raise FeishuWikiNodePermissionError(
                f"{exc}\n\n"
                "已完成 Markdown 上传和文档导入，但移动到目标 Wiki 失败。\n"
                "请先运行“授权目标 Wiki 文档应用”，把当前企业自建应用添加到目标 Wiki 的文档应用列表，"
                "然后重新执行导入。"
            ) from exc

    result_payload = {
        "provider": "feishu-import-openapi",
        "totalDocs": 1,
        "importedDocs": 1,
        "importedCount": 1,
        "failureCount": 0,
        "sourceFile": str(md_path),
        "title": title,
        "importTaskTitle": import_title,
        "fileToken": file_token,
        "ticket": ticket,
        "docToken": doc_token,
        "docUrl": doc_url,
        "renamed": bool(rename_result),
        "renameResult": rename_result,
        "imageRepair": image_repair_result,
        "imageRepairError": image_repair_error,
        "wikiGrant": wiki_grant_result,
        "movedToWiki": bool(move_result),
        "moveResult": move_result,
    }
    result_payload = finalize_report(result_payload, provider="feishu-import-openapi", mode="import", output=md_path)
    emit(
        args,
        f"单篇 Markdown 导入完成：{title}",
        event="document.import.completed",
        doc={"title": title, "path": str(md_path), "id": doc_token},
        result={
            "movedToWiki": bool(move_result),
            "renamed": bool(rename_result),
            "imageRepairFailureCount": (image_repair_result or {}).get("failureCount", 0) if image_repair_result else 0,
        },
    )
    return result_payload


def wiki_token_from_move_result(result: dict[str, Any]) -> str:
    move_result = result.get("moveResult") if isinstance(result.get("moveResult"), dict) else result
    node = move_result.get("node") if isinstance(move_result.get("node"), dict) else {}
    return str(
        node.get("node_token")
        or move_result.get("wiki_token")
        or move_result.get("node_token")
        or ""
    )


def create_folder_placeholder_markdown(folder_title: str, folder_key: str) -> tuple[Path, Path]:
    temp_dir = Path(tempfile.mkdtemp(prefix="wandao-feishu-folder-"))
    file_name = sanitize_filename(folder_title) or "folder"
    temp_path = temp_dir / f"{file_name}.md"
    content = (
        f"# {folder_title}\n\n"
        "> 此页面由万能导自动创建，用于恢复本地 Markdown 文件夹层级。\n\n"
        f"本地目录：`{folder_key}`\n"
    )
    temp_path.write_text(content, encoding="utf-8")
    return temp_path, temp_dir


def ensure_folder_parent_token(
    args: argparse.Namespace,
    *,
    relative_path: str,
    imported_by_relative_path: dict[str, str],
    folder_tokens: dict[str, str],
    root_parent_token: str,
    folder_pages: list[dict[str, str]],
    checkpoint: Any | None = None,
) -> str:
    parent = Path(relative_path).parent
    if parent.as_posix() in ("", "."):
        return root_parent_token

    current_parent_token = root_parent_token
    accumulated_parts: list[str] = []
    for part in parent.parts:
        if part in ("", "."):
            continue
        accumulated_parts.append(part)
        folder_key = "/".join(accumulated_parts)

        # 如果存在 A.md，就用真实文档 A.md 当 A/ 子文档的父节点，不再额外创建目录页。
        candidate_md = f"{folder_key}.md"
        if candidate_md in imported_by_relative_path:
            current_parent_token = imported_by_relative_path[candidate_md]
            folder_tokens[folder_key] = current_parent_token
            save_folder_tokens(checkpoint, folder_tokens)
            continue

        if folder_key in folder_tokens:
            current_parent_token = folder_tokens[folder_key]
            continue

        folder_title = part
        emit(args, f"创建目录页：{folder_key}")
        temp_path, temp_dir = create_folder_placeholder_markdown(folder_title, folder_key)
        try:
            folder_args = argparse.Namespace(**vars(args))
            folder_args.source_file = str(temp_path)
            folder_args.parent_wiki_token = current_parent_token
            folder_args.yes = True
            folder_args.skip_image_repair = True
            folder_args.poll_timeout = getattr(args, "poll_timeout", 120)
            folder_args.poll_interval = getattr(args, "poll_interval", 2.0)
            folder_args.image_max_width = getattr(args, "image_max_width", DEFAULT_IMAGE_MAX_WIDTH)
            result = import_one_with_openapi(folder_args)
            wiki_token = wiki_token_from_move_result(result)
            if not wiki_token:
                raise ExportError(f"目录页创建成功但没有返回 Wiki token：{folder_key}")
            folder_tokens[folder_key] = wiki_token
            save_folder_tokens(checkpoint, folder_tokens)
            current_parent_token = wiki_token
            folder_pages.append(
                {
                    "folderPath": folder_key,
                    "title": folder_title,
                    "wikiToken": wiki_token,
                    "docToken": str(result.get("docToken") or ""),
                }
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    return current_parent_token


def find_import_parent_token(relative_path: str, imported_by_relative_path: dict[str, str], root_parent_token: str) -> str:
    parent = Path(relative_path).parent
    while parent and parent.as_posix() not in ("", "."):
        # 常见导出结构是 A.md + A/子文档.md；导入子文档时应挂在 A.md 对应的 Wiki 节点下。
        candidate = parent.as_posix() + ".md"
        if candidate in imported_by_relative_path:
            return imported_by_relative_path[candidate]
        parent = parent.parent
    return root_parent_token


def checkpoint_item_key(doc: dict[str, Any]) -> str:
    return f"feishu-import:{doc['relativePath']}"


def select_checkpoint_docs(
    docs: list[dict[str, Any]],
    checkpoint: Any,
    *,
    resume: bool,
    retry_failed: bool,
) -> list[dict[str, Any]]:
    if not checkpoint:
        return docs
    if retry_failed:
        return [doc for doc in docs if checkpoint.item_status(checkpoint_item_key(doc)) == "failed"]
    if resume:
        return [doc for doc in docs if checkpoint.item_status(checkpoint_item_key(doc)) != "completed"]
    return docs


def restore_completed_import_tokens(checkpoint: Any) -> dict[str, str]:
    restored: dict[str, str] = {}
    for item in checkpoint.completed_items():
        item_key = str(item.get("item_key") or "")
        if not item_key.startswith("feishu-import:"):
            continue
        metadata = json.loads(str(item.get("metadata_json") or "{}"))
        wiki_token = str(metadata.get("wikiToken") or "")
        if wiki_token:
            restored[item_key.removeprefix("feishu-import:")] = wiki_token
    return restored


def save_folder_tokens(checkpoint: Any | None, folder_tokens: dict[str, str]) -> None:
    if checkpoint:
        checkpoint.save_cursor("folder_tokens", folder_tokens)


def restore_folder_tokens(checkpoint: Any | None) -> dict[str, str]:
    if not checkpoint:
        return {}
    saved_tokens = checkpoint.load_cursor("folder_tokens", {})
    if not isinstance(saved_tokens, dict):
        return {}
    return {
        str(folder_key): str(wiki_token)
        for folder_key, wiki_token in saved_tokens.items()
        if str(folder_key) and str(wiki_token)
    }


def import_all_with_openapi(args: argparse.Namespace) -> dict[str, Any]:
    if not getattr(args, "yes", False):
        raise ExportError("这是写入操作。命令行执行时必须增加 --yes 明确确认。")
    if not getattr(args, "move_to_wiki", False):
        raise ExportError("批量导入目前需要 --move-to-wiki，用目标 Wiki 节点恢复目录层级。")

    host, _origin, target_wiki_token, _wiki_url = parse_wiki_url(args.wiki_url)
    if not get_config_value(args, "space_id"):
        emit(args, "未填写 spaceId，开始从目标 Wiki 探测 spaceId")
        probe = probe_target_wiki(args)
        args.space_id = str(probe.get("spaceId") or "")
    if not get_config_value(args, "space_id"):
        raise ExportError("无法获取目标 Wiki 的 spaceId")

    docs = scan_markdown_source(Path(args.source_dir), limit=max(0, int(getattr(args, "max_import", 0) or 0))).get("docs") or []
    imported_by_relative_path: dict[str, str] = {}
    folder_tokens: dict[str, str] = {}
    checkpoint = open_checkpoint_from_args(args, "feishu-import", "import")
    if checkpoint:
        checkpoint.start_task({"source": str(Path(args.source_dir).resolve()), "target": args.wiki_url, "totalDocs": len(docs)})
        for doc in docs:
            relative_path = str(doc.get("relativePath") or "")
            checkpoint.upsert_item(checkpoint_item_key(doc), title=relative_path, source_id=relative_path)
        if getattr(args, "resume", False) or getattr(args, "retry_failed", False):
            imported_by_relative_path = restore_completed_import_tokens(checkpoint)
            folder_tokens = restore_folder_tokens(checkpoint)
        docs = select_checkpoint_docs(
            docs,
            checkpoint,
            resume=bool(getattr(args, "resume", False)),
            retry_failed=bool(getattr(args, "retry_failed", False)),
        )
    root_parent_token = (get_config_value(args, "parent_wiki_token") or target_wiki_token).strip()
    folder_pages: list[dict[str, str]] = []
    imported: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    emit(
        args,
        f"开始批量导入 Markdown：host={host} total={len(docs)}",
        event="task.started",
        totals={"documents": len(docs)},
        target={"host": host, "spaceId": get_config_value(args, "space_id"), "parentWikiToken": root_parent_token},
        sourceDir=str(Path(args.source_dir).resolve()),
    )
    for index, doc in enumerate(docs, start=1):
        relative_path = str(doc.get("relativePath") or "")
        item_key = checkpoint_item_key(doc)
        if stop_requested(args):
            if checkpoint:
                checkpoint.fail_item(item_key, "stopped")
                checkpoint.fail_task("stopped", status="stopped")
                checkpoint.close()
            raise ExportStopped("用户停止了飞书批量导入任务")
        emit(
            args,
            f"[{index}/{len(docs)}] 导入：{relative_path}",
            event="document.import.started",
            doc={"path": relative_path, "index": index},
        )
        try:
            if checkpoint:
                checkpoint.start_item(item_key, "import")
            parent_token = ensure_folder_parent_token(
                args,
                relative_path=relative_path,
                imported_by_relative_path=imported_by_relative_path,
                folder_tokens=folder_tokens,
                root_parent_token=root_parent_token,
                folder_pages=folder_pages,
                checkpoint=checkpoint,
            )
            child_args = argparse.Namespace(**vars(args))
            child_args.source_file = str(doc["path"])
            child_args.parent_wiki_token = parent_token
            child_args.yes = True
            result = import_one_with_openapi(child_args)
            wiki_token = wiki_token_from_move_result(result)
            if wiki_token:
                imported_by_relative_path[relative_path] = wiki_token
            imported.append(
                {
                    "relativePath": relative_path,
                    "title": result.get("title") or "",
                    "wikiToken": wiki_token,
                    "docToken": result.get("docToken") or "",
                    "movedToWiki": bool(result.get("movedToWiki")),
                    "renamed": bool(result.get("renamed")),
                    "imageRepair": result.get("imageRepair"),
                    "imageRepairError": result.get("imageRepairError") or "",
                }
            )
            if checkpoint:
                checkpoint.complete_item(item_key, metadata={"wikiToken": wiki_token, "docToken": result.get("docToken") or ""})
            emit(
                args,
                f"文档导入完成：{relative_path}",
                event="document.import.completed",
                doc={"path": relative_path, "index": index, "id": wiki_token},
                result={
                    "movedToWiki": bool(result.get("movedToWiki")),
                    "renamed": bool(result.get("renamed")),
                    "imageRepairError": result.get("imageRepairError") or "",
                },
            )
        except ExportStopped:
            if checkpoint:
                checkpoint.fail_item(item_key, "stopped")
                checkpoint.fail_task("stopped", status="stopped")
                checkpoint.close()
            raise
        except Exception as exc:
            if checkpoint:
                checkpoint.fail_item(item_key, str(exc))
            failures.append({"relativePath": relative_path, "error": str(exc)})
            emit(
                args,
                f"失败：{relative_path}：{exc}",
                event="document.import.failed",
                level="error",
                doc={"path": relative_path, "index": index},
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            if isinstance(exc, (FeishuPermissionError, FeishuWikiNodePermissionError)):
                emit(args, "检测到配置级权限问题，已停止批量导入。请修复权限后重新执行。")
                break
        emit(
            args,
            f"progress {index}/{len(docs)} imported={len(imported)} failures={len(failures)}",
            event="task.progress",
            progress={"current": index, "total": len(docs)},
            stats={"importedDocs": len(imported), "failureCount": len(failures), "folderPageCount": len(folder_pages)},
        )

    result = {
        "provider": "feishu-import-openapi-batch",
        "sourceDir": str(Path(args.source_dir).resolve()),
        "total": len(docs),
        "sourceDocCount": len(docs),
        "importedDocs": len(imported),
        "folderPageCount": len(folder_pages),
        "importedCount": len(imported),
        "failureCount": len(failures),
        "folderPages": folder_pages,
        "imported": imported,
        "failures": failures,
    }
    result = finalize_report(result, provider="feishu-import-openapi-batch", mode="import", output=Path(args.source_dir).resolve())
    if checkpoint:
        checkpoint.complete_task(result)
        checkpoint.close()
    emit(
        args,
        "飞书 Markdown 批量导入完成",
        event="task.completed",
        level="success" if not failures else "warn",
        stats={"importedDocs": len(imported), "failureCount": len(failures), "folderPageCount": len(folder_pages)},
    )
    return result


def import_one_with_checkpoint(args: argparse.Namespace) -> dict[str, Any]:
    checkpoint = open_checkpoint_from_args(args, "feishu-import", "import")
    source_file = str(Path(args.source_file).resolve())
    item_key = f"feishu-import:{source_file}"
    if not checkpoint:
        return import_one_with_openapi(args)

    checkpoint.start_task({"source": source_file, "target": args.wiki_url, "totalDocs": 1})
    checkpoint.upsert_item(item_key, title=Path(source_file).name, source_id=source_file)
    checkpoint.start_item(item_key, "import")
    try:
        result = import_one_with_openapi(args)
        checkpoint.complete_item(
            item_key,
            metadata={"wikiToken": wiki_token_from_move_result(result), "docToken": result.get("docToken") or ""},
        )
        checkpoint.complete_task(result)
        return result
    except ExportStopped:
        checkpoint.fail_item(item_key, "stopped")
        checkpoint.fail_task("stopped", status="stopped")
        raise
    except Exception as exc:
        checkpoint.fail_item(item_key, str(exc))
        checkpoint.fail_task(str(exc))
        raise
    finally:
        checkpoint.close()


def login_and_save_auth(args: argparse.Namespace) -> dict[str, Any]:
    wait_seconds = float(getattr(args, "login_wait_seconds", 0) or 0)
    if wait_seconds <= 0:
        return export_login_and_save_auth(args)

    def wait_for_login_timeout() -> None:
        deadline = time.time() + wait_seconds
        emit(args, f"请在浏览器中完成登录，工具将在 {int(wait_seconds)} 秒后自动保存凭证。")
        while time.time() < deadline:
            if stop_requested(args):
                raise ExportStopped("用户停止了飞书登录任务")
            remaining = int(deadline - time.time())
            if remaining in {300, 180, 120, 60, 30, 10, 5}:
                emit(args, f"等待登录中，剩余约 {remaining} 秒")
            time.sleep(1)

    return export_login_and_save_auth(args, wait_for_login_timeout)


def run_gui(initial_args: argparse.Namespace | None = None) -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext
    from gui_utils import create_collapsible_section, create_scrollable_body

    args0 = initial_args or argparse.Namespace()
    initial_config_file = getattr(args0, "config_file", None) or str(DEFAULT_CONFIG_FILE)
    try:
        initial_config = load_import_config(initial_config_file)
    except Exception:
        initial_config = {}
    root = tk.Tk()
    root.title("飞书 Markdown 导入工具")
    root.geometry("980x720")
    body = create_scrollable_body(root)

    wiki_var = tk.StringVar(value=getattr(args0, "wiki_url", None) or DEFAULT_WIKI_URL)
    source_var = tk.StringVar(
        value=getattr(args0, "source_dir", None)
        or (str(DEFAULT_SOURCE_DIR) if DEFAULT_SOURCE_DIR.exists() else str(PROJECT_DIR))
    )
    auth_var = tk.StringVar(value=getattr(args0, "auth_file", None) or str(default_auth_path()))
    profile_var = tk.StringVar(value=getattr(args0, "profile_dir", None) or str(default_profile_path()))
    browser_path_var = tk.StringVar(value=getattr(args0, "browser_path", None) or "")
    port_var = tk.StringVar(value=str(getattr(args0, "port", DEFAULT_PORT) or DEFAULT_PORT))
    request_delay_var = tk.StringVar(value=str(getattr(args0, "request_delay", 0.8) or 0.8))
    request_jitter_var = tk.StringVar(value=str(getattr(args0, "request_jitter", 0.4) or 0.4))
    limit_var = tk.StringVar(value=str(getattr(args0, "limit", 5) or 5))
    config_file_var = tk.StringVar(value=initial_config_file)
    source_file_var = tk.StringVar(value=getattr(args0, "source_file", None) or "")
    app_id_var = tk.StringVar(
        value=getattr(args0, "app_id", None) or os.getenv("FEISHU_APP_ID", "") or initial_config.get("app_id", "")
    )
    app_secret_var = tk.StringVar(
        value=getattr(args0, "app_secret", None)
        or os.getenv("FEISHU_APP_SECRET", "")
        or initial_config.get("app_secret", "")
    )
    drive_folder_var = tk.StringVar(
        value=getattr(args0, "drive_folder_token", None) or initial_config.get("drive_folder_token", "")
    )
    import_mount_var = tk.StringVar(
        value=getattr(args0, "import_mount_key", None) or initial_config.get("import_mount_key", "")
    )
    space_id_var = tk.StringVar(value=getattr(args0, "space_id", None) or initial_config.get("space_id", ""))
    parent_wiki_token_var = tk.StringVar(
        value=getattr(args0, "parent_wiki_token", None) or initial_config.get("parent_wiki_token", "")
    )
    obj_type_var = tk.StringVar(value=getattr(args0, "obj_type", None) or initial_config.get("obj_type", "docx"))
    poll_timeout_var = tk.StringVar(value=str(getattr(args0, "poll_timeout", 120) or 120))
    poll_interval_var = tk.StringVar(value=str(getattr(args0, "poll_interval", 2.0) or 2.0))
    max_import_var = tk.StringVar(value=str(getattr(args0, "max_import", 0) or 0))
    image_max_width_var = tk.StringVar(value=str(getattr(args0, "image_max_width", DEFAULT_IMAGE_MAX_WIDTH) or DEFAULT_IMAGE_MAX_WIDTH))
    move_to_wiki_var = tk.BooleanVar(value=True)
    skip_rename_var = tk.BooleanVar(value=bool(getattr(args0, "skip_rename", False)))
    repair_images_var = tk.BooleanVar(value=not bool(getattr(args0, "skip_image_repair", False)))
    require_image_repair_var = tk.BooleanVar(value=bool(getattr(args0, "require_image_repair", False)))
    close_chrome_var = tk.BooleanVar(value=False)
    log_queue: queue.Queue[str] = queue.Queue()
    current_stop_event: dict[str, threading.Event | None] = {"event": None}
    buttons: list[tk.Widget] = []

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
        root.after(150, poll_log)

    def browse_source() -> None:
        selected = filedialog.askdirectory(initialdir=source_var.get() or str(PROJECT_DIR))
        if selected:
            source_var.set(selected)

    def browse_source_file() -> None:
        selected = filedialog.askopenfilename(
            initialdir=source_var.get() or str(PROJECT_DIR),
            filetypes=[("Markdown files", "*.md"), ("All files", "*.*")],
        )
        if selected:
            source_file_var.set(selected)

    def browse_auth() -> None:
        selected = filedialog.asksaveasfilename(
            initialfile=Path(auth_var.get()).name or ".feishu_auth.json",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if selected:
            auth_var.set(selected)

    def browse_config() -> None:
        selected = filedialog.asksaveasfilename(
            initialfile=Path(config_file_var.get()).name or ".feishu_import_config.json",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if selected:
            config_file_var.set(selected)

    def open_feishu_developer_console() -> None:
        webbrowser.open(FEISHU_DEVELOPER_CONSOLE_URL)
        messagebox.showinfo(
            "飞书应用配置向导",
            "已打开飞书开放平台开发者后台。\n\n"
            "操作步骤：\n"
            "1. 创建企业自建应用，建议名称填写“万能导导入”。\n"
            "2. 进入应用详情页的“凭证与基础信息”。\n"
            "3. 复制 App ID 和 App Secret，填写回本工具。\n"
            "4. 如果导入时提示权限不足，请在应用权限里开通云文档/知识库相关权限并发布应用。",
        )

    def open_feishu_permission_page() -> None:
        app_id = app_id_var.get().strip()
        if not app_id:
            messagebox.showwarning("缺少 App ID", "请先填写飞书 App ID，再打开权限申请页。")
            return
        url = build_feishu_permission_url(app_id)
        webbrowser.open(url)
        messagebox.showinfo(
            "飞书 API 权限申请",
            "已打开当前应用的权限申请页。\n\n"
            "请开通页面里展示的云空间、云文档和知识库权限，然后发布应用新版本。\n"
            "如果企业需要管理员审批，请完成审批后再回到万能导重试导入。",
        )

    def save_config_from_gui() -> None:
        data = {
            "app_id": app_id_var.get().strip(),
            "app_secret": app_secret_var.get().strip(),
            "drive_folder_token": drive_folder_var.get().strip(),
            "import_mount_key": import_mount_var.get().strip(),
            "space_id": space_id_var.get().strip(),
            "parent_wiki_token": parent_wiki_token_var.get().strip(),
            "obj_type": obj_type_var.get().strip() or "docx",
        }
        if not data["app_id"] or not data["app_secret"]:
            messagebox.showwarning("配置不完整", "请先填写飞书 App ID 和 App Secret。")
            return
        path = save_import_config(config_file_var.get().strip() or str(DEFAULT_CONFIG_FILE), data)
        messagebox.showinfo("配置已保存", f"已保存到：\n{path}\n\n这个文件已加入 .gitignore，不会提交到仓库。")

    def browse_profile() -> None:
        selected = filedialog.askdirectory(initialdir=profile_var.get() or str(PROJECT_DIR))
        if selected:
            profile_var.set(selected)

    def browse_browser() -> None:
        selected = filedialog.askopenfilename(
            title="选择 Chrome / Edge / Chromium 浏览器程序",
            filetypes=[("Browser executable", "*.exe"), ("All files", "*.*")],
        )
        if selected:
            browser_path_var.set(selected)

    def detect_browser() -> None:
        found = find_chrome(browser_path_var.get().strip() or None)
        if found:
            browser_path_var.set(found)
            messagebox.showinfo("已找到浏览器", f"浏览器程序：\n{found}")
        else:
            messagebox.showwarning("未找到浏览器", "请安装 Chrome/Edge，或点击“选择”手动指定浏览器程序。")

    def open_source() -> None:
        path = Path(source_var.get())
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])

    def build_args() -> argparse.Namespace:
        if not wiki_var.get().strip():
            raise ExportError("请填写目标飞书 Wiki URL")
        if not source_var.get().strip():
            raise ExportError("请填写本地 Markdown 目录")
        return argparse.Namespace(
            wiki_url=wiki_var.get().strip(),
            source_dir=source_var.get().strip(),
            port=int(port_var.get().strip() or DEFAULT_PORT),
            profile_dir=profile_var.get().strip() or None,
            browser_path=browser_path_var.get().strip() or None,
            auth_file=auth_var.get().strip() or str(default_auth_path()),
            skip_auth_load=False,
            wait_login=False,
            limit=max(0, int(limit_var.get().strip() or "5")),
            request_delay=max(0.0, float(request_delay_var.get().strip() or "0.8")),
            request_jitter=max(0.0, float(request_jitter_var.get().strip() or "0.4")),
            close_started_chrome=close_chrome_var.get(),
            source_file=source_file_var.get().strip() or None,
            app_id=app_id_var.get().strip() or None,
            app_secret=app_secret_var.get().strip() or None,
            drive_folder_token=drive_folder_var.get().strip(),
            import_mount_key=import_mount_var.get().strip(),
            space_id=space_id_var.get().strip() or None,
            parent_wiki_token=parent_wiki_token_var.get().strip() or None,
            obj_type=obj_type_var.get().strip() or "docx",
            config_file=config_file_var.get().strip() or str(DEFAULT_CONFIG_FILE),
            move_to_wiki=move_to_wiki_var.get(),
            skip_rename=skip_rename_var.get(),
            skip_image_repair=not repair_images_var.get(),
            require_image_repair=require_image_repair_var.get(),
            max_import=max(0, int(max_import_var.get().strip() or "0")),
            image_max_width=max(1, int(image_max_width_var.get().strip() or str(DEFAULT_IMAGE_MAX_WIDTH))),
            poll_timeout=max(5.0, float(poll_timeout_var.get().strip() or "120")),
            poll_interval=max(0.5, float(poll_interval_var.get().strip() or "2")),
            yes=False,
            stop_event=None,
            log_callback=log,
        )

    def wait_for_login_dialog() -> None:
        event = threading.Event()

        def ask() -> None:
            messagebox.showinfo(
                "完成登录后继续",
                "浏览器已经打开。\n\n请在浏览器里完成飞书登录，并确认目标 Wiki 能正常打开。\n完成后回到这里点击“确定”，工具会保存登录凭证。",
            )
            event.set()

        root.after(0, ask)
        event.wait()

    def stop_current_task() -> None:
        event = current_stop_event.get("event")
        if event and not event.is_set():
            event.set()
            log("已发送停止请求，工具会在当前安全点停止。")

    def set_running_state(running: bool) -> None:
        for button in buttons:
            button.configure(state="disabled" if running else "normal")
        stop_button.configure(state="normal" if running else "disabled")

    def run_worker(name: str, fn: Callable[[argparse.Namespace], dict[str, Any]]) -> None:
        try:
            args = build_args()
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return

        def worker() -> None:
            stop_event = threading.Event()
            args.stop_event = stop_event
            current_stop_event["event"] = stop_event
            root.after(0, lambda: set_running_state(True))
            log(f"开始：{name}")
            try:
                result = fn(args)
                log(f"完成：{name}")
                log(json.dumps(result, ensure_ascii=False, indent=2))
            except ExportStopped as exc:
                log(f"已停止：{exc}")
            except Exception as exc:
                error_text = str(exc)
                log(f"失败：{error_text}")
                root.after(0, lambda text=error_text: messagebox.showerror("执行失败", text))
            finally:
                current_stop_event["event"] = None
                root.after(0, lambda: set_running_state(False))

        threading.Thread(target=worker, daemon=True).start()

    def do_login() -> None:
        def run(args: argparse.Namespace) -> dict[str, Any]:
            return export_login_and_save_auth(args, wait_for_login_dialog)

        run_worker("登录并保存凭证", run)

    def do_probe() -> None:
        def run(args: argparse.Namespace) -> dict[str, Any]:
            result = probe_target_wiki(args)

            def fill_probe_result() -> None:
                space_id = str(result.get("spaceId") or "")
                target_wiki_token = str(result.get("targetWikiToken") or "")
                if space_id and not space_id_var.get().strip():
                    space_id_var.set(space_id)
                if target_wiki_token and not parent_wiki_token_var.get().strip():
                    parent_wiki_token_var.set(target_wiki_token)

            root.after(0, fill_probe_result)
            return result

        run_worker("探测目标 Wiki", run)

    def do_plan() -> None:
        run_worker("扫描本地 Markdown 并生成导入计划", build_import_plan)

    def do_api_import_one() -> None:
        try:
            args = build_args()
            md_path = select_source_file(args)
            title = normalize_title_from_md(md_path)
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        target = "目标 Wiki" if args.move_to_wiki else "飞书云空间"
        ok = messagebox.askyesno(
            "确认创建测试文档",
            f"这会在{target}创建一篇测试文档。\n\n标题：{title}\n来源：{md_path}\n\n确认继续吗？",
        )
        if not ok:
            return

        def run(args2: argparse.Namespace) -> dict[str, Any]:
            args2.yes = True
            return import_one_with_openapi(args2)

        run_worker("API 单篇导入测试", run)

    def do_api_import_all() -> None:
        try:
            args = build_args()
            source = scan_markdown_source(Path(args.source_dir), limit=args.max_import)
            total = len(source.get("docs") or [])
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        if total <= 0:
            messagebox.showwarning("没有文档", "本地 Markdown 目录里没有找到可导入的 .md 文件。")
            return
        ok = messagebox.askyesno(
            "确认批量导入",
            f"这会向目标 Wiki 批量创建文档，并尽量按本地目录恢复层级。\n\n本次计划导入：{total} 篇\n\n确认继续吗？",
        )
        if not ok:
            return

        def run(args2: argparse.Namespace) -> dict[str, Any]:
            args2.yes = True
            return import_all_with_openapi(args2)

        run_worker("API 批量导入 Markdown", run)

    form = tk.Frame(body, padx=14, pady=12)
    form.pack(fill="x")
    form.columnconfigure(1, weight=1)

    def row(
        parent: tk.Frame,
        label: str,
        variable: tk.StringVar,
        row_index: int,
        browse: Callable[[], None] | None = None,
        *,
        show: str | None = None,
    ) -> None:
        tk.Label(parent, text=label, anchor="w").grid(row=row_index, column=0, sticky="w", pady=5)
        tk.Entry(parent, textvariable=variable, show=show).grid(row=row_index, column=1, sticky="ew", padx=8, pady=5)
        if browse:
            tk.Button(parent, text="选择", command=browse).grid(row=row_index, column=2, pady=5)

    def browser_row(parent: tk.Frame, row_index: int) -> None:
        tk.Label(parent, text="浏览器程序路径", anchor="w").grid(row=row_index, column=0, sticky="w", pady=5)
        tk.Entry(parent, textvariable=browser_path_var).grid(row=row_index, column=1, sticky="ew", padx=8, pady=5)
        tk.Button(parent, text="选择", command=browse_browser).grid(row=row_index, column=2, pady=5)
        tk.Button(parent, text="查找", command=detect_browser).grid(row=row_index, column=3, padx=(6, 0), pady=5)

    row(form, "目标飞书 Wiki URL", wiki_var, 0)
    row(form, "本地 Markdown 目录", source_var, 1, browse_source)
    row(form, "单篇测试文件（可选）", source_file_var, 2, browse_source_file)
    row(form, "飞书 App ID", app_id_var, 3)
    row(form, "飞书 App Secret", app_secret_var, 4, show="*")
    tk.Button(form, text="打开飞书开放平台创建应用", command=open_feishu_developer_console).grid(
        row=5, column=1, sticky="w", pady=(0, 8)
    )
    tk.Button(form, text="打开 API 权限申请页", command=open_feishu_permission_page).grid(
        row=5, column=1, sticky="w", padx=(190, 0), pady=(0, 8)
    )
    tk.Checkbutton(form, text="修复本地图片", variable=repair_images_var).grid(row=6, column=1, sticky="w", pady=5)
    tk.Checkbutton(form, text="图片修复失败时中断", variable=require_image_repair_var).grid(row=7, column=1, sticky="w", pady=5)

    advanced_form, _advanced_toggle = create_collapsible_section(body, "高级参数（通常不用改）", open_by_default=False)
    advanced_form.columnconfigure(1, weight=1)
    row(advanced_form, "凭证文件", auth_var, 0, browse_auth)
    row(advanced_form, "浏览器配置目录", profile_var, 1, browse_profile)
    browser_row(advanced_form, 2)
    row(advanced_form, "调试端口", port_var, 3)
    row(advanced_form, "请求延迟秒", request_delay_var, 4)
    row(advanced_form, "请求随机浮动秒", request_jitter_var, 5)
    row(advanced_form, "本地扫描样本数", limit_var, 6)
    row(advanced_form, "API 配置文件", config_file_var, 7, browse_config)
    row(advanced_form, "上传文件夹 token", drive_folder_var, 8)
    row(advanced_form, "导入挂载 token", import_mount_var, 9)
    row(advanced_form, "Wiki spaceId（可自动探测）", space_id_var, 10)
    row(advanced_form, "父级 Wiki token（可自动探测）", parent_wiki_token_var, 11)
    row(advanced_form, "移动 obj_type", obj_type_var, 12)
    row(advanced_form, "轮询超时秒", poll_timeout_var, 13)
    row(advanced_form, "轮询间隔秒", poll_interval_var, 14)
    row(advanced_form, "最多导入数量", max_import_var, 15)
    row(advanced_form, "图片最大宽度 px", image_max_width_var, 16)
    tk.Checkbutton(advanced_form, text="导入后移动到目标 Wiki", variable=move_to_wiki_var).grid(row=17, column=1, sticky="w", pady=5)
    tk.Checkbutton(advanced_form, text="跳过自动重命名", variable=skip_rename_var).grid(row=18, column=1, sticky="w", pady=5)
    tk.Checkbutton(advanced_form, text="结束后关闭本工具启动的浏览器", variable=close_chrome_var).grid(row=19, column=1, sticky="w", pady=5)

    actions = tk.Frame(body, padx=14, pady=4)
    actions.pack(fill="x")
    buttons.extend(
        [
            tk.Button(actions, text="1. 登录并保存凭证", command=do_login, width=18),
            tk.Button(actions, text="2. 探测目标 Wiki", command=do_probe, width=16),
            tk.Button(actions, text="3. 扫描并生成计划", command=do_plan, width=18),
            tk.Button(actions, text="4. API 单篇导入测试", command=do_api_import_one, width=20),
            tk.Button(actions, text="5. API 批量导入", command=do_api_import_all, width=18),
            tk.Button(actions, text="打开 API 权限申请页", command=open_feishu_permission_page, width=20),
            tk.Button(actions, text="保存 API 配置", command=save_config_from_gui, width=14),
            tk.Button(actions, text="停止当前任务", command=stop_current_task, width=14, state="disabled"),
            tk.Button(actions, text="打开本地目录", command=open_source, width=14),
        ]
    )
    stop_button = buttons[-2]
    for index, button in enumerate(buttons):
        button.grid(row=index // 4, column=index % 4, padx=5, pady=4, sticky="ew")
    for column in range(4):
        actions.columnconfigure(column, weight=1)

    note = tk.Label(
        body,
        text="说明：登录、探测、生成计划都是只读操作；API 导入依赖飞书开放平台权限，遇到 99991672 会自动打开权限申请页。",
        anchor="w",
        padx=14,
    )
    note.pack(fill="x", pady=(8, 0))

    log_text = scrolledtext.ScrolledText(body, height=18, state="disabled")
    log_text.pack(fill="both", expand=True, padx=14, pady=12)
    poll_log()
    root.mainloop()
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe local Markdown import into Feishu Wiki.")
    parser.add_argument("--gui", action="store_true", help="Open the graphical probe interface")
    parser.add_argument("--login", action="store_true", help="Open browser, let you log in, then save auth cookies")
    parser.add_argument("--login-wait-seconds", type=float, default=0.0, help="For non-interactive GUI wrappers, wait this many seconds before saving login cookies")
    parser.add_argument("--probe", action="store_true", help="Read target Wiki info without writing")
    parser.add_argument("--plan", action="store_true", help="Read target Wiki and scan local Markdown directory")
    parser.add_argument("--api-import-one", action="store_true", help="Upload one Markdown file through Feishu OpenAPI")
    parser.add_argument("--api-import-all", action="store_true", help="Batch import local Markdown files through Feishu OpenAPI")
    parser.add_argument("--setup-openapi-permissions", action="store_true", help="Open and check Feishu Open Platform permissions used by the import flow")
    parser.add_argument("--check-app-setup", action="store_true", help="Check whether the Feishu app identity is available")
    parser.add_argument("--save-config", action="store_true", help="Save Feishu OpenAPI settings to the private plugin config file")
    parser.add_argument("--setup-target-wiki-doc-app", action="store_true", help="Open the target Wiki and add the current app as a document app")
    parser.add_argument("--grant-target-wiki-permission", action="store_true", help="Deprecated alias of --setup-target-wiki-doc-app")
    parser.add_argument("--wiki-url", default=DEFAULT_WIKI_URL, help="Target Feishu Wiki URL")
    parser.add_argument("--source-dir", default=str(DEFAULT_SOURCE_DIR), help="Local Markdown directory")
    parser.add_argument("--source-file", help="One Markdown file used by --api-import-one. Omit to use the first file in --source-dir")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Chrome remote debugging port")
    parser.add_argument("--profile-dir", help=f"Chrome profile dir. Omit to auto-use {default_profile_path()}")
    parser.add_argument("--browser-path", help="Optional Chrome/Edge/Chromium executable path")
    parser.add_argument("--auth-file", help=f"Auth cookie file. Omit to auto-use {default_auth_path()}")
    parser.add_argument("--config-file", default=str(DEFAULT_CONFIG_FILE), help="Local JSON file for Feishu API config")
    parser.add_argument("--skip-auth-load", action="store_true", help="Do not load saved auth cookies before probing")
    parser.add_argument("--limit", type=int, default=5, help="Maximum local Markdown samples in the plan output")
    parser.add_argument("--max-import", type=int, default=0, help="Maximum Markdown files to import in --api-import-all. 0 means all")
    parser.add_argument("--request-delay", type=float, default=0.8, help="Fixed seconds to wait before each request")
    parser.add_argument("--request-jitter", type=float, default=0.4, help="Extra random seconds added before each request")
    parser.add_argument("--close-started-chrome", action="store_true", help="Close Chrome started by this script after the task")
    parser.add_argument("--app-id", help="Feishu app id. Omit to read FEISHU_APP_ID")
    parser.add_argument("--app-secret", help="Feishu app secret. Omit to read FEISHU_APP_SECRET")
    parser.add_argument("--drive-folder-token", default="", help="Feishu Drive folder token used as import mount key")
    parser.add_argument("--import-mount-key", default="", help="Import task mount_key. Leave empty to use Feishu default")
    parser.add_argument("--space-id", help="Target Wiki spaceId. Omit to probe it from --wiki-url")
    parser.add_argument("--parent-wiki-token", help="Target parent wiki token. Omit to use the token in --wiki-url")
    parser.add_argument("--obj-type", default="docx", help="Wiki move obj_type for imported document, usually docx")
    parser.add_argument("--wiki-grant-perm", default="edit", help=argparse.SUPPRESS)
    parser.add_argument("--move-to-wiki", action="store_true", help="Move imported docx into the target Wiki after import")
    parser.add_argument("--skip-rename", action="store_true", help="Do not rename imported docx back to the Markdown title")
    parser.add_argument("--skip-image-repair", action="store_true", help="Do not replace imported local-image placeholders")
    parser.add_argument("--require-image-repair", action="store_true", help="Fail the import if local-image repair fails")
    parser.add_argument("--image-max-width", type=int, default=DEFAULT_IMAGE_MAX_WIDTH, help="Maximum display width for repaired images")
    parser.add_argument("--poll-timeout", type=float, default=120.0, help="Seconds to wait for async Feishu tasks")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="Seconds between async task polls")
    parser.add_argument("--no-auto-open-permission", action="store_true", help="Do not automatically open Feishu permission request pages")
    parser.add_argument("--yes", action="store_true", help="Confirm write operation for API import commands")
    add_checkpoint_args(parser)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.gui:
        print("旧版 Python GUI 已废弃，请使用 Electron 桌面端：start-wandao.cmd 或 ./start-wandao.sh", file=sys.stderr)
        return 2
    try:
        if args.login:
            result = login_and_save_auth(args)
        elif args.save_config:
            result = save_import_config_from_args(args)
        elif args.api_import_one:
            result = import_one_with_checkpoint(args)
        elif args.api_import_all:
            result = import_all_with_openapi(args)
        elif args.setup_openapi_permissions:
            result = setup_openapi_permissions(args)
        elif args.check_app_setup:
            result = check_app_setup(args)
        elif args.setup_target_wiki_doc_app:
            result = setup_target_wiki_doc_app(args)
        elif args.grant_target_wiki_permission:
            result = setup_target_wiki_doc_app(args)
        elif args.plan:
            result = build_import_plan(args)
        elif args.probe:
            result = probe_target_wiki(args)
        else:
            result = scan_markdown_source(Path(args.source_dir), limit=args.limit)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except ExportStopped as exc:
        emit(args, f"飞书导入任务已停止：{exc}", event="task.stopped", level="warn")
        print(f"Stopped: {exc}", file=sys.stderr)
        return 130
    except Exception as exc:
        maybe_open_permission_page(args, exc)
        emit(
            args,
            f"飞书导入任务失败：{exc}",
            event="task.failed",
            level="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
