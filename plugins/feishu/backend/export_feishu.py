#!/usr/bin/env python3
# Author: tllovesxs
"""
Standalone Feishu Wiki exporter.

Desktop UI:
  Use start-wandao.cmd or ./start-wandao.sh. The old Python GUI is deprecated.

First login:
  python export_feishu.py --login \
    --wiki-url https://<tenant>.feishu.cn/wiki/<wiki_token> \
    --auth-file .feishu_auth.json

Incremental export:
  python export_feishu.py \
    --wiki-url https://<tenant>.feishu.cn/wiki/<wiki_token> \
    --output "./exports/feishu" \
    --auth-file .feishu_auth.json \
    --incremental

The tool controls Chrome/Edge through Chrome DevTools Protocol and stores session
cookies, not passwords. Keep auth files private.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from wandao_core.browser import (
    CDPClient,
    DEFAULT_PORT,
    ExportError,
    ExportStopped,
    check_stopped,
    chrome_debug_available,
    default_data_dir,
    default_state_path,
    emit,
    find_chrome,
    http_json,
    js_string,
    pad,
    prepare_cookie_for_set,
    sanitize_filename,
    stop_requested,
    throttle_request,
    wait_for_debug_port,
)
from wandao_core.checkpoint import add_checkpoint_args, open_checkpoint_from_args
from wandao_cli import extend_arg_list_from_file
from wandao_core.credentials import write_private_json
from wandao_core.report import finalize_report


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR
DEFAULT_PROFILE = ".feishu-chrome-profile"
DEFAULT_AUTH_FILE = ".feishu_auth.json"
DEFAULT_WIKI_URL = ""
FEISHU_NATIVE_DOC_OBJ_TYPE = 22
FEISHU_FILE_OBJ_TYPE = 12
FEISHU_MARKDOWN_FILE_TYPES = {"md", "markdown"}


class FeishuLoginRequired(ExportError):
    pass


class FeishuPermissionDenied(ExportError):
    pass


class FeishuMarkdownSourceInvalid(ExportError):
    pass


def default_auth_path() -> Path:
    return default_state_path(DEFAULT_AUTH_FILE)


def default_profile_path() -> Path:
    env_profile = os.environ.get("FEISHU_PROFILE_DIR")
    if env_profile:
        return Path(env_profile).expanduser().resolve()
    return default_data_dir() / DEFAULT_PROFILE


def auth_path_from_args(args: argparse.Namespace) -> Path:
    return Path(args.auth_file).resolve() if args.auth_file else default_auth_path().resolve()


def start_chrome(port: int, profile_dir: Path, url: str, browser_path: str | None = None) -> subprocess.Popen[Any]:
    chrome = find_chrome(browser_path)
    if not chrome:
        raise ExportError(
            "Chrome/Edge executable was not found. Install Chrome/Edge, add it to PATH, "
            "or set WANDAO_BROWSER to the browser executable path."
        )
    profile_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--disable-popup-blocking",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def open_tab(port: int, url: str) -> None:
    encoded = urllib.parse.quote(url, safe="")
    endpoint = f"http://127.0.0.1:{port}/json/new?{encoded}"
    try:
        urllib.request.urlopen(endpoint, timeout=5).close()
    except urllib.error.HTTPError as exc:
        if exc.code != 405:
            raise
        request = urllib.request.Request(endpoint, method="PUT")
        urllib.request.urlopen(request, timeout=5).close()


FEISHU_DOC_PATH_TYPES = {"doc", "docs", "docx"}


def parse_feishu_entry_url(entry_url: str) -> tuple[str, str, str, str, str]:
    parsed = urllib.parse.urlparse(entry_url.strip())
    if not parsed.scheme or not parsed.netloc:
        raise ExportError("请填写完整的飞书 URL")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 3 and parts[0] == "drive" and parts[1] == "folder":
        normalized = f"{parsed.scheme}://{parsed.netloc}/drive/folder/{parts[2]}"
        return parsed.netloc, f"{parsed.scheme}://{parsed.netloc}", parts[2], normalized, "drive_folder"
    if len(parts) < 2 or parts[0] not in {"wiki", *FEISHU_DOC_PATH_TYPES}:
        raise ExportError(
            "飞书 URL 需要形如 https://xxx.feishu.cn/wiki/<token>、/docx/<token> 或 /drive/folder/<token>"
        )
    kind = "wiki" if parts[0] == "wiki" else "document"
    normalized = f"{parsed.scheme}://{parsed.netloc}/{parts[0]}/{parts[1]}"
    return parsed.netloc, f"{parsed.scheme}://{parsed.netloc}", parts[1], normalized, kind


def normalize_wiki_url(wiki_url: str) -> str:
    _host, _origin, _token, normalized, kind = parse_feishu_entry_url(wiki_url)
    if kind != "wiki":
        raise ExportError("飞书 Wiki URL 需要形如 https://xxx.feishu.cn/wiki/<wiki_token>")
    return normalized


def parse_wiki_url(wiki_url: str) -> tuple[str, str, str, str]:
    host, origin, token, normalized, kind = parse_feishu_entry_url(wiki_url)
    if kind != "wiki":
        raise ExportError("飞书 Wiki URL 需要形如 https://xxx.feishu.cn/wiki/<wiki_token>")
    return host, origin, token, normalized


def page_for_entry(port: int, host: str, normalized_url: str) -> dict[str, Any] | None:
    pages = http_json(f"http://127.0.0.1:{port}/json/list", timeout=5)
    parsed = urllib.parse.urlparse(normalized_url)
    entry_needle = f"{host}{parsed.path}"
    host_needle = f"{host}/"
    for page in pages:
        if page.get("type") == "page" and entry_needle in page.get("url", ""):
            return page
    for page in pages:
        if page.get("type") == "page" and host_needle in page.get("url", ""):
            return page
    return None


def page_for_wiki(port: int, host: str, wiki_token: str) -> dict[str, Any] | None:
    return page_for_entry(port, host, f"https://{host}/wiki/{wiki_token}")


def connect_wiki_browser(
    args: argparse.Namespace,
    wiki_url: str,
    host: str,
    wiki_token: str,
) -> tuple[CDPClient, subprocess.Popen[Any] | None]:
    chrome_proc: subprocess.Popen[Any] | None = None
    if not chrome_debug_available(args.port):
        profile = Path(args.profile_dir).resolve() if args.profile_dir else default_profile_path()
        chrome_proc = start_chrome(args.port, profile, wiki_url, getattr(args, "browser_path", None))
        wait_for_debug_port(args.port, timeout=30)

    page = page_for_wiki(args.port, host, wiki_token)
    if not page:
        open_tab(args.port, wiki_url)
        time.sleep(4)
        page = page_for_wiki(args.port, host, wiki_token)
    if not page:
        raise ExportError("Could not find or create a Feishu Wiki page in Chrome.")

    cdp = CDPClient(page["webSocketDebuggerUrl"])
    cdp.connect()
    cdp.send("Runtime.enable")
    cdp.send("Page.enable")
    return cdp, chrome_proc


def connect_entry_browser(
    args: argparse.Namespace,
    entry_url: str,
    host: str,
) -> tuple[CDPClient, subprocess.Popen[Any] | None]:
    chrome_proc: subprocess.Popen[Any] | None = None
    if not chrome_debug_available(args.port):
        profile = Path(args.profile_dir).resolve() if args.profile_dir else default_profile_path()
        chrome_proc = start_chrome(args.port, profile, entry_url, getattr(args, "browser_path", None))
        wait_for_debug_port(args.port, timeout=30)

    page = page_for_entry(args.port, host, entry_url)
    if not page:
        open_tab(args.port, entry_url)
        time.sleep(4)
        page = page_for_entry(args.port, host, entry_url)
    if not page:
        raise ExportError("Could not find or create a Feishu page in Chrome.")

    cdp = CDPClient(page["webSocketDebuggerUrl"])
    cdp.connect()
    cdp.send("Runtime.enable")
    cdp.send("Page.enable")
    return cdp, chrome_proc


def is_feishu_cookie(cookie: dict[str, Any], host: str) -> bool:
    domain = (cookie.get("domain") or "").lower().lstrip(".")
    host = host.lower()
    return (
        domain == host
        or host.endswith(domain)
        or any(
            token in domain
            for token in (
                "feishu.cn",
                "feishucdn.com",
                "larksuite.com",
                "larksuitecdn.com",
                "bytedance.net",
                "byteoversea.com",
            )
        )
    )


def save_auth_state(cdp: CDPClient, auth_file: Path, wiki_url: str, host: str) -> dict[str, Any]:
    cdp.send("Network.enable")
    cookies = cdp.send("Network.getAllCookies", timeout=20).get("result", {}).get("cookies", [])
    cookies = [cookie for cookie in cookies if is_feishu_cookie(cookie, host)]
    if not cookies:
        raise ExportError("No Feishu cookies found. Make sure login is complete in Chrome.")
    payload = {
        "version": 1,
        "wikiUrl": wiki_url,
        "host": host,
        "savedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "cookies": cookies,
    }
    write_private_json(auth_file, payload)
    return {"cookieCount": len(cookies), "authFile": str(auth_file)}


def load_auth_state(cdp: CDPClient, auth_file: Path) -> int:
    if not auth_file.exists():
        raise ExportError(f"飞书登录凭证不存在：{auth_file}。请先点击“登录并保存凭证”。")
    payload = json.loads(auth_file.read_text(encoding="utf-8"))
    cookies = [prepare_cookie_for_set(cookie) for cookie in payload.get("cookies", [])]
    cookies = [cookie for cookie in cookies if cookie.get("name") and cookie.get("value")]
    if not cookies:
        raise ExportError(f"飞书登录凭证里没有可用 Cookie：{auth_file}。请重新登录并保存凭证。")
    cdp.send("Network.enable")
    cdp.send("Network.setCookies", {"cookies": cookies}, timeout=30)
    return len(cookies)


def inspect_entry_session(cdp: CDPClient) -> dict[str, Any]:
    value = cdp.evaluate(
        r"""
        (() => {
          const bodyText = (document.body && document.body.innerText) || "";
          const href = location.href || "";
          const url = new URL(href);
          const loginHost = /(^|\.)accounts\.(feishu\.cn|larksuite\.com)$/i.test(url.hostname);
          const loginPath = /\/accounts\/page\/login/i.test(url.pathname);
          const hasLoginForm = !!document.querySelector(
            'form input[type="password"], input[name="password"], [data-testid*="login"] input'
          );
          const permissionSelector = !!document.querySelector(
            '[data-testid*="permission"], [class*="permission-denied"], [class*="no-permission"], [class*="access-denied"]'
          );
          const permissionTitle = /没有权限|暂无权限|无权访问|access denied/i.test(document.title || "")
            && bodyText.length < 2000;
          const permissionDenied = permissionSelector || permissionTitle;
          return {
            href,
            title: document.title || "",
            readyState: document.readyState || "",
            hasBody: !!document.body,
            textLength: bodyText.length,
            hasLoginForm,
            loginRequired: loginHost || loginPath || hasLoginForm,
            permissionDenied,
          };
        })()
        """,
        timeout=10,
    )
    return value if isinstance(value, dict) else {}


def is_expected_entry_url(actual_url: str, expected_url: str) -> bool:
    try:
        actual = urllib.parse.urlparse(actual_url)
        expected = urllib.parse.urlparse(expected_url)
    except ValueError:
        return False
    if actual.hostname != expected.hostname:
        return False
    actual_parts = [part for part in actual.path.split("/") if part]
    expected_parts = [part for part in expected.path.split("/") if part]
    if len(actual_parts) < 2 or len(expected_parts) < 2:
        return actual.path.rstrip("/") == expected.path.rstrip("/")
    if expected_parts[:2] == ["drive", "folder"]:
        return len(actual_parts) >= 3 and len(expected_parts) >= 3 and actual_parts[:3] == expected_parts[:3]
    if actual_parts[1] != expected_parts[1]:
        return False
    if expected_parts[0] in FEISHU_DOC_PATH_TYPES:
        return actual_parts[0] in FEISHU_DOC_PATH_TYPES
    return actual_parts[:2] == expected_parts[:2]


def should_restore_auth(
    state: dict[str, Any],
    *,
    auth_exists: bool,
    skip_auth_load: bool,
) -> bool:
    return bool(auth_exists and not skip_auth_load and state.get("loginRequired"))


def wait_for_wiki_ready(
    cdp: CDPClient,
    timeout: int,
    args: argparse.Namespace | None = None,
    expected_url: str = "",
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_state: dict[str, Any] = {}
    while time.time() < deadline:
        check_stopped(args)
        try:
            last_state = inspect_entry_session(cdp)
        except ExportError:
            time.sleep(0.5)
            continue
        route_ready = not expected_url or is_expected_entry_url(str(last_state.get("href") or ""), expected_url)
        if route_ready and last_state.get("permissionDenied"):
            raise ExportError("当前账号没有权限访问该飞书页面")
        if route_ready and last_state.get("loginRequired"):
            raise FeishuLoginRequired("飞书登录状态已失效，请重新登录并保存凭证")
        document_ready = str(last_state.get("readyState") or "") != "loading"
        content_ready = bool(last_state.get("hasBody")) and int(last_state.get("textLength") or 0) > 20
        if route_ready and document_ready and content_ready:
            return last_state
        time.sleep(0.5)
    if last_state.get("loginRequired"):
        raise FeishuLoginRequired("飞书登录状态已失效，请重新登录并保存凭证")
    detail = (
        f"href={last_state.get('href') or '-'}, title={last_state.get('title') or '-'}, "
        f"readyState={last_state.get('readyState') or '-'}, textLength={last_state.get('textLength') or 0}"
    )
    raise ExportError(f"飞书页面没有加载完成（{detail}）")


def prepare_entry_session(
    cdp: CDPClient,
    args: argparse.Namespace,
    entry_url: str,
) -> dict[str, Any]:
    state = inspect_entry_session(cdp)
    auth_file = auth_path_from_args(args)
    restored_cookies = 0
    navigated = False

    matches_target = is_expected_entry_url(str(state.get("href") or ""), entry_url)
    if matches_target and state.get("permissionDenied"):
        raise ExportError("当前账号没有权限访问该飞书页面")

    can_restore = should_restore_auth(
        state,
        auth_exists=auth_file.exists(),
        skip_auth_load=bool(getattr(args, "skip_auth_load", False)),
    )
    if state.get("loginRequired") and not can_restore:
        raise FeishuLoginRequired("飞书登录状态已失效，请先登录并保存凭证")

    if can_restore:
        restored_cookies = load_auth_state(cdp, auth_file)
        emit(args, f"Loaded {restored_cookies} auth cookies from {auth_file}")
        cdp.navigate(entry_url)
        navigated = True
    elif not matches_target:
        cdp.navigate(entry_url)
        navigated = True

    try:
        ready_state = wait_for_wiki_ready(cdp, timeout=35, args=args, expected_url=entry_url)
    except FeishuLoginRequired:
        if restored_cookies or not auth_file.exists() or bool(getattr(args, "skip_auth_load", False)):
            raise
        restored_cookies = load_auth_state(cdp, auth_file)
        emit(args, f"Loaded {restored_cookies} auth cookies from {auth_file}")
        cdp.navigate(entry_url)
        navigated = True
        ready_state = wait_for_wiki_ready(cdp, timeout=35, args=args, expected_url=entry_url)
    return {
        "restoredCookies": restored_cookies,
        "navigated": navigated,
        "state": ready_state,
    }


def run_with_auth_retry(
    cdp: CDPClient,
    args: argparse.Namespace,
    entry_url: str,
    operation: Callable[[], Any],
    *,
    already_restored: bool = False,
) -> Any:
    """Run a Feishu page operation and recover from one API-level 401.

    ``prepare_entry_session`` deliberately preserves a healthy browser session.
    The session can still expire between that readiness check and a directory
    API request, so only that narrow failure gets one saved-cookie retry.  A
    second authentication failure is returned to the user instead of looping.
    """

    try:
        return operation()
    except FeishuLoginRequired:
        auth_file = auth_path_from_args(args)
        if (
            already_restored
            or bool(getattr(args, "_feishu_auth_retry_used", False))
            or bool(getattr(args, "skip_auth_load", False))
            or not auth_file.exists()
        ):
            raise

        restored_cookies = load_auth_state(cdp, auth_file)
        args._feishu_auth_retry_used = True
        emit(
            args,
            f"飞书接口登录态已过期，已恢复 {restored_cookies} 个凭证并重试一次。",
            level="warn",
        )
        cdp.navigate(entry_url)
        wait_for_wiki_ready(cdp, timeout=35, args=args, expected_url=entry_url)
        return operation()


FEISHU_TREE_LOADER_JS = r"""
async (startToken) => {
  function assertOk(json, url) {
    if (!json || (json.code !== 0 && json.code !== undefined)) {
      throw new Error("Feishu API failed: " + url + " " + JSON.stringify(json && {code: json.code, msg: json.msg || json.message}));
    }
    return json;
  }
  async function getJson(url) {
    const res = await fetch(url, {credentials: "include"});
    if (res.status === 401) throw new Error("FEISHU_AUTH_REQUIRED: " + url);
    if (res.status === 403) throw new Error("FEISHU_PERMISSION_DENIED: " + url);
    if (res.redirected && /\/accounts\/page\/login/i.test(new URL(res.url, location.origin).pathname)) {
      throw new Error("FEISHU_AUTH_REQUIRED: " + url);
    }
    if (!res.ok) throw new Error("FEISHU_API_HTTP_" + res.status + ": " + url);
    let json;
    try { json = await res.json(); }
    catch (_) { throw new Error("FEISHU_API_INVALID_JSON: " + url); }
    return assertOk(json, url);
  }

  let spaceId = "";
  let bootstrapError = null;
  try {
    const node = await getJson(`/space/api/wiki/v2/tree/get_node/?wiki_token=${encodeURIComponent(startToken)}&space_id=&expand_shortcut=true&with_deleted=true`);
    spaceId = node && node.data && node.data.space_id || "";
  } catch (error) {
    bootstrapError = error;
  }
  if (!spaceId) {
    const entry = performance.getEntriesByType("resource")
      .map(e => e.name)
      .find(url => url.includes("/space/api/wiki/v2/tree/get_info/") && url.includes("space_id="));
    if (entry) spaceId = new URL(entry).searchParams.get("space_id") || "";
  }
  if (!spaceId) {
    if (bootstrapError && /FEISHU_AUTH_REQUIRED|FEISHU_PERMISSION_DENIED/.test(String(bootstrapError))) {
      throw bootstrapError;
    }
    throw new Error("Could not detect Feishu Wiki space_id.");
  }

  const seen = new Set();
  const nodes = {};
  const childMap = {};
  let rootList = [];
  let space = {};

  function nodeFileType(node) {
    let iconInfo = node && node.icon_info;
    if (typeof iconInfo === "string") {
      try { iconInfo = JSON.parse(iconInfo); }
      catch (_) { iconInfo = {}; }
    }
    return iconInfo && iconInfo.file_type || node && node.file_type || "";
  }

  async function load(token) {
    if (!token || seen.has(token)) return;
    seen.add(token);
    const url = `/space/api/wiki/v2/tree/get_info/?space_id=${encodeURIComponent(spaceId)}&with_space=true&with_perm=true&expand_shortcut=true&need_shared=true&exclude_fields=5&with_deleted=true&wiki_token=${encodeURIComponent(token)}`;
    const json = await getJson(url);
    const data = json.data || {};
    const tree = data.tree || {};
    if (tree.root_list && tree.root_list.length) rootList = tree.root_list;
    if (data.space) space = data.space;
    Object.assign(nodes, tree.nodes || {});
    Object.assign(childMap, tree.child_map || {});
    for (const kids of Object.values(tree.child_map || {})) {
      for (const kid of kids || []) {
        if ((tree.nodes || {})[kid] && (tree.nodes || {})[kid].has_child) await load(kid);
      }
    }
  }

  await load(startToken);
  for (const token of rootList) {
    if ((nodes[token] || {}).has_child) await load(token);
  }

  const compactNodes = {};
  for (const [token, node] of Object.entries(nodes)) {
    compactNodes[token] = {
      wiki_token: token,
      parent_wiki_token: node.parent_wiki_token || "",
      title: node.title || "",
      obj_token: node.obj_token || "",
      obj_type: node.obj_type,
      has_child: !!node.has_child,
      sort_id: node.sort_id || 0,
      url: node.url || (location.origin + "/wiki/" + token),
      wiki_node_type: node.wiki_node_type || 0,
      file_type: nodeFileType(node),
      mount_point: "wiki",
    };
  }
  return {spaceId, space, rootList, childMap, nodes: compactNodes};
}
"""


FEISHU_DRIVE_FOLDER_LOADER_JS = r"""
async (startToken) => {
  function assertOk(json, url) {
    if (!json || (json.code !== 0 && json.code !== undefined)) {
      throw new Error("Feishu Drive API failed: " + url + " " + JSON.stringify(json && {code: json.code, msg: json.msg || json.message}));
    }
    return json;
  }
  async function getJson(url) {
    const res = await fetch(url, {credentials: "include"});
    if (res.status === 401) throw new Error("FEISHU_AUTH_REQUIRED: " + url);
    if (res.status === 403) throw new Error("FEISHU_PERMISSION_DENIED: " + url);
    if (res.redirected && /\/accounts\/page\/login/i.test(new URL(res.url, location.origin).pathname)) {
      throw new Error("FEISHU_AUTH_REQUIRED: " + url);
    }
    if (!res.ok) throw new Error("FEISHU_API_HTTP_" + res.status + ": " + url);
    let json;
    try { json = await res.json(); }
    catch (_) { throw new Error("FEISHU_API_INVALID_JSON: " + url); }
    return assertOk(json, url);
  }
  function normalizeNode(raw, parentToken, sortId) {
    const token = raw.token || raw.node_token || raw.obj_token || "";
    const type = raw.type;
    return {
      wiki_token: token,
      parent_wiki_token: parentToken || "",
      title: raw.name || raw.title || "未命名",
      obj_token: raw.obj_token || token,
      obj_type: type,
      has_child: type === 0,
      sort_id: sortId || raw.sort_id || raw.edit_time || raw.create_time || 0,
      url: raw.url || (type === 0 ? `${location.origin}/drive/folder/${token}` : ""),
      wiki_node_type: 0,
      drive_node_type: raw.node_type || 0,
      file_type: raw.file_type || raw.extension || ((raw.name || "").split(".").pop() || ""),
      mount_point: "explorer",
    };
  }
  const nodes = {};
  const childMap = {};
  const seen = new Set();

  async function folderName(token) {
    try {
      const pathJson = await getJson(`/space/api/explorer/v2/obj/path/?obj_token=${encodeURIComponent(token)}&obj_type=0&need_path=true`);
      const entity = pathJson && pathJson.data && pathJson.data.entities && pathJson.data.entities.nodes && pathJson.data.entities.nodes[token];
      if (entity) return entity.name || entity.title || "飞书云空间文件夹";
    } catch (_) {}
    return "飞书云空间文件夹";
  }

  async function loadFolder(token, parentToken, sortId) {
    if (!token || seen.has(token)) return;
    seen.add(token);
    if (!nodes[token]) {
      nodes[token] = {
        wiki_token: token,
        parent_wiki_token: parentToken || "",
        title: await folderName(token),
        obj_token: token,
        obj_type: 0,
        has_child: true,
        sort_id: sortId || 0,
        url: `${location.origin}/drive/folder/${token}`,
        wiki_node_type: 0,
        drive_node_type: 0,
      };
    }
    const children = [];
    let lastLabel = "";
    let page = 0;
    while (page < 100) {
      const url = `/space/api/explorer/v3/children/list/?thumbnail_width=1028&thumbnail_height=1028&thumbnail_policy=4&obj_type=0&obj_type=2&obj_type=22&obj_type=44&obj_type=3&obj_type=30&obj_type=8&obj_type=11&obj_type=12&obj_type=84&obj_type=123&obj_type=124&length=50&asc=1&rank=5&token=${encodeURIComponent(token)}${lastLabel ? `&last_label=${encodeURIComponent(lastLabel)}` : ""}&interflow_filter=CLIENT_VARS`;
      const json = await getJson(url);
      const data = json.data || {};
      const rawNodes = data.entities && data.entities.nodes || {};
      for (const childToken of data.node_list || []) {
        const raw = rawNodes[childToken];
        if (!raw) continue;
        const child = normalizeNode(raw, token, children.length + 1);
        if (!child.wiki_token) continue;
        nodes[child.wiki_token] = child;
        children.push(child.wiki_token);
      }
      if (!data.has_more) break;
      lastLabel = data.last_label || "";
      if (!lastLabel) break;
      page += 1;
    }
    childMap[token] = children;
    for (const childToken of children) {
      const child = nodes[childToken];
      if (child && child.obj_type === 0) await loadFolder(childToken, token, child.sort_id);
    }
  }

  await loadFolder(startToken, "", 1);
  return {
    spaceId: "",
    space: {space_name: nodes[startToken] && nodes[startToken].title || "飞书云空间文件夹"},
    rootList: [startToken],
    childMap,
    nodes,
    entryKind: "drive_folder",
  };
}
"""


def load_wiki_tree(
    cdp: CDPClient,
    wiki_url: str,
    start_token: str,
    args: argparse.Namespace | None = None,
) -> dict[str, Any]:
    throttle_request(args)
    wait_for_wiki_ready(cdp, timeout=35, args=args, expected_url=wiki_url)
    try:
        value = cdp.evaluate(f"({FEISHU_TREE_LOADER_JS})({js_string(start_token)})", timeout=120)
    except ExportError as exc:
        message = str(exc)
        if "FEISHU_AUTH_REQUIRED" in message:
            raise FeishuLoginRequired("飞书目录接口要求重新登录，请重新登录并保存凭证") from exc
        if "FEISHU_PERMISSION_DENIED" in message:
            raise ExportError("当前账号没有权限读取该飞书目录") from exc
        raise
    if not isinstance(value, dict) or not value.get("nodes"):
        raise ExportError("Failed to load Feishu Wiki tree")
    return value


def load_drive_folder_tree(
    cdp: CDPClient,
    folder_url: str,
    start_token: str,
    args: argparse.Namespace | None = None,
) -> dict[str, Any]:
    throttle_request(args)
    wait_for_wiki_ready(cdp, timeout=35, args=args, expected_url=folder_url)
    try:
        value = cdp.evaluate(f"({FEISHU_DRIVE_FOLDER_LOADER_JS})({js_string(start_token)})", timeout=180)
    except ExportError as exc:
        message = str(exc)
        if "FEISHU_AUTH_REQUIRED" in message:
            raise FeishuLoginRequired("飞书目录接口要求重新登录，请重新登录并保存凭证") from exc
        if "FEISHU_PERMISSION_DENIED" in message:
            raise ExportError("当前账号没有权限读取该飞书目录") from exc
        raise
    if not isinstance(value, dict) or not value.get("nodes"):
        raise ExportError("Failed to load Feishu Drive folder tree")
    return value


def feishu_node_file_type(node: dict[str, Any]) -> str:
    icon_info = node.get("icon_info")
    if isinstance(icon_info, str):
        try:
            parsed_icon_info = json.loads(icon_info)
            icon_info = parsed_icon_info if isinstance(parsed_icon_info, dict) else {}
        except json.JSONDecodeError:
            icon_info = {}
    icon_file_type = icon_info.get("file_type") if isinstance(icon_info, dict) else ""
    explicit = node.get("file_type") or icon_file_type or ""
    normalized = str(explicit).strip().lower().lstrip(".")
    if normalized:
        return normalized
    title = str(node.get("title") or "").strip().lower()
    if title.endswith(".md"):
        return "md"
    if title.endswith(".markdown"):
        return "markdown"
    return ""


def is_markdown_file_node(node: dict[str, Any]) -> bool:
    try:
        obj_type = int(node.get("obj_type"))
    except (TypeError, ValueError):
        return False
    return obj_type == FEISHU_FILE_OBJ_TYPE and feishu_node_file_type(node) in FEISHU_MARKDOWN_FILE_TYPES


def is_exportable_feishu_node(node: dict[str, Any]) -> bool:
    if not node.get("url"):
        return False
    try:
        obj_type = int(node.get("obj_type"))
    except (TypeError, ValueError):
        return False
    return obj_type == FEISHU_NATIVE_DOC_OBJ_TYPE or is_markdown_file_node(node)


def feishu_output_stem(node: dict[str, Any]) -> str:
    title = str(node.get("title") or "未命名")
    if is_markdown_file_node(node):
        lowered = title.lower()
        if lowered.endswith(".markdown"):
            title = title[:-9]
        elif lowered.endswith(".md"):
            title = title[:-3]
    return sanitize_filename(title)


def annotate_selectable_toc(ordered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    annotated: list[dict[str, Any]] = []
    for item in ordered:
        node = dict(item)
        node["selectable"] = is_exportable_feishu_node(node)
        annotated.append(node)
    return annotated


def select_exportable_docs(
    ordered: list[dict[str, Any]], selected_doc_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    exportable_docs = [item for item in ordered if is_exportable_feishu_node(item)]
    if not selected_doc_ids:
        return exportable_docs
    docs = [item for item in exportable_docs if item.get("wiki_token") in selected_doc_ids]
    if exportable_docs and not docs:
        preview = ", ".join(sorted(selected_doc_ids)[:5])
        raise ExportError(
            "选择的飞书文档未匹配当前目录，"
            "请重新读取目录后再试。未匹配 ID：" + preview
        )
    return docs


def order_tree(tree: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = tree.get("nodes") or {}
    child_map: dict[str, list[str]] = tree.get("childMap") or {}
    root_list: list[str] = tree.get("rootList") or []
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(token: str, level: int) -> None:
        if token in seen:
            return
        node = nodes.get(token)
        if not node:
            return
        seen.add(token)
        item = dict(node)
        item["level"] = level
        ordered.append(item)
        children = child_map.get(token) or []
        children = sorted(children, key=lambda child: int((nodes.get(child) or {}).get("sort_id") or 0))
        for child in children:
            walk(child, level + 1)

    for token in root_list:
        walk(token, 0)
    for token, node in sorted(nodes.items(), key=lambda pair: int(pair[1].get("sort_id") or 0)):
        if token not in seen and node.get("title"):
            walk(token, 0)
    return annotate_selectable_toc(ordered)


FEISHU_CONVERTER_JS = r"""
async (fallbackTitle) => {
  const images = [];
  const ZERO = /[\u200b\u200c\u200d\ufeff]/g;
  function clean(value) {
    return (value || "")
      .replace(ZERO, "")
      .replace(/\u00a0/g, " ")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }
  function escTable(value) {
    return clean(value).replace(/\|/g, "\\|").replace(/\n+/g, "<br>");
  }
  function inline(node) {
    if (!node) return "";
    if (node.nodeType === 3) return node.nodeValue.replace(ZERO, "");
    if (node.nodeType !== 1) return "";
    const tag = node.tagName.toLowerCase();
    if (tag === "br") return "\n";
    if (tag === "img") {
      const src = node.getAttribute("src") || node.getAttribute("data-src") || "";
      const alt = node.getAttribute("alt") || "image";
      if (src) images.push(src);
      return src ? `![${alt}](${src})` : "";
    }
    const text = [...node.childNodes].map(inline).join("");
    if (tag === "a") {
      const href = node.getAttribute("href") || node.getAttribute("data-href") || "";
      return href && clean(text) ? `[${clean(text)}](${href})` : text;
    }
    const style = node.getAttribute("style") || "";
    const cls = node.getAttribute("class") || "";
    let out = text;
    if ((/font-weight\s*:\s*(bold|[6-9]00)/i.test(style) || /\bbold\b/i.test(cls)) && clean(out)) out = `**${out}**`;
    if (/font-style\s*:\s*italic/i.test(style) && clean(out)) out = `*${out}*`;
    return out;
  }
  function blockText(el) {
    const lines = [...el.querySelectorAll(".ace-line")];
    if (lines.length) return clean(lines.map(line => inline(line)).join("\n"));
    return clean(inline(el) || el.innerText || "");
  }
  function renderTable(el) {
    const rows = [...el.querySelectorAll("table tr")]
      .map(tr => [...tr.children].map(td => escTable(blockText(td) || td.innerText || "")))
      .filter(row => row.length);
    if (!rows.length) return "";
    const max = Math.max(...rows.map(row => row.length));
    rows.forEach(row => { while (row.length < max) row.push(""); });
    return "| " + rows[0].join(" | ") + " |\n| " + Array(max).fill("---").join(" | ") + " |\n"
      + rows.slice(1).map(row => "| " + row.join(" | ") + " |").join("\n");
  }
  function renderImage(el) {
    const img = el.querySelector("img");
    if (!img) return "";
    const src = img.getAttribute("src") || img.getAttribute("data-src") || "";
    const alt = img.getAttribute("alt") || "image";
    if (src) images.push(src);
    return src ? `![${alt}](${src})` : "";
  }
  function quote(value) {
    const text = clean(value);
    return text ? text.split(/\n/).map(line => `> ${line}`).join("\n") : "";
  }
  function renderBlock(el) {
    const type = el.getAttribute("data-block-type") || "";
    const text = blockText(el);
    if (!text && !["image", "table", "divider"].includes(type)) return "";
    if (type === "heading1") return `## ${text}`;
    if (type === "heading2") return `### ${text}`;
    if (type === "heading3") return `#### ${text}`;
    if (type === "heading4") return `##### ${text}`;
    if (type === "heading5" || type === "heading6") return `###### ${text}`;
    if (type === "divider") return "---";
    if (type === "image") return renderImage(el);
    if (type === "table") return renderTable(el);
    if (type === "quote_container" || type === "callout") return quote(el.innerText || text);
    if (/bullet|unordered/i.test(type)) return text.split(/\n/).filter(Boolean).map(line => `- ${line}`).join("\n");
    if (/ordered|number/i.test(type)) return text.split(/\n/).filter(Boolean).map((line, i) => `${i + 1}. ${line}`).join("\n");
    if (/todo|check/i.test(type)) return text.split(/\n/).filter(Boolean).map(line => `- [ ] ${line}`).join("\n");
    if (/code/i.test(type)) return "```\n" + text + "\n```";
    return text;
  }

  function currentBlocks() {
    const root = document.querySelector(".root-render-unit-container > .render-unit-wrapper")
      || document.querySelector(".root-render-unit-container")
      || document.querySelector(".page-main-item.editor")
      || document.querySelector(".editor-container");
    return [...(root ? root.children : [])].filter(el => el.getAttribute && el.getAttribute("data-block-type"));
  }
  const pageTitle = clean((document.querySelector(".page-block-content") || {}).innerText || fallbackTitle || document.title.replace(/\s*-\s*飞书云文档\s*$/, ""));
  const initialRoot = document.querySelector(".root-render-unit-container")
    || document.querySelector(".page-main-item.editor")
    || document.querySelector(".editor-container")
    || document.body;
  function findScroller(root) {
    let el = root;
    while (el && el !== document.body) {
      const style = getComputedStyle(el);
      if (el.scrollHeight > el.clientHeight + 30 && /(auto|scroll)/.test(style.overflowY)) return el;
      el = el.parentElement;
    }
    return document.scrollingElement || document.documentElement;
  }
  const scroller = findScroller(initialRoot);
  const scrollWindow = scroller === document.scrollingElement || scroller === document.documentElement || scroller === document.body;
  function setScroll(y) {
    if (scrollWindow) window.scrollTo(0, y);
    else {
      scroller.scrollTop = y;
      scroller.dispatchEvent(new Event("scroll", {bubbles: true}));
    }
  }
  const seen = new Set();
  const rendered = [];
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const collect = () => {
    for (const block of currentBlocks()) {
      const key = block.getAttribute("data-record-id")
        || block.getAttribute("data-block-id")
        || `${block.getAttribute("data-block-type")}:${clean(block.innerText).slice(0, 80)}:${rendered.length}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const md = renderBlock(block);
      if (md) rendered.push(md);
    }
  };
  setScroll(0);
  await sleep(220);
  collect();
  let y = 0;
  let stable = 0;
  for (let i = 0; i < 80; i++) {
    const viewport = scrollWindow ? window.innerHeight : scroller.clientHeight;
    const maxY = Math.max(0, scroller.scrollHeight - viewport);
    if (y >= maxY && stable >= 2) break;
    y = Math.min(maxY, y + Math.max(360, Math.floor(viewport * 0.7)));
    setScroll(y);
    await sleep(260);
    const before = rendered.length;
    collect();
    stable = rendered.length === before ? stable + 1 : 0;
    if (y >= maxY) stable += 1;
  }
  setScroll(0);
  const body = rendered.filter(Boolean).join("\n\n").replace(/\n{3,}/g, "\n\n").trim();
  const markdown = "# " + pageTitle + "\n\n" + body + "\n";
  return {title: pageTitle, markdown, images: [...new Set(images)], blockCount: rendered.length, textLength: body.length, renderer: "native_doc"};
}
"""


FEISHU_MARKDOWN_FILE_LOADER_JS = r"""
async (objToken, wikiToken, mountPoint, fallbackTitle, allowPreviewFallback) => {
  const ZERO = /[\u200b\u200c\u200d\ufeff]/g;
  const MAX_BYTES = 50 * 1024 * 1024;
  const title = ((typeof store !== "undefined"
    && store.getState
    && store.getState().box_common_base
    && store.getState().box_common_base.currentDriveFileInfo
    && store.getState().box_common_base.currentDriveFileInfo.name) || fallbackTitle || "未命名.md").trim();
  const sourceErrors = [];

  function fatal(code, detail) {
    return new Error(`FEISHU_MARKDOWN_FATAL_${code}: ${detail}`);
  }

  function classifyResponse(response, path) {
    if (response.status === 401) throw new Error(`FEISHU_AUTH_REQUIRED: ${path}`);
    if (response.status === 403) throw new Error(`FEISHU_PERMISSION_DENIED: ${path}`);
    if (response.redirected && /\/accounts\/page\/login/i.test(new URL(response.url, location.origin).pathname)) {
      throw new Error(`FEISHU_AUTH_REQUIRED: ${path}`);
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
  }

  async function readLimited(response) {
    const lengthHeader = Number(response.headers.get('content-length') || 0);
    if (lengthHeader > MAX_BYTES) throw fatal('TOO_LARGE', 'Markdown file exceeds 50 MiB');
    if (!response.body || !response.body.getReader) {
      const buffer = await response.arrayBuffer();
      if (buffer.byteLength > MAX_BYTES) throw fatal('TOO_LARGE', 'Markdown file exceeds 50 MiB');
      return buffer;
    }
    const reader = response.body.getReader();
    const chunks = [];
    let total = 0;
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      total += part.value.byteLength;
      if (total > MAX_BYTES) {
        await reader.cancel();
        throw fatal('TOO_LARGE', 'Markdown file exceeds 50 MiB');
      }
      chunks.push(part.value);
    }
    const bytes = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return bytes.buffer;
  }

  function collectResources(markdown) {
    const images = [];
    const relativeResources = [];
    const add = (rawValue) => {
      let value = String(rawValue || '').trim().replace(/^<|>$/g, '');
      const titled = value.match(/^(.*?)(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))$/);
      if (titled) value = titled[1].trim();
      if (!value) return;
      if (/^https?:\/\//i.test(value)) images.push(value);
      else if (!/^(?:data:|#)/i.test(value)) relativeResources.push(value);
    };
    for (const match of markdown.matchAll(/!\[[^\]]*\]\(\s*([^\n)]*)\)/gi)) add(match[1]);
    for (const match of markdown.matchAll(/<img\b[^>]*\bsrc\s*=\s*["']([^"']+)["'][^>]*>/gi)) add(match[1]);
    const definitions = new Map();
    for (const match of markdown.matchAll(/^\s*\[([^\]]+)\]:\s*(\S+|<[^>]+>)/gim)) {
      definitions.set(match[1].trim().toLowerCase(), match[2]);
    }
    for (const match of markdown.matchAll(/!\[[^\]]*\]\[([^\]]+)\]/gi)) {
      add(definitions.get(match[1].trim().toLowerCase()) || '');
    }
    return {
      images: [...new Set(images)],
      relativeResources: [...new Set(relativeResources)],
    };
  }

  try {
    let fileInfo = {};
    let metadataTrusted = false;
    try {
      const infoResponse = await fetch('/space/api/box/file/info/', {
        method: 'POST',
        credentials: 'include',
        headers: {'content-type': 'application/json'},
        body: JSON.stringify({
          caller: mountPoint === 'wiki' ? 'wiki' : 'explorer',
          file_token: objToken,
          mount_point: mountPoint || 'wiki',
          mount_node_token: wikiToken,
          option_params: ['preview_meta', 'check_cipher'],
        }),
      });
      classifyResponse(infoResponse, '/space/api/box/file/info/');
      let infoJson;
      try { infoJson = await infoResponse.json(); }
      catch (_) { throw new Error('file info returned invalid JSON'); }
      if (!infoJson || infoJson.code !== 0 || !infoJson.data) {
        throw new Error(`file info HTTP ${infoResponse.status}, code ${infoJson && infoJson.code}`);
      }
      fileInfo = infoJson.data;
      const infoType = String(fileInfo.type || '').toLowerCase();
      const mimeType = String(fileInfo.mime_type || '').toLowerCase();
      if (!['md', 'markdown'].includes(infoType) && !mimeType.includes('markdown')) {
        throw fatal('UNSUPPORTED_TYPE', infoType || mimeType || 'unknown');
      }
      if (Number(fileInfo.size || 0) > MAX_BYTES) throw fatal('TOO_LARGE', 'Markdown file exceeds 50 MiB');
      metadataTrusted = true;
    } catch (error) {
      if (/FEISHU_(?:AUTH_REQUIRED|PERMISSION_DENIED)|FEISHU_MARKDOWN_FATAL_/.test(String(error))) throw error;
      sourceErrors.push(`metadata: ${error}`);
    }

    const version = String(fileInfo.data_version || fileInfo.version || '');
    const query = new URLSearchParams({mount_point: mountPoint || 'wiki'});
    if (version) query.set('version', version);
    const paths = [
      `/space/api/box/stream/download/all/${encodeURIComponent(objToken)}?${query}`,
    ];
    if (version) {
      const previewQuery = new URLSearchParams({
        preview_type: '16',
        version,
        mount_point: mountPoint || 'wiki',
      });
      paths.push(`/space/api/box/stream/download/preview/${encodeURIComponent(objToken)}?${previewQuery}`);
    }

    for (const path of paths) {
      try {
        const response = await fetch(path, {credentials: 'include'});
        classifyResponse(response, path);
        const contentType = String(response.headers.get('content-type') || '').toLowerCase();
        if (/text\/html|application\/(?:json|problem\+json)/.test(contentType)) {
          throw fatal('INVALID_CONTENT', `unexpected content type ${contentType}`);
        }
        if (!metadataTrusted && contentType && !/markdown|text\/plain|application\/octet-stream/.test(contentType)) {
          throw fatal('INVALID_CONTENT', `untrusted content type ${contentType}`);
        }
        const buffer = await readLimited(response);
        const expectedSize = Number(fileInfo.size || 0);
        if (expectedSize && buffer.byteLength !== expectedSize) {
          throw fatal('SIZE_MISMATCH', `expected ${expectedSize}, received ${buffer.byteLength}`);
        }
        let markdown;
        try { markdown = new TextDecoder('utf-8', {fatal: true}).decode(buffer).replace(/^\ufeff/, ''); }
        catch (error) { throw fatal('INVALID_UTF8', String(error)); }
        if (!markdown.trim()) throw fatal('EMPTY', 'Markdown file is empty');
        if (/^\s*<!doctype\s+html|^\s*<html\b/i.test(markdown)) {
          throw fatal('INVALID_CONTENT', 'download returned HTML');
        }
        if (!markdown.endsWith('\n')) markdown += '\n';
        const resources = collectResources(markdown);
        return {
          title: fileInfo.name || title,
          markdown,
          images: resources.images,
          relativeResources: resources.relativeResources,
          blockCount: markdown.split(/\r?\n/).length,
          textLength: markdown.length,
          renderer: 'markdown_file',
          contentType: response.headers.get('content-type') || fileInfo.mime_type || '',
          byteLength: buffer.byteLength,
        };
      } catch (error) {
        if (/FEISHU_(?:AUTH_REQUIRED|PERMISSION_DENIED)|FEISHU_MARKDOWN_FATAL_/.test(String(error))) throw error;
        sourceErrors.push(`${path.split('?')[0]}: ${error}`);
      }
    }
    throw new Error(sourceErrors.join('; '));
  } catch (error) {
    if (/FEISHU_(?:AUTH_REQUIRED|PERMISSION_DENIED)|FEISHU_MARKDOWN_FATAL_/.test(String(error))) throw error;
    if (!allowPreviewFallback) throw new Error(`Feishu Markdown source unavailable: ${error}`);
    const lines = [...document.querySelectorAll('.box-editor-kit .ace-line')].map((line) => {
      let text = (line.innerText || "").replace(ZERO, "");
      const heading = [...line.classList].find((name) => /^heading-h[1-6]$/.test(name));
      if (heading) text = `${"#".repeat(Number(heading.slice(-1)))} ${text}`;
      return text;
    });
    const markdown = lines.join("\n").trim();
    if (!markdown) throw new Error(`Unable to read Feishu Markdown file: ${error}`);
    const resources = collectResources(markdown);
    return {
      title,
      markdown: markdown + "\n",
      images: resources.images,
      relativeResources: resources.relativeResources,
      blockCount: lines.length,
      textLength: markdown.length,
      renderer: "markdown_preview_fallback",
      sourceError: String(error),
    };
  }
}
"""


def wait_for_doc_ready(
    cdp: CDPClient,
    timeout: int,
    args: argparse.Namespace | None = None,
    node: dict[str, Any] | None = None,
) -> None:
    deadline = time.time() + timeout
    markdown_file = is_markdown_file_node(node or {})
    while time.time() < deadline:
        check_stopped(args)
        selector = (
            "!!(document.querySelector('[data-sel=\"box-preview-container\"]') || "
            "document.querySelector('.box-preview-wrapper') || document.querySelector('.box-editor-kit .ace-line') || "
            "(typeof store !== 'undefined' && store.getState && store.getState().box_common_base && "
            "store.getState().box_common_base.currentDriveFileInfo))"
            if markdown_file
            else "!!(document.querySelector('.root-render-unit-container [data-block-type]') || document.querySelector('.page-main-item.editor'))"
        )
        ready = cdp.evaluate(selector, timeout=8)
        if ready:
            return
        time.sleep(0.5)
    kind = "Markdown 文件预览" if markdown_file else "文档正文"
    raise ExportError(f"飞书{kind}没有加载完成：{(node or {}).get('title') or '未命名'}")


def materialize_doc_dom(cdp: CDPClient) -> None:
    cdp.evaluate(
        "(async () => {"
        "const h = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);"
        "for (const p of [0, 0.25, 0.5, 0.75, 1, 0]) { window.scrollTo(0, Math.floor(h * p)); await new Promise(r => setTimeout(r, 160)); }"
        "})()",
        timeout=10,
    )


def extract_doc_markdown_current(
    cdp: CDPClient,
    node: dict[str, Any],
    args: argparse.Namespace | None = None,
) -> dict[str, Any]:
    check_stopped(args)
    if is_markdown_file_node(node):
        obj_token = str(node.get("obj_token") or node.get("wiki_token") or "")
        if not obj_token:
            raise ExportError(f"飞书 Markdown 文件缺少 obj_token：{node.get('title') or '未命名'}")
        mount_point = str(node.get("mount_point") or "wiki")

        def evaluate_markdown_file(allow_preview_fallback: bool) -> dict[str, Any]:
            expression = (
                f"({FEISHU_MARKDOWN_FILE_LOADER_JS})({js_string(obj_token)}, "
                f"{js_string(node.get('wiki_token') or '')}, {js_string(mount_point)}, "
                f"{js_string(node.get('title') or '未命名.md')}, "
                f"{'true' if allow_preview_fallback else 'false'})"
            )
            try:
                result = cdp.evaluate(expression, timeout=60)
            except ExportError as exc:
                message = str(exc)
                if "FEISHU_AUTH_REQUIRED" in message:
                    raise FeishuLoginRequired("飞书 Markdown 原文件接口要求重新登录") from exc
                if "FEISHU_PERMISSION_DENIED" in message:
                    raise FeishuPermissionDenied("当前账号没有权限读取该飞书 Markdown 文件") from exc
                if "FEISHU_MARKDOWN_FATAL_" in message:
                    raise FeishuMarkdownSourceInvalid(
                        f"飞书 Markdown 原文件校验失败：{node.get('title') or '未命名'}：{message}"
                    ) from exc
                raise
            if not isinstance(result, dict):
                raise ExportError(f"Unexpected Feishu Markdown response: {node.get('title')}")
            return result

        try:
            value = evaluate_markdown_file(False)
        except (FeishuLoginRequired, FeishuPermissionDenied, FeishuMarkdownSourceInvalid):
            raise
        except ExportError:
            wait_for_doc_ready(cdp, timeout=35, args=args, node=node)
            value = evaluate_markdown_file(True)
    else:
        wait_for_doc_ready(cdp, timeout=35, args=args, node=node)
        value = cdp.evaluate(f"({FEISHU_CONVERTER_JS})({js_string(node.get('title') or '未命名')})", timeout=60)
    if not isinstance(value, dict):
        raise ExportError(f"Unexpected Feishu doc response: {node.get('title')}")
    if value.get("renderer") == "markdown_preview_fallback":
        value["incomplete"] = True
        emit(args, f"飞书原始 Markdown 下载失败，已使用页面预览兜底：{node.get('title') or '未命名'}", level="warn")
    return value


def fetch_doc_markdown(
    cdp: CDPClient,
    node: dict[str, Any],
    args: argparse.Namespace | None = None,
) -> dict[str, Any]:
    check_stopped(args)
    throttle_request(args)
    url = node.get("url") or ""
    if not url:
        raise ExportError(f"Node has no URL: {node.get('title')}")
    cdp.navigate(url)
    return extract_doc_markdown_current(cdp, node, args)


def guess_extension(url: str, content_type: str | None) -> str:
    path = urllib.parse.urlparse(url).path.lower()
    match = re.search(r"\.([a-z0-9]{2,5})$", path)
    if match:
        ext = match.group(1)
        return "jpg" if ext == "jpeg" else ext
    content_type = content_type or ""
    if "svg" in content_type:
        return "svg"
    if "png" in content_type:
        return "png"
    if "jpeg" in content_type or "jpg" in content_type:
        return "jpg"
    if "gif" in content_type:
        return "gif"
    if "webp" in content_type:
        return "webp"
    return "png"


def cookie_header_for_url(cookies: list[dict[str, Any]], url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or "/"
    pairs: list[str] = []
    for cookie in cookies:
        domain = (cookie.get("domain") or "").lstrip(".")
        cookie_path = cookie.get("path") or "/"
        if not cookie.get("name") or cookie.get("value") is None:
            continue
        if host == domain or host.endswith("." + domain):
            if path.startswith(cookie_path):
                pairs.append(f"{cookie['name']}={cookie['value']}")
    return "; ".join(pairs)


def download_image(url: str, dest_dir: Path, cookies: list[dict[str, Any]], timeout: int) -> Path:
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.feishu.cn/"}
    cookie_header = cookie_header_for_url(cookies, url)
    if cookie_header:
        headers["Cookie"] = cookie_header
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
        ext = guess_extension(url, response.headers.get("Content-Type"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / (hashlib.sha1(url.encode("utf-8")).hexdigest()[:12] + "." + ext)
    if not target.exists():
        target.write_bytes(data)
    return target


def localize_images(
    cdp: CDPClient,
    markdown: str,
    images: list[str],
    md_path: Path,
    timeout: int,
    keep_remote: bool,
    args: argparse.Namespace | None = None,
) -> tuple[str, int, list[dict[str, str]]]:
    cdp.send("Network.enable")
    cookies = cdp.send("Network.getAllCookies", timeout=20).get("result", {}).get("cookies", [])
    success = 0
    failures: list[dict[str, str]] = []
    for url in sorted(set(images)):
        check_stopped(args)
        if not url.startswith(("http://", "https://")):
            continue
        try:
            target = download_image(url, md_path.parent / "assets", cookies, timeout)
            markdown = markdown.replace(url, os.path.relpath(target, md_path.parent).replace("\\", "/"))
            success += 1
        except Exception as exc:
            failures.append({"url": url, "error": str(exc)})
            emit(
                args,
                f"图片下载失败：{url[:160]}：{exc}",
                event="resource.download.failed",
                level="error",
                step="download_image",
                resource={"type": "image", "url": url, "host": urllib.parse.urlparse(url).netloc},
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            if not keep_remote:
                markdown = markdown.replace(url, "")
    return markdown, success, failures


def build_doc_paths(ordered: list[dict[str, Any]], tree: dict[str, Any], output: Path) -> dict[str, Path]:
    nodes: dict[str, dict[str, Any]] = tree.get("nodes") or {}
    root_list: list[str] = tree.get("rootList") or []
    root_parents = {nodes.get(token, {}).get("parent_wiki_token") or "" for token in root_list}
    children: dict[str, list[dict[str, Any]]] = {}
    by_token: dict[str, dict[str, Any]] = {item["wiki_token"]: item for item in ordered if item.get("wiki_token")}
    index_in_parent: dict[str, int] = {}
    for item in ordered:
        parent = item.get("parent_wiki_token") or "ROOT"
        if parent in root_parents:
            parent = "ROOT"
        children.setdefault(parent, []).append(item)
    for siblings in children.values():
        siblings.sort(key=lambda item: int(item.get("sort_id") or 0))
        for index, item in enumerate(siblings, start=1):
            index_in_parent[item["wiki_token"]] = index

    containers: dict[str, Path] = {"ROOT": output, "": output}
    for parent in root_parents:
        containers[parent] = output

    def ensure_container(token: str) -> Path:
        if token in containers:
            return containers[token]
        item = by_token.get(token)
        if not item:
            return output
        parent_token = item.get("parent_wiki_token") or "ROOT"
        if parent_token in root_parents:
            parent_token = "ROOT"
        parent = ensure_container(parent_token)
        directory = parent / f"{pad(index_in_parent.get(token, 1))}-{sanitize_filename(item.get('title') or '未命名')}"
        directory.mkdir(parents=True, exist_ok=True)
        containers[token] = directory
        return directory

    for item in ordered:
        if item.get("has_child"):
            ensure_container(item["wiki_token"])

    doc_paths: dict[str, Path] = {}
    for fallback_index, item in enumerate(ordered, start=1):
        token = item.get("wiki_token")
        if not token or not is_exportable_feishu_node(item):
            continue
        parent_token = item.get("parent_wiki_token") or "ROOT"
        if parent_token in root_parents:
            parent_token = "ROOT"
        parent_dir = ensure_container(parent_token)
        index = index_in_parent.get(token, fallback_index)
        doc_paths[token] = parent_dir / f"{pad(index)}-{feishu_output_stem(item)}.md"
    return doc_paths


def scan_exported_docs(output: Path) -> dict[str, Path]:
    exported: dict[str, Path] = {}
    if not output.exists():
        return exported
    pattern = re.compile(r"飞书(?:Wiki|Doc|Drive)Token:\s*([A-Za-z0-9]+)")
    for md_file in output.rglob("*.md"):
        try:
            text = md_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for match in pattern.finditer(text):
            exported[match.group(1)] = md_file
    return exported


def write_index(
    output: Path,
    tree: dict[str, Any],
    ordered: list[dict[str, Any]],
    doc_paths: dict[str, Path],
    selected_doc_ids: set[str] | None = None,
) -> None:
    space = tree.get("space") or {}
    title = space.get("space_name") or "飞书知识库"
    index_path = output / "00-知识库入口.md"
    lines = [f"# {title}", "", "> 从飞书知识库导出。", ""]
    for item in ordered:
        token = item.get("wiki_token")
        if not token:
            continue
        if selected_doc_ids and token not in selected_doc_ids:
            continue
        indent = "  " * max(0, int(item.get("level") or 0))
        doc_path = doc_paths.get(token)
        label = item.get("title") or "未命名"
        if doc_path:
            rel_path = os.path.relpath(doc_path, index_path.parent).replace("\\", "/")
            lines.append(f"{indent}- [{label}]({rel_path})")
        else:
            lines.append(f"{indent}- **{label}**")
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def scan_wiki_toc(args: argparse.Namespace) -> dict[str, Any]:
    host, origin, start_token, entry_url, kind = parse_feishu_entry_url(args.wiki_url)
    if kind == "drive_folder":
        cdp, chrome_proc = connect_entry_browser(args, entry_url, host)
        try:
            session = prepare_entry_session(cdp, args, entry_url)
            emit(args, "开始读取飞书云空间文件夹目录。")
            tree = run_with_auth_retry(
                cdp,
                args,
                entry_url,
                lambda: load_drive_folder_tree(cdp, entry_url, start_token, args),
                already_restored=bool(session.get("restoredCookies")),
            )
            ordered = order_tree(tree)
            return {
                "tree": tree,
                "ordered": ordered,
                "totalDocs": sum(1 for item in ordered if is_exportable_feishu_node(item)),
                "entryKind": "drive_folder",
            }
        finally:
            cdp.close()
            if chrome_proc and args.close_started_chrome:
                chrome_proc.terminate()

    if kind != "wiki":
        cdp, chrome_proc = connect_entry_browser(args, entry_url, host)
        try:
            node = {
                "wiki_token": start_token,
                "parent_wiki_token": "",
                "title": "飞书云文档",
                "obj_token": start_token,
                "obj_type": FEISHU_NATIVE_DOC_OBJ_TYPE,
                "has_child": False,
                "sort_id": 1,
                "url": entry_url,
                "wiki_node_type": 0,
                "level": 0,
                "entry_kind": "document",
            }
            prepare_entry_session(cdp, args, entry_url)
            result = extract_doc_markdown_current(cdp, node, args)
            node["title"] = result.get("title") or "飞书云文档"
            tree = {
                "spaceId": "",
                "space": {"space_name": result.get("title") or "飞书云文档"},
                "rootList": [start_token],
                "childMap": {},
                "nodes": {start_token: node},
            }
            return {"tree": tree, "ordered": [node], "totalDocs": 1, "entryKind": "document"}
        finally:
            cdp.close()
            if chrome_proc and args.close_started_chrome:
                chrome_proc.terminate()

    wiki_url = entry_url
    cdp, chrome_proc = connect_wiki_browser(args, wiki_url, host, start_token)
    try:
        session = prepare_entry_session(cdp, args, wiki_url)
        emit(args, "开始读取飞书目录。")
        tree = run_with_auth_retry(
            cdp,
            args,
            wiki_url,
            lambda: load_wiki_tree(cdp, wiki_url, start_token, args),
            already_restored=bool(session.get("restoredCookies")),
        )
        ordered = order_tree(tree)
        return {
            "tree": tree,
            "ordered": ordered,
            "totalDocs": sum(1 for item in ordered if is_exportable_feishu_node(item)),
        }
    finally:
        cdp.close()
        if chrome_proc and args.close_started_chrome:
            chrome_proc.terminate()


def export_wiki(args: argparse.Namespace) -> dict[str, Any]:
    host, origin, start_token, wiki_url, entry_kind = parse_feishu_entry_url(args.wiki_url)
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = open_checkpoint_from_args(args, "feishu", "export")
    cdp, chrome_proc = (
        connect_wiki_browser(args, wiki_url, host, start_token)
        if entry_kind == "wiki"
        else connect_entry_browser(args, wiki_url, host)
    )
    try:
        emit(args, "Chrome page is ready. If login is required, finish login in Chrome.")
        if args.wait_login:
            input("Press Enter after the Feishu Wiki page is logged in and visible...")
        session = prepare_entry_session(cdp, args, wiki_url)

        prefetched_results: dict[str, dict[str, Any]] = {}
        if entry_kind == "wiki":
            tree = run_with_auth_retry(
                cdp,
                args,
                wiki_url,
                lambda: load_wiki_tree(cdp, wiki_url, start_token, args),
                already_restored=bool(session.get("restoredCookies")),
            )
            ordered = order_tree(tree)
        elif entry_kind == "drive_folder":
            tree = run_with_auth_retry(
                cdp,
                args,
                wiki_url,
                lambda: load_drive_folder_tree(cdp, wiki_url, start_token, args),
                already_restored=bool(session.get("restoredCookies")),
            )
            ordered = order_tree(tree)
        else:
            node = {
                "wiki_token": start_token,
                "parent_wiki_token": "",
                "title": "飞书云文档",
                "obj_token": start_token,
                "obj_type": FEISHU_NATIVE_DOC_OBJ_TYPE,
                "has_child": False,
                "sort_id": 1,
                "url": wiki_url,
                "wiki_node_type": 0,
                "level": 0,
                "entry_kind": "document",
            }
            probe = extract_doc_markdown_current(cdp, node, args)
            node["title"] = probe.get("title") or "飞书云文档"
            prefetched_results[start_token] = probe
            tree = {
                "spaceId": "",
                "space": {"space_name": probe.get("title") or "飞书云文档"},
                "rootList": [start_token],
                "childMap": {},
                "nodes": {start_token: node},
            }
            ordered = [node]
        selected_doc_ids = set(getattr(args, "selected_doc_ids", None) or [])
        docs = select_exportable_docs(ordered, selected_doc_ids)
        doc_paths = build_doc_paths(ordered, tree, output)
        existing = scan_exported_docs(output)
        for token, old_path in existing.items():
            doc_paths[token] = old_path

        def checkpoint_key(doc: dict[str, Any]) -> str:
            return f"feishu:doc:{doc.get('wiki_token') or doc.get('obj_token') or doc.get('url') or ''}"

        if checkpoint:
            checkpoint.start_task(
                {
                    "source": wiki_url,
                    "outputDir": str(output),
                    "entryKind": entry_kind,
                    "totalDocs": len(docs),
                    "resume": bool(getattr(args, "resume", False)),
                    "retryFailed": bool(getattr(args, "retry_failed", False)),
                }
            )
            for doc in docs:
                token = str(doc.get("wiki_token") or doc.get("obj_token") or "")
                checkpoint.upsert_item(
                    checkpoint_key(doc),
                    title=str(doc.get("title") or token),
                    source_url=str(doc.get("url") or origin + "/wiki/" + token),
                    source_id=token,
                    metadata={"doc": doc},
                )
            if getattr(args, "retry_failed", False):
                docs = [doc for doc in docs if checkpoint.item_status(checkpoint_key(doc)) == "failed"]

        exported = 0
        skipped = 0
        image_success = 0
        failures: list[dict[str, str]] = []
        image_failures: list[dict[str, Any]] = []
        stopped = False

        emit(
            args,
            f"开始导出飞书文档：共 {len(docs)} 篇。",
            event="task.started",
            totals={"documents": len(docs), "nodes": len(ordered)},
            output=str(output),
            entryKind=entry_kind,
        )

        for index, doc in enumerate(docs, start=1):
            if stop_requested(args):
                stopped = True
                emit(args, "收到停止请求，正在结束并写入已完成的导出结果。")
                break
            token = doc["wiki_token"]
            md_path = doc_paths[token]
            item_key = checkpoint_key(doc)
            if checkpoint and getattr(args, "resume", False) and not args.update_existing and checkpoint.item_status(item_key) == "completed":
                skipped += 1
                continue
            if args.incremental and token in existing and not args.update_existing:
                if checkpoint:
                    checkpoint.complete_item(item_key, local_path=str(md_path), metadata={"doc": doc, "skippedExisting": True})
                skipped += 1
                continue
            try:
                if checkpoint:
                    checkpoint.start_item(item_key, "content")
                emit(
                    args,
                    f"开始导出文档：{doc.get('title') or token}",
                    event="document.export.started",
                    doc={"id": token, "title": doc.get("title") or "", "index": index, "path": str(md_path)},
                )
                result = prefetched_results.pop(token, None) or run_with_auth_retry(
                    cdp,
                    args,
                    wiki_url,
                    lambda: fetch_doc_markdown(cdp, doc, args),
                    already_restored=bool(session.get("restoredCookies")),
                )
                markdown = result.get("markdown") or f"# {doc.get('title') or '未命名'}\n"
                markdown += (
                    f"\n---\n\n来源: {doc.get('url') or origin + '/wiki/' + token}\n"
                    f"飞书{'Wiki' if entry_kind == 'wiki' else ('Drive' if entry_kind == 'drive_folder' else 'Doc')}Token: {token}\n"
                    f"飞书ObjToken: {doc.get('obj_token') or ''}\n"
                    f"飞书ObjType: {doc.get('obj_type')}\n"
                )
                markdown, count, img_errors = localize_images(
                    cdp,
                    markdown,
                    result.get("images") or [],
                    md_path,
                    args.download_timeout,
                    args.keep_remote_images,
                    args,
                )
                relative_resources = [
                    str(resource).strip()
                    for resource in (result.get("relativeResources") or [])
                    if str(resource).strip()
                ]
                relative_errors = [
                    {
                        "url": resource,
                        "error": "源 Markdown 引用的相对资源未随飞书 Wiki 节点提供",
                    }
                    for resource in dict.fromkeys(relative_resources)
                ]
                if relative_errors:
                    img_errors.extend(relative_errors)
                    emit(
                        args,
                        f"文档包含 {len(relative_errors)} 个飞书未提供的相对图片资源：{doc.get('title') or token}",
                        event="resource.download.failed",
                        level="warn",
                        step="resolve_relative_resource",
                        doc={"id": token, "title": doc.get("title") or "", "path": str(md_path)},
                        stats={"relativeResourceFailures": len(relative_errors)},
                    )
                image_success += count
                if img_errors:
                    image_failures.append({"document": doc.get("title"), "path": str(md_path), "failures": img_errors})
                md_path.parent.mkdir(parents=True, exist_ok=True)
                md_path.write_text(markdown, encoding="utf-8")
                incomplete = bool(result.get("incomplete"))
                if incomplete:
                    failures.append(
                        {
                            "title": doc.get("title") or "",
                            "wiki_token": token,
                            "error": "飞书原始 Markdown 下载失败，已保留页面预览兜底内容，但内容可能不完整",
                            "local_path": str(md_path),
                        }
                    )
                if checkpoint:
                    if incomplete:
                        checkpoint.fail_item(item_key, "原始 Markdown 下载失败，页面预览兜底内容可能不完整")
                    elif img_errors:
                        checkpoint.fail_item(item_key, f"{len(img_errors)} 个图片下载失败")
                    else:
                        checkpoint.complete_item(item_key, local_path=str(md_path), metadata={"doc": doc})
                exported += 1
                emit(
                    args,
                    (
                        f"文档已导出但内容可能不完整：{doc.get('title') or token}"
                        if incomplete
                        else f"文档导出完成：{doc.get('title') or token}"
                    ),
                    event="document.export.completed",
                    level="warn" if incomplete or img_errors else "success",
                    doc={"id": token, "title": doc.get("title") or "", "index": index, "path": str(md_path)},
                    stats={
                        "imageSuccessInDoc": count,
                        "imageFailuresInDoc": len(img_errors),
                        "contentIncomplete": incomplete,
                    },
                )
            except ExportStopped:
                if checkpoint:
                    checkpoint.fail_item(item_key, "stopped")
                stopped = True
                emit(args, "收到停止请求，当前文档未写入，正在结束。")
                break
            except Exception as exc:
                if checkpoint:
                    checkpoint.fail_item(item_key, str(exc))
                failures.append({"title": doc.get("title") or "", "wiki_token": token, "error": str(exc)})
                emit(
                    args,
                    f"文档导出失败：{doc.get('title') or token}：{exc}",
                    event="document.export.failed",
                    level="error",
                    doc={"id": token, "title": doc.get("title") or "", "index": index, "path": str(md_path)},
                    error={"type": type(exc).__name__, "message": str(exc)},
                )

            if index % args.progress_every == 0 or index == len(docs):
                emit(
                    args,
                    f"progress {index}/{len(docs)} exported={exported} skipped={skipped} "
                    f"image_success={image_success} failures={len(failures)}",
                    event="task.progress",
                    progress={"current": index, "total": len(docs)},
                    stats={
                        "exportedDocs": exported,
                        "skippedDocs": skipped,
                        "imageSuccess": image_success,
                        "failureCount": len(failures),
                        "imageFailureCount": sum(len(item["failures"]) for item in image_failures),
                    },
                )

        write_index(output, tree, ordered, doc_paths, selected_doc_ids or None)
        report = {
            "provider": "feishu",
            "entryKind": entry_kind,
            "exportMode": "incremental" if args.incremental else "full",
            "space": tree.get("space") or {},
            "spaceId": tree.get("spaceId"),
            "wikiUrl": wiki_url,
            "output": str(output),
            "totalNodes": len(ordered),
            "totalDocs": len(docs),
            "selectedDocs": len(docs),
            "exportedDocs": exported,
            "skippedDocs": skipped,
            "stopped": stopped,
            "imageSuccess": image_success,
            "imageFailureCount": sum(len(item["failures"]) for item in image_failures),
            "resourceFailureCount": sum(len(item["failures"]) for item in image_failures),
            "requestCount": int(getattr(args, "_request_count", 0) or 0),
            "requestDelaySeconds": float(getattr(args, "request_delay", 0.8) or 0),
            "requestJitterSeconds": float(getattr(args, "request_jitter", 0.4) or 0),
            "failures": failures,
            "imageFailures": image_failures,
            "exportedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        if checkpoint:
            report["checkpoint"] = checkpoint.stats()
        report_path = output / "00-导出报告.json"
        report = finalize_report(report, provider="feishu", mode="export", report_file=report_path, output=output)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if checkpoint:
            if stopped:
                checkpoint.fail_task("stopped", status="stopped")
            elif failures or image_failures:
                checkpoint.fail_task(
                    f"{len(failures)} 个文档失败，{sum(len(item['failures']) for item in image_failures)} 个图片失败",
                    status="failed",
                )
            else:
                checkpoint.complete_task(report)
        emit(
            args,
            "飞书导出完成" if not stopped else "飞书导出已停止",
            event="task.completed" if not stopped else "task.stopped",
            level="success" if not stopped and not failures and not image_failures else "warn",
            reportFile=str(report_path),
            stats={
                "exportedDocs": exported,
                "skippedDocs": skipped,
                "failureCount": len(failures),
                "imageSuccess": image_success,
                "imageFailureCount": report.get("imageFailureCount", 0),
            },
        )
        return report
    finally:
        cdp.close()
        if chrome_proc and args.close_started_chrome:
            chrome_proc.terminate()
        if checkpoint:
            checkpoint.close()


def login_and_save_auth(args: argparse.Namespace, wait_callback: Callable[[], None] | None = None) -> dict[str, Any]:
    host, _origin, start_token, entry_url, entry_kind = parse_feishu_entry_url(args.wiki_url)
    auth_file = auth_path_from_args(args)
    cdp, chrome_proc = (
        connect_wiki_browser(args, entry_url, host, start_token)
        if entry_kind == "wiki"
        else connect_entry_browser(args, entry_url, host)
    )
    try:
        emit(args, "Chrome opened. Log in to Feishu in the browser.")
        if wait_callback:
            wait_callback()
        elif float(getattr(args, "login_wait_seconds", 0) or 0) > 0:
            wait_seconds = float(getattr(args, "login_wait_seconds", 0) or 0)
            deadline = time.time() + wait_seconds
            emit(args, f"请在浏览器中完成登录，工具将在 {int(wait_seconds)} 秒后自动保存凭证。")
            while time.time() < deadline:
                check_stopped(args)
                time.sleep(1)
        else:
            input("After login is complete and the Feishu page is visible, press Enter...")
        check_stopped(args)
        cdp.navigate(entry_url)
        time.sleep(2)
        check_stopped(args)
        result = save_auth_state(cdp, auth_file, entry_url, host)
        emit(args, f"Saved {result['cookieCount']} auth cookies to {auth_file}")
        return result
    finally:
        cdp.close()
        if chrome_proc and args.close_started_chrome:
            chrome_proc.terminate()


def run_gui() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
    from gui_utils import create_scrollable_body

    root = tk.Tk()
    root.title("飞书知识库导出工具")
    root.geometry("980x780")
    body = create_scrollable_body(root)

    wiki_var = tk.StringVar(value=DEFAULT_WIKI_URL)
    output_var = tk.StringVar(value=str((PROJECT_DIR / "exports" / "feishu").resolve()))
    auth_var = tk.StringVar(value=str(default_auth_path()))
    profile_var = tk.StringVar(value=str(default_profile_path()))
    browser_path_var = tk.StringVar(value="")
    port_var = tk.StringVar(value=str(DEFAULT_PORT))
    request_delay_var = tk.StringVar(value="0.8")
    request_jitter_var = tk.StringVar(value="0.4")
    close_chrome_var = tk.BooleanVar(value=False)
    log_queue: queue.Queue[str] = queue.Queue()
    current_stop_event: dict[str, threading.Event | None] = {"event": None}
    buttons: list[tk.Widget] = []
    toc_status_var = tk.StringVar(value="目录：未读取")
    toc_state: dict[str, Any] = {"tree": {}, "ordered": [], "selected": set()}

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

    def browse_output() -> None:
        selected = filedialog.askdirectory(initialdir=output_var.get() or str(PROJECT_DIR))
        if selected:
            output_var.set(selected)

    def browse_auth() -> None:
        selected = filedialog.asksaveasfilename(
            initialfile=Path(auth_var.get()).name or DEFAULT_AUTH_FILE,
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if selected:
            auth_var.set(selected)

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
            return
        messagebox.showwarning(
            "未找到浏览器",
            "没有自动找到 Chrome、Edge 或 Chromium。\n\n请点击“选择”手动指定浏览器程序，或者先下载安装 Chrome/Edge。",
        )

    def open_output() -> None:
        path = Path(output_var.get())
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])

    def build_args(
        *,
        incremental: bool,
        update_existing: bool,
        selected_doc_ids: list[str] | None = None,
    ) -> argparse.Namespace:
        if not wiki_var.get().strip():
            raise ExportError("请填写飞书知识库 URL")
        if not output_var.get().strip():
            raise ExportError("请填写输出目录")
        return argparse.Namespace(
            wiki_url=wiki_var.get().strip(),
            output=output_var.get().strip(),
            port=int(port_var.get().strip() or DEFAULT_PORT),
            profile_dir=profile_var.get().strip() or None,
            browser_path=browser_path_var.get().strip() or None,
            auth_file=auth_var.get().strip() or str(default_auth_path()),
            skip_auth_load=False,
            wait_login=False,
            incremental=incremental,
            update_existing=update_existing,
            selected_doc_ids=selected_doc_ids,
            download_timeout=45,
            progress_every=10,
            request_delay=max(0.0, float(request_delay_var.get().strip() or "0.8")),
            request_jitter=max(0.0, float(request_jitter_var.get().strip() or "0.4")),
            keep_remote_images=True,
            close_started_chrome=close_chrome_var.get(),
            stop_event=None,
            log_callback=log,
        )

    def wait_for_login_dialog() -> None:
        event = threading.Event()

        def ask() -> None:
            messagebox.showinfo(
                "完成登录后继续",
                "浏览器已经打开。\n\n请在浏览器里完成飞书登录，并确认知识库页面已经能正常打开。\n完成后回到这里点击“确定”，工具会保存登录凭证。",
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

    def exportable_doc_tokens() -> list[str]:
        ordered = toc_state.get("ordered") or []
        docs = [item for item in ordered if is_exportable_feishu_node(item)]
        return [str(item.get("wiki_token")) for item in docs if item.get("wiki_token")]

    def refresh_toc_status() -> None:
        keys = exportable_doc_tokens()
        selected = toc_state.get("selected") or set()
        if not keys:
            toc_status_var.set("目录：未读取")
        else:
            toc_status_var.set(f"目录：共 {len(keys)} 篇，已选择 {len(selected)} 篇")

    def tree_children() -> dict[str, list[dict[str, Any]]]:
        tree = toc_state.get("tree") or {}
        ordered = toc_state.get("ordered") or []
        nodes: dict[str, dict[str, Any]] = tree.get("nodes") or {}
        root_list: list[str] = tree.get("rootList") or []
        root_parents = {nodes.get(token, {}).get("parent_wiki_token") or "" for token in root_list}
        children: dict[str, list[dict[str, Any]]] = {}
        for item in ordered:
            parent = item.get("parent_wiki_token") or "ROOT"
            if parent in root_parents:
                parent = "ROOT"
            children.setdefault(parent, []).append(item)
        for bucket in children.values():
            bucket.sort(key=lambda item: int(item.get("sort_id") or 0))
        return children

    def render_toc_tree() -> None:
        toc_tree.delete(*toc_tree.get_children(""))
        selected: set[str] = toc_state.get("selected") or set()
        exportable = set(exportable_doc_tokens())
        children = tree_children()

        def docs_under(token: str) -> list[str]:
            result: list[str] = []
            for child in children.get(token, []):
                child_token = str(child.get("wiki_token") or "")
                if child_token in exportable:
                    result.append(child_token)
                result.extend(docs_under(child_token))
            return result

        def add_item(parent_iid: str, item: dict[str, Any]) -> None:
            token = str(item.get("wiki_token") or "")
            if not token:
                return
            title = item.get("title") or "未命名"
            if token in exportable:
                mark = "☑" if token in selected else "☐"
                suffix = f"  ({sum(1 for key in docs_under(token) if key in selected)}/{len(docs_under(token))})" if children.get(token) else ""
                toc_tree.insert(parent_iid, "end", iid=token, text=f"{mark} {title}{suffix}", open=True)
            else:
                docs = docs_under(token)
                selected_count = sum(1 for key in docs if key in selected)
                mark = "☑" if docs and selected_count == len(docs) else ("◩" if selected_count else "☐")
                toc_tree.insert(parent_iid, "end", iid=token, text=f"{mark} {title}  ({selected_count}/{len(docs)})", open=True)
            for child in children.get(token, []):
                add_item(token, child)

        for item in children.get("ROOT", []):
            add_item("", item)
        refresh_toc_status()

    def set_all_toc_selected(selected: bool) -> None:
        keys = exportable_doc_tokens()
        if not keys:
            messagebox.showinfo("还没有目录", "请先点击“读取目录”。")
            return
        toc_state["selected"] = set(keys) if selected else set()
        render_toc_tree()

    def invert_toc_selected() -> None:
        keys = set(exportable_doc_tokens())
        if not keys:
            messagebox.showinfo("还没有目录", "请先点击“读取目录”。")
            return
        toc_state["selected"] = keys - set(toc_state.get("selected") or set())
        render_toc_tree()

    def selected_doc_ids_for_export() -> list[str] | None:
        if not toc_state.get("ordered"):
            return None
        selected = sorted(toc_state.get("selected") or set())
        if not selected:
            raise ExportError("目录已读取，但没有选择任何文档。")
        return selected

    def toggle_toc_selection(event: Any | None = None) -> str:
        node = toc_tree.focus()
        if not node:
            return "break"
        children = tree_children()
        exportable = set(exportable_doc_tokens())
        selected: set[str] = toc_state.get("selected") or set()

        def docs_under(token: str) -> list[str]:
            docs = [token] if token in exportable else []
            for child in children.get(token, []):
                child_token = str(child.get("wiki_token") or "")
                docs.extend(docs_under(child_token))
            return docs

        docs = docs_under(node)
        if docs and all(key in selected for key in docs):
            selected.difference_update(docs)
        else:
            selected.update(docs)
        toc_state["selected"] = selected
        render_toc_tree()
        if toc_tree.exists(node):
            toc_tree.focus(node)
        return "break"

    def run_worker(
        name: str,
        args: argparse.Namespace,
        fn: Callable[[], dict[str, Any]],
        on_success: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        def worker() -> None:
            stop_event = threading.Event()
            args.stop_event = stop_event
            current_stop_event["event"] = stop_event
            root.after(0, lambda: set_running_state(True))
            log(f"开始：{name}")
            try:
                result = fn()
                summary = {
                    key: result.get(key)
                    for key in ("totalDocs", "exportedDocs", "skippedDocs", "requestCount", "stopped", "imageSuccess", "imageFailureCount")
                    if key in result
                }
                log(f"{'已停止' if result.get('stopped') else '完成'}：{name}")
                if summary:
                    log(json.dumps(summary, ensure_ascii=False, indent=2))
                if on_success:
                    root.after(0, lambda result=result: on_success(result))
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
        try:
            args = build_args(incremental=True, update_existing=False)
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        run_worker("登录并保存凭证", args, lambda: login_and_save_auth(args, wait_for_login_dialog))

    def on_toc_loaded(result: dict[str, Any]) -> None:
        toc_state["tree"] = result.get("tree") or {}
        toc_state["ordered"] = result.get("ordered") or []
        toc_state["selected"] = set(exportable_doc_tokens())
        render_toc_tree()

    def do_scan_toc() -> None:
        try:
            args = build_args(incremental=True, update_existing=False)
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        run_worker("读取目录", args, lambda: scan_wiki_toc(args), on_toc_loaded)

    def do_incremental() -> None:
        try:
            args = build_args(incremental=True, update_existing=False, selected_doc_ids=selected_doc_ids_for_export())
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        run_worker("增量更新缺失文档", args, lambda: export_wiki(args))

    def do_full_export() -> None:
        try:
            args = build_args(incremental=False, update_existing=True, selected_doc_ids=selected_doc_ids_for_export())
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        run_worker("全量覆盖导出", args, lambda: export_wiki(args))

    form = tk.Frame(body, padx=14, pady=12)
    form.pack(fill="x")
    form.columnconfigure(1, weight=1)

    def row(label: str, variable: tk.StringVar, row_index: int, browse: Callable[[], None] | None = None) -> None:
        tk.Label(form, text=label, anchor="w").grid(row=row_index, column=0, sticky="w", pady=5)
        tk.Entry(form, textvariable=variable).grid(row=row_index, column=1, sticky="ew", padx=8, pady=5)
        if browse:
            tk.Button(form, text="选择", command=browse).grid(row=row_index, column=2, pady=5)

    def browser_row(row_index: int) -> None:
        tk.Label(form, text="浏览器程序路径", anchor="w").grid(row=row_index, column=0, sticky="w", pady=5)
        tk.Entry(form, textvariable=browser_path_var).grid(row=row_index, column=1, sticky="ew", padx=8, pady=5)
        tk.Button(form, text="选择", command=browse_browser).grid(row=row_index, column=2, pady=5)
        tk.Button(form, text="查找", command=detect_browser).grid(row=row_index, column=3, padx=(6, 0), pady=5)

    row("飞书知识库 URL", wiki_var, 0)
    row("输出目录", output_var, 1, browse_output)
    row("凭证文件", auth_var, 2, browse_auth)
    row("浏览器配置目录", profile_var, 3, browse_profile)
    browser_row(4)
    row("调试端口", port_var, 5)
    row("请求延迟秒", request_delay_var, 6)
    row("请求随机浮动秒", request_jitter_var, 7)
    tk.Checkbutton(form, text="导出后关闭本工具启动的浏览器", variable=close_chrome_var).grid(row=8, column=1, sticky="w", pady=5)

    actions = tk.Frame(body, padx=14, pady=4)
    actions.pack(fill="x")
    buttons.extend(
        [
            tk.Button(actions, text="1. 登录并保存凭证", command=do_login, width=18),
            tk.Button(actions, text="2. 读取目录", command=do_scan_toc, width=14),
            tk.Button(actions, text="3. 增量导出选中/全部", command=do_incremental, width=20),
            tk.Button(actions, text="4. 全量导出选中/全部", command=do_full_export, width=20),
            tk.Button(actions, text="停止当前任务", command=stop_current_task, width=14, state="disabled"),
            tk.Button(actions, text="打开输出目录", command=open_output, width=14),
        ]
    )
    stop_button = buttons[-2]
    for button in buttons:
        button.pack(side="left", padx=5, pady=6)

    toc_frame = tk.LabelFrame(body, text="目录选择", padx=10, pady=8)
    toc_frame.pack(fill="both", expand=False, padx=14, pady=8)
    toc_header = tk.Frame(toc_frame)
    toc_header.pack(fill="x")
    tk.Label(toc_header, textvariable=toc_status_var, anchor="w").pack(side="left")
    tk.Button(toc_header, text="全选", command=lambda: set_all_toc_selected(True), width=8).pack(side="right", padx=4)
    tk.Button(toc_header, text="全不选", command=lambda: set_all_toc_selected(False), width=8).pack(side="right", padx=4)
    tk.Button(toc_header, text="反选", command=invert_toc_selected, width=8).pack(side="right", padx=4)

    toc_tree_frame = tk.Frame(toc_frame)
    toc_tree_frame.pack(fill="both", expand=True, pady=(8, 0))
    toc_tree = ttk.Treeview(toc_tree_frame, show="tree", height=10, selectmode="browse")
    toc_scroll = ttk.Scrollbar(toc_tree_frame, orient="vertical", command=toc_tree.yview)
    toc_tree.configure(yscrollcommand=toc_scroll.set)
    toc_tree.pack(side="left", fill="both", expand=True)
    toc_scroll.pack(side="right", fill="y")
    toc_tree.bind("<Double-1>", toggle_toc_selection)
    toc_tree.bind("<space>", toggle_toc_selection)

    note = tk.Label(
        body,
        text="说明：先读取目录可选择导出范围；未读取目录时默认导出全部。凭证文件保存的是登录 Cookie，不保存密码。",
        anchor="w",
        padx=14,
    )
    note.pack(fill="x")

    log_text = scrolledtext.ScrolledText(body, height=12, state="disabled")
    log_text.pack(fill="both", expand=True, padx=14, pady=12)
    poll_log()
    root.mainloop()
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Feishu Wiki to Markdown.")
    parser.add_argument("--gui", action="store_true", help="Open the graphical interface")
    parser.add_argument("--login", action="store_true", help="Open browser, let you log in, then save auth cookies")
    parser.add_argument("--login-wait-seconds", type=float, default=0.0, help="For non-interactive GUI wrappers, wait this many seconds before saving login cookies")
    parser.add_argument("--scan-toc", action="store_true", help="Read the Feishu Wiki directory and print it as JSON, without exporting")
    parser.add_argument("--wiki-url", help="Feishu Wiki URL, for example https://xxx.feishu.cn/wiki/<token>")
    parser.add_argument("--output", help="Output directory")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Chrome remote debugging port")
    parser.add_argument("--profile-dir", help=f"Chrome profile dir. Omit to auto-use {default_profile_path()}")
    parser.add_argument("--browser-path", help="Optional Chrome/Edge/Chromium executable path")
    parser.add_argument("--auth-file", help=f"Auth cookie file. Omit to auto-use {default_auth_path()}")
    parser.add_argument("--skip-auth-load", action="store_true", help="Do not load saved auth cookies before export")
    parser.add_argument("--wait-login", action="store_true", help="Pause for manual login before exporting")
    parser.add_argument("--incremental", action="store_true", help="Only export documents missing from local Markdown")
    parser.add_argument("--update-existing", action="store_true", help="With --incremental, update existing documents too")
    add_checkpoint_args(parser)
    parser.add_argument("--doc-id", action="append", dest="selected_doc_ids", help="Export one specific wiki token, repeatable")
    parser.add_argument("--doc-id-file", default="", help="Read selected wiki tokens from a JSON array/object or line-based text file")
    parser.add_argument("--download-timeout", type=int, default=45, help="Seconds to wait for each image download")
    parser.add_argument("--progress-every", type=int, default=10, help="Print progress after N documents")
    parser.add_argument("--request-delay", type=float, default=0.8, help="Fixed seconds to wait before each document/API request")
    parser.add_argument("--request-jitter", type=float, default=0.4, help="Extra random seconds added before each document/API request")
    parser.add_argument("--keep-remote-images", action="store_true", default=True, help="Keep remote image URLs when download fails")
    parser.add_argument("--drop-failed-images", dest="keep_remote_images", action="store_false", help="Remove image URL when download fails")
    parser.add_argument("--close-started-chrome", action="store_true", help="Close Chrome started by this script after export")
    args = parser.parse_args(argv)
    extend_arg_list_from_file(args, "selected_doc_ids")
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.gui:
        print("旧版 Python GUI 已废弃，请使用 Electron 桌面端：start-wandao.cmd 或 ./start-wandao.sh", file=sys.stderr)
        return 2
    if not args.wiki_url:
        raise ExportError("--wiki-url is required")
    if not args.output:
        args.output = str((PROJECT_DIR / "exports" / "feishu").resolve())
    try:
        if args.login:
            result = login_and_save_auth(args)
        elif args.scan_toc:
            result = scan_wiki_toc(args)
        else:
            result = export_wiki(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ExportStopped as exc:
        emit(args, f"飞书导出任务已停止：{exc}", event="task.stopped", level="warn")
        print(f"Stopped: {exc}", file=sys.stderr)
        return 130
    except Exception as exc:
        emit(
            args,
            f"飞书导出任务失败：{exc}",
            event="task.failed",
            level="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
