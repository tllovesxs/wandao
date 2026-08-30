#!/usr/bin/env python3
# Author: tllovesxs
"""Export WizNote web notebooks to local Markdown files.

The exporter uses the logged-in Wiz web app through Chrome DevTools Protocol.
It keeps long-lived login state in the browser profile and does not write the
account password or Wiz token to Wandao config files.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Callable

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
    throttle_request,
    wait_for_debug_port,
)
from wandao_core.checkpoint import add_checkpoint_args, open_checkpoint_from_args
from wandao_cli import extend_arg_list_from_file
from wandao_core.credentials import write_private_json
from wandao_core.report import finalize_report


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 9233
DEFAULT_PROFILE = ".wiz-chrome-profile"
DEFAULT_AUTH_FILE = ".wiz_auth.json"
WIZ_APP_URL = "https://www.wiz.cn/xapp"
FORBIDDEN_FILENAME_CHARS = r'<>:"/\|?*'
MAX_BROWSER_IMAGE_BYTES = 25 * 1024 * 1024
WIZ_NOTE_DOWNLOAD_TIMEOUT = 12.0
WIZ_EMPTY_BODY_RETRY_TIMEOUT = 4.0
WIZ_EMPTY_BODY_RETRY_DELAY = 0.4
WIZ_OT_DOCUMENT_TIMEOUT = 6.0
WIZ_PAGE_HEALTH_TIMEOUT = 2.0
WIZ_PAGE_RECOVERY_LOGIN_TIMEOUT = 20
WIZ_IMAGE_TOTAL_TIMEOUT = 12.0
WIZ_IMAGE_PRIMARY_TIMEOUT = 8.0
WIZ_EXTERNAL_IMAGE_TIMEOUT = 8.0
WIZ_UPGRADE_PAGE_MARKERS = (
    ("当前客户端版本较低", "无法编辑协作笔记"),
    ("the current client version is too low", "edit collaborative notes"),
)


@dataclass
class WizDoc:
    kb_guid: str
    doc_guid: str
    title: str
    category: str
    note_type: str
    file_type: str
    created: int
    modified: int
    raw: dict[str, Any]


@dataclass
class WizFolder:
    kb_guid: str
    location: str
    name: str
    parent_location: str
    position: int
    note_count: int


class WizPageSessionLost(ExportError):
    """The Wiz tab is reachable through CDP but its readonly jobs no longer finish."""


class WizPageSessionUnrecoverable(ExportError):
    """A fresh readonly Wiz tab could not restore the current export."""


def default_profile_path() -> Path:
    env_profile = os.environ.get("WIZ_PROFILE_DIR")
    if env_profile:
        return Path(env_profile).expanduser().resolve()
    return default_data_dir() / DEFAULT_PROFILE


def default_auth_path() -> Path:
    return default_data_dir() / DEFAULT_AUTH_FILE


def auth_path_from_args(args: argparse.Namespace) -> Path:
    return Path(args.auth_file).resolve() if args.auth_file else default_auth_path().resolve()


def safe_name(value: str, fallback: str = "未命名", max_len: int = 90) -> str:
    text = html.unescape(str(value or "")).replace("\xa0", " ")
    cleaned = "".join("-" if ch in FORBIDDEN_FILENAME_CHARS or ord(ch) < 32 else ch for ch in text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(". ")
    return (cleaned or fallback)[:max_len]


def markdown_link_path(value: str) -> str:
    return value.replace("\\", "/").replace(" ", "%20")


def safe_resource_url(value: str) -> str:
    """Keep credentials and tracking parameters out of checkpoint records."""
    parsed = urllib.parse.urlsplit(str(value or ""))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def wiz_resource_key(doc: WizDoc, resource_type: str, source: str) -> str:
    source = str(source or "").strip()
    if not source:
        return ""
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]
    return f"wiz:doc:{doc.doc_guid}:{resource_type}:{digest}"


def js_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def redact_wiz_url(value: str) -> str:
    """Keep only the origin/path in diagnostics; discard query credentials."""
    parsed = urllib.parse.urlsplit(str(value or ""))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def note_download_diagnostic(data: dict[str, Any] | None) -> dict[str, Any]:
    payload = data if isinstance(data, dict) else {}
    metadata = payload.get("__wandaoMeta") or payload.get("__wandaoNoteMeta")
    metadata = metadata if isinstance(metadata, dict) else {}
    html_text = str(payload.get("html") or "")
    return {
        "httpStatus": int(metadata.get("httpStatus") or 0),
        "returnCode": int(payload.get("returnCode") or payload.get("return_code") or 200),
        "bodyLength": len(html_text) or int(metadata.get("bodyLength") or 0),
        "jsonBody": bool(metadata.get("jsonBody")),
        "contentType": str(metadata.get("contentType") or "")[:100],
        "hasHtml": bool(html_text),
        "upgradePage": is_wiz_upgrade_page(html_text),
        "topLevelKeys": sorted(str(key)[:80] for key in payload if key not in {"__wandaoMeta", "__wandaoNoteMeta"})[:30],
    }


def emit_wiz_diagnostic(
    args: argparse.Namespace | None,
    doc: WizDoc,
    phase: str,
    *,
    level: str = "info",
    **details: Any,
) -> None:
    emit(
        args,
        f"为知导出诊断：{doc.title}：{phase}",
        event="wiz.diagnostic",
        level=level,
        doc={"id": doc.doc_guid, "title": doc.title},
        **details,
    )


def page_for_wiz(port: int) -> dict[str, Any] | None:
    pages = http_json(f"http://127.0.0.1:{port}/json/list", timeout=5)
    for page in pages:
        url = page.get("url", "")
        if "wiz.cn" in url and page.get("type") == "page":
            return page
    return None


def open_fresh_wiz_page(port: int, initial_url: str) -> dict[str, Any]:
    """Open a new Wiz target instead of reconnecting to a stalled renderer."""
    existing_ids = {
        str(page.get("id") or "")
        for page in http_json(f"http://127.0.0.1:{port}/json/list", timeout=5)
    }
    open_tab(port, initial_url)
    deadline = time.time() + 10
    while time.time() < deadline:
        for page in http_json(f"http://127.0.0.1:{port}/json/list", timeout=5):
            if (
                page.get("type") == "page"
                and "wiz.cn" in str(page.get("url") or "")
                and str(page.get("id") or "") not in existing_ids
            ):
                return page
        time.sleep(0.2)
    raise ExportError("无法创建新的为知笔记网页标签页。")


def connect_wiz_browser(
    args: argparse.Namespace,
    initial_url: str = WIZ_APP_URL,
    *,
    force_new_page: bool = False,
) -> tuple[CDPClient, subprocess.Popen[Any] | None]:
    chrome_proc: subprocess.Popen[Any] | None = None
    if not chrome_debug_available(args.port):
        profile = Path(args.profile_dir).resolve() if args.profile_dir else default_profile_path()
        chrome_proc = start_chrome(args.port, profile, initial_url, getattr(args, "browser_path", None))
        wait_for_debug_port(args.port, timeout=30)

    page = open_fresh_wiz_page(args.port, initial_url) if force_new_page and chrome_proc is None else page_for_wiz(args.port)
    if not page:
        open_tab(args.port, initial_url)
        time.sleep(2)
        page = page_for_wiz(args.port)
    if not page:
        pages = http_json(f"http://127.0.0.1:{args.port}/json/list", timeout=5)
        page = next((item for item in pages if item.get("type") == "page"), None)
    if not page:
        raise ExportError("无法找到或创建为知笔记网页标签页。")

    cdp = CDPClient(page["webSocketDebuggerUrl"])
    cdp.connect()
    cdp.send("Runtime.enable")
    cdp.send("Page.enable")
    cdp.send("Network.enable")
    return cdp, chrome_proc


def ensure_same_wiz_account(expected: dict[str, Any], actual: dict[str, Any]) -> None:
    """Never resume on a Wiz tab that selected a different account."""
    expected_account = expected.get("account") or {}
    actual_account = actual.get("account") or {}
    for key in ("userGuid", "userId"):
        expected_value = str(expected_account.get(key) or "").strip()
        actual_value = str(actual_account.get(key) or "").strip()
        if expected_value and expected_value != actual_value:
            raise ExportError("为知浏览器恢复后的账号与任务开始时不一致，已停止任务。")


def recover_wiz_page(
    args: argparse.Namespace,
    previous_cdp: CDPClient,
    expected_snapshot: dict[str, Any],
) -> tuple[CDPClient, dict[str, Any], subprocess.Popen[Any] | None]:
    """Replace a stalled Wiz target with a fresh readonly target for the same account."""
    previous_cdp.close()
    cdp, chrome_proc = connect_wiz_browser(args, force_new_page=True)
    try:
        snapshot = wait_for_login_state(cdp, timeout=WIZ_PAGE_RECOVERY_LOGIN_TIMEOUT)
        ensure_same_wiz_account(expected_snapshot, snapshot)
    except Exception:
        cdp.close()
        if chrome_proc and getattr(args, "close_started_chrome", False):
            chrome_proc.terminate()
        raise
    return cdp, snapshot, chrome_proc


WIZ_HELPER_JS = r"""
(() => {
  if (window.__wandaoWiz && window.__wandaoWiz.version === 8) return true;
  window.__wandaoWizDocumentIndex = null;

  const reqToPromise = (req) => new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error || new Error("IndexedDB request failed"));
  });

  const openDb = async (name) => {
    const req = indexedDB.open(name);
    return await reqToPromise(req);
  };

  const getAll = async (dbName, storeName) => {
    const db = await openDb(dbName);
    try {
      return await reqToPromise(db.transaction(storeName, "readonly").objectStore(storeName).getAll());
    } finally {
      db.close();
    }
  };

  const getOne = async (dbName, storeName, key) => {
    const db = await openDb(dbName);
    try {
      return await reqToPromise(db.transaction(storeName, "readonly").objectStore(storeName).get(key));
    } finally {
      db.close();
    }
  };

  const databases = async () => {
    if (!indexedDB.databases) return [];
    return await indexedDB.databases();
  };

  const currentAccount = async () => {
    const accounts = await getAll("wiz-account", "accounts").catch(() => []);
    return accounts.find((item) => item.current) || accounts[0] || null;
  };

  const userDbName = async (account) => {
    if (account && account.userGuid) {
      const exact = `wiz-${account.userGuid}`;
      const dbs = await databases();
      if (dbs.some((item) => item.name === exact)) return exact;
    }
    const dbs = await databases();
    const found = dbs.find((item) => item.name && item.name.startsWith("wiz-") && item.name !== "wiz-account");
    return found ? found.name : "";
  };

  const safeAccount = (account) => account ? ({
    userGuid: account.userGuid || "",
    userId: account.userId || "",
    displayName: account.displayName || "",
    serverUrl: account.serverUrl || "",
    kbGuid: account.kbGuid || "",
    kbServer: account.kbServer || "",
    hasToken: Boolean(account.token),
  }) : null;

  const snapshot = async () => {
    const account = await currentAccount();
    const dbName = await userDbName(account);
    const result = {
      account: safeAccount(account),
      dbName,
      folders: [],
      docs: [],
      kbs: [],
    };
    if (!dbName) return result;
    result.folders = (await getAll(dbName, "folders").catch(() => [])).map((item) => ({
      kbGuid: item.kbGuid || "",
      location: item.location || "",
      name: item.name || "",
      parentLocation: item.parentLocation || "",
      position: Number(item.position || 0),
      noteCount: Number(item.noteCount || 0),
    }));
    result.docs = (await getAll(dbName, "docs").catch(() => [])).map((item) => ({
      kbGuid: item.kbGuid || "",
      docGuid: item.docGuid || "",
      title: item.title || "",
      category: item.category || "",
      type: item.type || "",
      fileType: item.fileType || "",
      created: Number(item.created || 0),
      dataModified: Number(item.dataModified || item.modified || 0),
      attachmentCount: Number(item.attachmentCount || 0),
      abstractText: item.abstractText || "",
      dataSize: Number(item.dataSize || 0),
    }));
    result.kbs = (await getAll(dbName, "kbs").catch(() => [])).map((item) => ({
      kbGuid: item.kbGuid || "",
      kbServer: item.kbServer || "",
      name: item.name || "",
      type: item.type || "",
      noteCount: Number(item.noteCount || 0),
      isKbOwner: Boolean(item.isKbOwner),
    }));
    return result;
  };

  const health = async () => {
    const account = await currentAccount();
    const dbName = await userDbName(account);
    if (!account || !dbName) throw new Error("为知本地会话不可用");
    await getOne(dbName, "docs", "__wandao_health_probe__");
    return { userGuid: account.userGuid || "", userId: account.userId || "" };
  };

  const tokenHeaders = async () => {
    const account = await currentAccount();
    if (!account || !account.token) throw new Error("为知登录 token 不可用，请重新登录。");
    return { "x-wiz-token": account.token };
  };

  const noteDownload = async (kbGuid, docGuid) => {
    const account = await currentAccount();
    if (!account || !account.token) throw new Error("为知登录 token 不可用，请重新登录。");
    const kbServer = account.kbServer || "";
    const url = `${kbServer}/ks/note/download/${encodeURIComponent(kbGuid)}/${encodeURIComponent(docGuid)}?downloadInfo=1&downloadData=1`;
    const response = await fetch(url, { headers: { "x-wiz-token": account.token }, credentials: "include" });
    const text = await response.text();
    const meta = {
      httpStatus: response.status,
      contentType: response.headers.get("content-type") || "",
      bodyLength: text.length,
    };
    let data = null;
    try { data = JSON.parse(text); } catch (_error) {}
    if (!response.ok) {
      throw new Error(`HTTP ${response.status} (${meta.contentType || "unknown"}, ${meta.bodyLength} bytes)`);
    }
    if (data && typeof data === "object" && !Array.isArray(data)) {
      return { ...data, __wandaoNoteMeta: meta };
    }
    return { html: text, __wandaoNoteMeta: meta };
  };

  const otDoc = async (kbGuid, docGuid) => {
    const key = `${kbGuid}:${docGuid}`;
    const row = await getOne("wiz-editor-ot", "docs", key).catch(() => null);
    if (!row || !row.data) return null;
    const text = new TextDecoder("utf-8").decode(row.data);
    return { id: key, ver: row.ver || 0, syncVer: row.syncVer || 0, text };
  };

  const resourceCache = async (name) => {
    const hash = String(name || "").replace(/\.[a-z0-9]+$/i, "");
    if (!hash) return null;
    const row = await getOne("wiz-editor-ot-res", "cache", hash).catch(() => null);
    if (!row || !row.data) return null;
    const bytes = new Uint8Array(row.data);
    let binary = "";
    for (let index = 0; index < bytes.length; index += 1) {
      binary += String.fromCharCode(bytes[index]);
    }
    return {
      base64: btoa(binary),
      contentType: row.contentType || "application/octet-stream",
    };
  };

  const fetchBase64 = async (url, timeoutMs = 8000) => {
    const headers = await tokenHeaders().catch(() => ({}));
    const deadline = Date.now() + Math.max(500, Number(timeoutMs) || 8000);
    const fetchBody = async (requestHeaders) => {
      const controller = new AbortController();
      const remaining = Math.max(1, deadline - Date.now());
      const timer = window.setTimeout(() => controller.abort(), remaining);
      try {
        const response = await fetch(url, { headers: requestHeaders, credentials: "include", signal: controller.signal });
        if (!response.ok) throw new Error(`HTTP ${response.status}: ${url}`);
        return { response, buffer: await response.arrayBuffer() };
      } finally {
        window.clearTimeout(timer);
      }
    };
    let result;
    let firstError = null;
    try { result = await fetchBody(headers); } catch (error) { firstError = error; }
    if (!result && Date.now() < deadline) {
      try { result = await fetchBody({}); } catch (error) { throw firstError || error; }
    }
    if (!result) throw firstError || new Error(`图片下载超时: ${url}`);
    const { response, buffer } = result;
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let index = 0; index < bytes.length; index += 1) {
      binary += String.fromCharCode(bytes[index]);
    }
    return {
      base64: btoa(binary),
      contentType: response.headers.get("content-type") || "application/octet-stream",
      finalUrl: response.url || url,
    };
  };

  const normalizeTitle = (value) => String(value || "").replace(/\s+/g, " ").trim();

  const editorRoot = () => (
    document.querySelector(".editor-container.root-container.editor-with-title")
    || document.querySelector(".editor-container")
  );

  const editorTitle = (root) => {
    if (!root) return "";
    const title = root.querySelector("h1.title-block, .title-block");
    return normalizeTitle(title ? (title.innerText || title.textContent) : "");
  };

  const findDocumentItem = (docGuid) => {
    const wanted = `note-list-item-${docGuid}`;
    return Array.from(document.querySelectorAll("[data-drag-id]")).find(
      (candidate) => candidate.getAttribute("data-drag-id") === wanted
    );
  };

  const listScrollLayer = () => Array.from(document.querySelectorAll(".react-custom-scrollbars-layer")).find(
    (layer) => layer.querySelector(".virtual-list-container")
  );

  const domDocumentIndex = async () => {
    if (window.__wandaoWizDocumentIndex) return window.__wandaoWizDocumentIndex;
    const layer = listScrollLayer();
    if (!layer) return {};
    const result = {};
    const originalTop = layer.scrollTop;
    const step = Math.max(250, Math.floor(layer.clientHeight * 0.8));
    for (let top = 0; top <= layer.scrollHeight; top += step) {
      layer.scrollTop = top;
      await new Promise((resolve) => setTimeout(resolve, 100));
      const layerTop = layer.getBoundingClientRect().top;
      for (const item of document.querySelectorAll('[data-drag-id^="note-list-item-"]')) {
        const id = String(item.getAttribute("data-drag-id") || "").replace(/^note-list-item-/, "");
        const itemTop = item.getBoundingClientRect().top;
        if (id && Number.isFinite(itemTop)) result[id] = layer.scrollTop + itemTop - layerTop;
      }
    }
    layer.scrollTop = originalTop;
    window.__wandaoWizDocumentIndex = result;
    return result;
  };

  const clickDocument = async (docGuid) => {
    let item = findDocumentItem(docGuid);
    if (!item) {
      const layer = listScrollLayer();
      const index = await domDocumentIndex();
      const documentTop = Number(index[docGuid]);
      if (layer && Number.isFinite(documentTop)) {
        layer.scrollTop = Math.max(0, Math.min(layer.scrollHeight, documentTop - Math.floor(layer.clientHeight * 0.25)));
        const deadline = Date.now() + 5000;
        while (Date.now() < deadline) {
          item = findDocumentItem(docGuid);
          if (item) break;
          await new Promise((resolve) => setTimeout(resolve, 60));
        }
      }
    }
    if (!item) return false;
    item.click();
    return true;
  };

  const domEditorDocument = async (docGuid, expectedTitle, timeoutMs = 15000) => {
    const wantedTitle = normalizeTitle(expectedTitle);
    let root = editorRoot();
    if (!root || editorTitle(root) !== wantedTitle || !root.querySelector('[data-node-type="block"]')) {
      await clickDocument(docGuid);
    }
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      root = editorRoot();
      const title = editorTitle(root);
      if (root && title === wantedTitle && root.querySelector('[data-node-type="block"]')) {
        return {
          title,
          html: root.outerHTML,
          blockCount: root.querySelectorAll('[data-node-type="block"]').length,
        };
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    return null;
  };

  const beginImageLoad = (url, timeoutMs = 12000) => {
    const key = `wandao-image-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    window.__wandaoWizImages = window.__wandaoWizImages || {};
    const image = new Image();
    image.decoding = "async";
    const timer = window.setTimeout(() => {
      image.src = "";
      delete window.__wandaoWizImages[key];
    }, Math.max(500, Number(timeoutMs) || 12000));
    image.src = String(url || "");
    window.__wandaoWizImages[key] = { image, timer };
    return key;
  };

  const cancelImageLoad = (key) => {
    const state = window.__wandaoWizImages && window.__wandaoWizImages[key];
    if (!state) return false;
    window.clearTimeout(state.timer);
    state.image.src = "";
    delete window.__wandaoWizImages[key];
    return true;
  };

  window.__wandaoWiz = {
    version: 8,
    snapshot,
    health,
    noteDownload,
    otDoc,
    resourceCache,
    fetchBase64,
    beginImageLoad,
    cancelImageLoad,
    domDocumentIndex,
    domEditorDocument,
  };
  return true;
})()
"""


def install_helpers(cdp: CDPClient, *, timeout: float = 30) -> None:
    cdp.evaluate(WIZ_HELPER_JS, timeout=timeout)


def read_snapshot(cdp: CDPClient, *, timeout: float = 60) -> dict[str, Any]:
    deadline = time.time() + timeout
    install_helpers(cdp, timeout=timeout)
    data = cdp.evaluate("window.__wandaoWiz.snapshot()", timeout=max(0.5, deadline - time.time()))
    if not isinstance(data, dict):
        raise ExportError("读取为知笔记登录状态失败：页面没有返回有效数据。")
    account = data.get("account") or {}
    if not account.get("hasToken"):
        raise ExportError("为知笔记登录态不可用。请先点击“登录并保存凭证”，登录后再读取目录。")
    return data


def wait_for_login_state(cdp: CDPClient, timeout: int = 20) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            remaining = max(0.5, deadline - time.time())
            return read_snapshot(cdp, timeout=min(60.0, remaining))
        except Exception as exc:  # noqa: BLE001 - keep polling after login redirect.
            last_error = str(exc)
            time.sleep(min(1.0, max(0.0, deadline - time.time())))
    raise ExportError(last_error or "未检测到为知笔记登录态。")


def save_auth_state(args: argparse.Namespace, cdp: CDPClient) -> dict[str, Any]:
    data = wait_for_login_state(cdp)
    account = data.get("account") or {}
    payload = {
        "version": 1,
        "savedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "profileDir": str(Path(args.profile_dir).resolve() if args.profile_dir else default_profile_path()),
        "account": {
            "displayName": account.get("displayName") or "",
            "userId": account.get("userId") or "",
            "userGuid": account.get("userGuid") or "",
            "kbGuid": account.get("kbGuid") or "",
            "kbServer": account.get("kbServer") or "",
            "serverUrl": account.get("serverUrl") or "",
        },
    }
    auth_file = auth_path_from_args(args)
    write_private_json(auth_file, payload)
    return {
        "authFile": str(auth_file),
        "displayName": account.get("displayName") or "",
        "docCount": len(data.get("docs") or []),
        "folderCount": len(data.get("folders") or []),
    }


def load_doc_id_file(args: argparse.Namespace) -> None:
    try:
        extend_arg_list_from_file(args, "selected_doc_ids")
    except (FileNotFoundError, ValueError) as exc:
        raise ExportError(str(exc)) from exc


def folders_from_snapshot(snapshot: dict[str, Any]) -> list[WizFolder]:
    folders: list[WizFolder] = []
    for item in snapshot.get("folders") or []:
        folders.append(
            WizFolder(
                kb_guid=str(item.get("kbGuid") or ""),
                location=str(item.get("location") or ""),
                name=str(item.get("name") or ""),
                parent_location=str(item.get("parentLocation") or ""),
                position=int(item.get("position") or 0),
                note_count=int(item.get("noteCount") or 0),
            )
        )
    return folders


def docs_from_snapshot(snapshot: dict[str, Any]) -> list[WizDoc]:
    docs: list[WizDoc] = []
    for item in snapshot.get("docs") or []:
        doc_guid = str(item.get("docGuid") or "")
        kb_guid = str(item.get("kbGuid") or "")
        if not doc_guid or not kb_guid:
            continue
        docs.append(
            WizDoc(
                kb_guid=kb_guid,
                doc_guid=doc_guid,
                title=str(item.get("title") or "未命名"),
                category=str(item.get("category") or "/"),
                note_type=str(item.get("type") or ""),
                file_type=str(item.get("fileType") or ""),
                created=int(item.get("created") or 0),
                modified=int(item.get("dataModified") or 0),
                raw=dict(item),
            )
        )
    return docs


def folder_display_name(folder: WizFolder) -> str:
    if folder.name:
        return folder.name
    parts = category_parts(folder.location)
    return parts[-1] if parts else "根目录"


def toc_json(snapshot: dict[str, Any]) -> dict[str, Any]:
    folders = sorted(folders_from_snapshot(snapshot), key=lambda item: (item.kb_guid, item.location))
    docs = sorted(docs_from_snapshot(snapshot), key=lambda item: (item.category, item.title, item.doc_guid))
    kbs = {str(item.get("kbGuid") or ""): item for item in snapshot.get("kbs") or []}
    account = snapshot.get("account") or {}
    default_kb = account.get("kbGuid") or (next(iter(kbs.keys()), ""))
    root_id = f"wiz-kb:{default_kb or 'default'}"
    root_title = "个人笔记"
    if default_kb and kbs.get(default_kb, {}).get("name"):
        root_title = str(kbs[default_kb].get("name"))

    nodes: list[dict[str, Any]] = [
        {
            "nodeId": root_id,
            "exportId": "",
            "title": root_title,
            "parentNodeId": "",
            "selectable": False,
            "type": "kb",
        }
    ]
    folder_ids: dict[tuple[str, str], str] = {}
    for folder in folders:
        node_id = f"wiz-folder:{folder.kb_guid}:{folder.location}"
        parent_id = folder_ids.get((folder.kb_guid, folder.parent_location), root_id)
        folder_ids[(folder.kb_guid, folder.location)] = node_id
        nodes.append(
            {
                "nodeId": node_id,
                "exportId": "",
                "title": folder_display_name(folder),
                "parentNodeId": parent_id,
                "selectable": False,
                "type": "folder",
            }
        )
    for doc in docs:
        parent_id = folder_ids.get((doc.kb_guid, doc.category), root_id)
        nodes.append(
            {
                "nodeId": f"wiz-doc:{doc.doc_guid}",
                "exportId": doc.doc_guid,
                "title": doc.title or "未命名",
                "parentNodeId": parent_id,
                "selectable": True,
                "type": doc.note_type or "note",
            }
        )
    return {"platform": "wiz", "nodes": nodes, "docCount": len(docs), "folderCount": len(folders)}


def category_parts(category: str) -> list[str]:
    text = urllib.parse.unquote(str(category or "/")).replace("\\", "/")
    return [part for part in (safe_name(part) for part in text.strip("/").split("/")) if part]


class PathPlanner:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.used_files: dict[tuple[str, ...], set[str]] = {}

    def unique_file(self, parent_key: tuple[str, ...], title: str) -> str:
        used = self.used_files.setdefault(parent_key, set())
        base = safe_name(title, fallback="未命名")
        candidate = f"{base}.md"
        index = 2
        while candidate.lower() in used:
            candidate = f"{base} ({index}).md"
            index += 1
        used.add(candidate.lower())
        return candidate

    def markdown_path(self, doc: WizDoc) -> Path:
        parts = tuple(category_parts(doc.category))
        return self.output.joinpath(*parts, self.unique_file(parts, doc.title))


def extract_text_ops(ops: Any) -> str:
    if isinstance(ops, str):
        return ops
    if not isinstance(ops, list):
        return ""
    parts: list[str] = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        text = str(op.get("insert") or "")
        attrs = op.get("attributes") or {}
        if not text:
            continue
        if attrs.get("code"):
            text = "`" + text.replace("`", "\\`") + "`"
        if attrs.get("bold"):
            text = f"**{text}**"
        if attrs.get("italic"):
            text = f"*{text}*"
        if attrs.get("link"):
            text = f"[{text}]({attrs.get('link')})"
        parts.append(text)
    return "".join(parts).strip("\n")


def extension_from_content_type(content_type: str, fallback: str = ".bin") -> str:
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    guessed = mimetypes.guess_extension(content_type)
    if guessed == ".jpe":
        return ".jpg"
    return guessed or fallback


def extension_from_name(name: str, content_type: str = "") -> str:
    suffix = Path(PurePosixPath(urllib.parse.unquote(name or "")).name).suffix
    return suffix or extension_from_content_type(content_type)


class ResourceSaver:
    def __init__(
        self,
        cdp: CDPClient,
        doc: WizDoc,
        md_path: Path,
        kb_server: str,
        args: argparse.Namespace,
        checkpoint: Any | None = None,
        item_key: str = "",
    ) -> None:
        self.cdp = cdp
        self.doc = doc
        self.md_path = md_path
        self.kb_server = kb_server.rstrip("/")
        self.args = args
        self.asset_dir = md_path.parent / f"{md_path.stem}_assets"
        self.saved: dict[str, str] = {}
        self.image_count = 0
        self.failures: list[dict[str, str]] = []
        self.checkpoint = checkpoint
        self.item_key = item_key
        failed_hosts = getattr(args, "_wiz_unavailable_external_image_hosts", None)
        if not isinstance(failed_hosts, set):
            failed_hosts = set()
            setattr(args, "_wiz_unavailable_external_image_hosts", failed_hosts)
        self.unavailable_external_image_hosts: set[str] = failed_hosts

    def diagnostic(self, phase: str, *, level: str = "info", **details: Any) -> None:
        emit_wiz_diagnostic(self.args, self.doc, phase, level=level, **details)

    def start_image_resource(self, url: str) -> str:
        resource_key = wiz_resource_key(self.doc, "image", url)
        if self.checkpoint and resource_key:
            self.checkpoint.upsert_resource(
                self.item_key,
                resource_key,
                "image",
                safe_resource_url(url),
            )
            self.checkpoint.start_resource(resource_key)
        return resource_key

    def complete_image_resource(self, resource_key: str, relative_path: str) -> None:
        if self.checkpoint and resource_key and relative_path:
            self.checkpoint.complete_resource(
                resource_key,
                local_path=str((self.md_path.parent / relative_path).resolve()),
                target=relative_path,
            )

    def fail_image_resource(self, resource_key: str, error: Exception | str) -> None:
        if self.checkpoint and resource_key:
            self.checkpoint.fail_resource(resource_key, str(error))

    def image_deadline(self) -> float:
        check_stopped(self.args)
        return time.time() + WIZ_IMAGE_TOTAL_TIMEOUT

    def remaining_image_timeout(self, deadline: float) -> float:
        check_stopped(self.args)
        remaining = deadline - time.time()
        if remaining <= 0:
            raise ExportError("图片下载超时")
        return max(0.5, remaining)

    def is_wiz_resource_url(self, url: str) -> bool:
        target = urllib.parse.urlsplit(url)
        trusted = urllib.parse.urlsplit(self.kb_server)
        return bool(target.netloc) and target.scheme in {"http", "https"} and target.netloc.lower() == trusted.netloc.lower()

    @staticmethod
    def external_host(url: str) -> str:
        return str(urllib.parse.urlsplit(url).netloc or "").lower()

    def build_collab_url(self, src: str) -> str:
        if re.match(r"^https?://", src, re.I):
            return src
        quoted = urllib.parse.quote(src, safe="")
        return f"{self.kb_server}/editor/{self.doc.kb_guid}/{self.doc.doc_guid}/resources/{quoted}"

    def build_normal_url(self, src: str) -> str:
        value = html.unescape(src or "").strip()
        if re.match(r"^https?://", value, re.I):
            parsed = urllib.parse.urlparse(value)
            if parsed.netloc == "wiznote-desktop":
                return f"{self.kb_server}{parsed.path}"
            return value
        if value.startswith("//wiznote-desktop/"):
            parsed = urllib.parse.urlparse("http:" + value)
            return f"{self.kb_server}{parsed.path}"
        if value.startswith("/ks/"):
            return f"{self.kb_server}{value}"
        if value.startswith("index_files/"):
            return f"{self.kb_server}/ks/note/view/{self.doc.kb_guid}/{self.doc.doc_guid}/{value}"
        quoted = urllib.parse.quote(value, safe="/")
        return f"{self.kb_server}/ks/note/view/{self.doc.kb_guid}/{self.doc.doc_guid}/index_files/{quoted}"

    def fetch_base64(self, url: str, *, timeout: float = WIZ_IMAGE_PRIMARY_TIMEOUT) -> dict[str, Any]:
        throttle_request(self.args)
        check_stopped(self.args)
        timeout = max(0.5, float(timeout))
        expression = f"window.__wandaoWiz.fetchBase64({js_string(url)}, {int(timeout * 1000)})"
        return self.cdp.evaluate(expression, timeout=timeout + 1)

    def fetch_image_via_network_resource(self, url: str) -> dict[str, Any]:
        """Read an image through Chrome's network stack when page fetch is CORS-blocked."""
        if not self.cdp:
            raise ExportError("缺少浏览器连接，无法读取图片资源。")
        safe_url = redact_wiz_url(url)
        self.diagnostic("image.network_resource.started", url=safe_url)
        frame_tree = self.cdp.send("Page.getFrameTree", timeout=30).get("result") or {}
        frame_id = str((((frame_tree.get("frameTree") or {}).get("frame") or {}).get("id") or ""))
        if not frame_id:
            raise ExportError("浏览器页面缺少主框架，无法读取图片资源。")
        loaded = self.cdp.send(
            "Network.loadNetworkResource",
            {
                "frameId": frame_id,
                "url": url,
                "options": {"disableCache": False, "includeCredentials": False},
            },
            timeout=120,
        )
        resource = ((loaded.get("result") or {}).get("resource") or {})
        if not resource.get("success"):
            raise ExportError("浏览器网络资源读取失败。")
        status = int(resource.get("httpStatusCode") or resource.get("statusCode") or 0)
        if status and (status < 200 or status >= 300):
            raise ExportError(f"浏览器图片请求返回 HTTP {status}。")
        headers = resource.get("headers") or {}
        content_type = next(
            (str(value) for key, value in headers.items() if str(key).lower() == "content-type"),
            "",
        )
        if not content_type.split(";", 1)[0].strip().lower().startswith("image/"):
            raise ExportError("浏览器网络资源不是图片。")
        stream = str(resource.get("stream") or "")
        if not stream:
            raise ExportError("浏览器网络资源没有可读取的数据流。")
        raw = bytearray()
        try:
            while True:
                check_stopped(self.args)
                chunk = self.cdp.send("IO.read", {"handle": stream, "size": 65536}, timeout=120).get("result") or {}
                data = chunk.get("data")
                if not isinstance(data, str):
                    raise ExportError("浏览器网络资源返回了无效数据。")
                raw.extend(base64.b64decode(data) if chunk.get("base64Encoded") else data.encode("utf-8"))
                if len(raw) > MAX_BROWSER_IMAGE_BYTES:
                    raise ExportError(f"图片超过大小限制（{MAX_BROWSER_IMAGE_BYTES // 1024 // 1024} MB）。")
                if chunk.get("eof"):
                    break
                if not data:
                    raise ExportError("浏览器网络资源读取中断。")
        finally:
            try:
                self.cdp.send("IO.close", {"handle": stream}, timeout=30)
            except Exception:  # noqa: BLE001 - preserve the original resource failure.
                pass
        if not raw:
            raise ExportError("浏览器网络图片响应为空。")
        self.diagnostic("image.network_resource.loaded", url=safe_url, bytes=len(raw), contentType=content_type[:100])
        return {"base64": base64.b64encode(raw).decode("ascii"), "contentType": content_type}

    def fetch_image_via_browser(self, url: str) -> dict[str, Any]:
        """Read a browser-loaded image response through CDP after it finishes."""
        if not self.cdp:
            raise ExportError("缺少浏览器连接，无法读取图片响应。")
        safe_url = redact_wiz_url(url)
        self.diagnostic("image.browser_fallback.started", url=safe_url)
        self.cdp.send("Network.enable")
        self.cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
        try:
            expression = (
                "(() => {"
                "const image = new Image();"
                "window.__wandaoWizImageFallback = image;"
                f"image.src = {js_string(url)};"
                "return true;"
                "})()"
            )
            self.cdp.evaluate(expression, timeout=120)

            def is_matching_request(event: dict[str, Any]) -> bool:
                params = event.get("params") or {}
                request = params.get("request") or {}
                return str(request.get("url") or "") == url

            request_event = self.cdp.wait_for_event(
                "Network.requestWillBeSent",
                timeout=30,
                predicate=is_matching_request,
            )
            request_params = request_event.get("params") or {}
            request_id = str(request_params.get("requestId") or "")
            if not request_id:
                raise ExportError("浏览器图片请求缺少请求标识。")

            def is_matching_request_id(event: dict[str, Any]) -> bool:
                params = event.get("params") or {}
                return str(params.get("requestId") or "") == request_id

            def wait_matching_event(method: str, timeout: float) -> dict[str, Any] | None:
                try:
                    return self.cdp.wait_for_event(method, timeout=timeout, predicate=is_matching_request_id)
                except ExportError as exc:
                    if str(exc).startswith("Timed out waiting for CDP event:"):
                        return None
                    raise

            response: dict[str, Any] | None = None
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                check_stopped(self.args)
                remaining = max(0.1, min(0.5, deadline - time.monotonic()))
                if response is None:
                    event = wait_matching_event("Network.responseReceived", remaining)
                    if event:
                        params = event.get("params") or {}
                        candidate = params.get("response") or {}
                        status = int(candidate.get("status") or 0)
                        if 300 <= status < 400:
                            continue
                        if status < 200 or status >= 300:
                            raise ExportError(f"浏览器图片请求返回 HTTP {status}。")
                        headers = candidate.get("headers") or {}
                        content_type = next(
                            (str(value) for key, value in headers.items() if str(key).lower() == "content-type"),
                            "application/octet-stream",
                        )
                        media_type = content_type.split(";", 1)[0].strip().lower()
                        if media_type and media_type != "application/octet-stream" and not media_type.startswith("image/"):
                            raise ExportError("浏览器图片响应不是图片资源。")
                        response = candidate

                failed = wait_matching_event("Network.loadingFailed", remaining)
                if failed:
                    raise ExportError("浏览器图片网络加载失败。")

                finished = wait_matching_event("Network.loadingFinished", remaining)
                if not finished:
                    continue
                if response is None:
                    raise ExportError("浏览器图片加载完成，但没有可读取的响应。")
                break

            if response is None:
                raise ExportError("浏览器图片请求等待超时。")

            body_response = self.cdp.send("Network.getResponseBody", {"requestId": request_id}, timeout=120)
            result = body_response.get("result") or {}
            body = result.get("body")
            if not isinstance(body, str) or not body:
                raise ExportError("浏览器图片响应为空。")
            if result.get("base64Encoded"):
                encoded = body
                raw_size = (len(body) * 3) // 4
            else:
                raw = body.encode("utf-8")
                encoded = base64.b64encode(raw).decode("ascii")
                raw_size = len(raw)
            if raw_size > MAX_BROWSER_IMAGE_BYTES:
                raise ExportError(f"图片超过大小限制（{MAX_BROWSER_IMAGE_BYTES // 1024 // 1024} MB）。")
            headers = response.get("headers") or {}
            content_type = next(
                (str(value) for key, value in headers.items() if str(key).lower() == "content-type"),
                "application/octet-stream",
            )
            return {
                "base64": encoded,
                "contentType": content_type,
                "finalUrl": str(response.get("url") or url),
            }
        finally:
            try:
                self.cdp.evaluate("window.__wandaoWizImageFallback = null", timeout=3)
            except Exception:  # noqa: BLE001 - cleanup must not replace the image error.
                pass
            self.cdp.send("Network.setCacheDisabled", {"cacheDisabled": False})

    def fetch_image_base64(self, url: str) -> dict[str, Any]:
        safe_url = redact_wiz_url(url)
        try:
            payload = self.fetch_base64(url)
            self.diagnostic("image.fetch.success", transport="fetch", url=safe_url)
            return payload
        except Exception as first_error:  # noqa: BLE001 - try Chrome-native loading paths.
            self.diagnostic("image.fetch.failed", level="warn", transport="fetch", url=safe_url, errorType=type(first_error).__name__)
            try:
                payload = self.fetch_image_via_network_resource(url)
                self.diagnostic("image.fetch.success", transport="browser-network", url=safe_url)
                return payload
            except Exception as network_error:  # noqa: BLE001 - retain the final browser image fallback.
                self.diagnostic("image.fetch.failed", level="warn", transport="browser-network", url=safe_url, errorType=type(network_error).__name__)
                try:
                    payload = self.fetch_image_via_browser(url)
                    self.diagnostic("image.fetch.success", transport="browser-cdp", url=safe_url)
                    return payload
                except Exception as browser_error:  # noqa: BLE001 - preserve all fallback causes.
                    self.diagnostic("image.fetch.failed", level="error", transport="browser-cdp", url=safe_url, errorType=type(browser_error).__name__)
                    raise ExportError(
                        f"图片下载失败：网页 fetch={first_error}；浏览器网络={network_error}；浏览器图片加载={browser_error}"
                    ) from browser_error

    def fetch_base64_via_browser(self, url: str, *, deadline: float | None = None) -> dict[str, Any]:
        """Load an image as the logged-in browser and read its CDP response body."""
        deadline = deadline if deadline is not None else self.image_deadline()
        image_token = ""
        try:
            self.cdp.send("Network.enable", {}, timeout=self.remaining_image_timeout(deadline))
            self.cdp.send("Network.setCacheDisabled", {"cacheDisabled": True}, timeout=self.remaining_image_timeout(deadline))
            image_token = str(
                self.cdp.evaluate(
                    f"window.__wandaoWiz.beginImageLoad({js_string(url)}, {int(self.remaining_image_timeout(deadline) * 1000)})",
                    timeout=self.remaining_image_timeout(deadline),
                )
                or ""
            )
            response_event = self.cdp.wait_for_event(
                "Network.responseReceived",
                timeout=self.remaining_image_timeout(deadline),
                predicate=lambda event: (
                    str(event.get("params", {}).get("response", {}).get("url") or "") == url
                    and str(event.get("params", {}).get("type") or "").lower() in {"image", "media"}
                ),
            )
            params = response_event.get("params") or {}
            request_id = str(params.get("requestId") or "")
            response = params.get("response") or {}
            status = int(response.get("status") or 0)
            while 300 <= status < 400:
                response_event = self.cdp.wait_for_event(
                    "Network.responseReceived",
                    timeout=self.remaining_image_timeout(deadline),
                    predicate=lambda event: str(event.get("params", {}).get("requestId") or "") == request_id,
                )
                response = (response_event.get("params") or {}).get("response") or {}
                status = int(response.get("status") or 0)
            headers = response.get("headers") or {}
            content_type = str(response.get("mimeType") or next((value for key, value in headers.items() if str(key).lower() == "content-type"), ""))
            if status < 200 or status >= 300:
                raise ExportError(f"图片响应 HTTP {status}")
            if not content_type.lower().startswith("image/"):
                raise ExportError("浏览器响应不是图片")
            try:
                failed = self.cdp.wait_for_event(
                    "Network.loadingFailed",
                    timeout=0.1,
                    predicate=lambda event: str(event.get("params", {}).get("requestId") or "") == request_id,
                )
            except Exception:
                failed = None
            if failed:
                error_text = str((failed.get("params") or {}).get("errorText") or "图片加载失败")
                raise ExportError(error_text)
            try:
                self.cdp.wait_for_event(
                    "Network.loadingFinished",
                    timeout=self.remaining_image_timeout(deadline),
                    predicate=lambda event: str(event.get("params", {}).get("requestId") or "") == request_id,
                )
            except Exception:
                for event in getattr(self.cdp, "pending_events", []):
                    event_params = event.get("params") or {}
                    if event.get("method") == "Network.loadingFailed" and str(event_params.get("requestId") or "") == request_id:
                        raise ExportError(str(event_params.get("errorText") or "图片加载失败"))
                raise
            body = self.cdp.send(
                "Network.getResponseBody",
                {"requestId": request_id},
                timeout=self.remaining_image_timeout(deadline),
            ).get("result") or {}
            raw = str(body.get("body") or "")
            if not raw:
                raise ExportError("浏览器没有返回图片响应体")
            if not body.get("base64Encoded"):
                raw = base64.b64encode(raw.encode("latin-1")).decode("ascii")
            return {"base64": raw, "contentType": content_type, "finalUrl": str(response.get("url") or url)}
        finally:
            if image_token:
                try:
                    self.cdp.evaluate(f"window.__wandaoWiz.cancelImageLoad({js_string(image_token)})", timeout=1)
                except Exception:
                    pass

    def fetch_cache_base64(self, name: str, *, deadline: float | None = None) -> dict[str, Any] | None:
        deadline = deadline if deadline is not None else self.image_deadline()
        expression = f"window.__wandaoWiz.resourceCache({js_string(name)})"
        return self.cdp.evaluate(expression, timeout=self.remaining_image_timeout(deadline))

    def fetch_external_base64(self, url: str, *, timeout: float = WIZ_EXTERNAL_IMAGE_TIMEOUT) -> dict[str, Any]:
        """Read a public image without Wiz credentials or browser cookies."""
        throttle_request(self.args)
        check_stopped(self.args)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
                "Referer": self.kb_server + "/",
            },
        )
        with urllib.request.urlopen(request, timeout=max(0.5, timeout)) as response:
            status = int(getattr(response, "status", 200) or 200)
            if status < 200 or status >= 300:
                raise ExportError(f"图片响应 HTTP {status}")
            headers = getattr(response, "headers", {})
            content_type = str(headers.get("Content-Type") or headers.get("content-type") or "")
            body = response.read()
        check_stopped(self.args)
        if not body:
            raise ExportError("HTTP 图片响应体为空")
        return {
            "base64": base64.b64encode(body).decode("ascii"),
            "contentType": content_type,
            "finalUrl": url,
        }

    def fetch_trusted_image(self, url: str, *, cache_name: str = "") -> dict[str, Any]:
        deadline = self.image_deadline()
        try:
            return self.fetch_image_base64(url)
        except (ExportStopped, WizPageSessionLost):
            raise
        except Exception as primary_error:
            raise_if_wiz_page_session_lost(self.cdp, "图片下载", primary_error)
        try:
            return self.fetch_base64_via_browser(url, deadline=deadline)
        except (ExportStopped, WizPageSessionLost):
            raise
        except Exception as browser_error:
            raise_if_wiz_page_session_lost(self.cdp, "图片浏览器兜底", browser_error)
            if cache_name:
                try:
                    cached = self.fetch_cache_base64(cache_name, deadline=deadline)
                    if cached:
                        return cached
                except (ExportStopped, WizPageSessionLost):
                    raise
                except Exception as cache_error:
                    raise_if_wiz_page_session_lost(self.cdp, "图片本地缓存", cache_error)
            raise ExportError(str(browser_error or primary_error)) from browser_error

    def save_external_image(self, key: str, url: str, name: str, alt: str = "") -> str:
        host = self.external_host(url)
        if not host or host in self.unavailable_external_image_hosts:
            return url
        resource_key = self.start_image_resource(url)
        try:
            payload = self.fetch_external_base64(url)
            relative_path = self.save_data(key, name, payload, alt)
            self.complete_image_resource(resource_key, relative_path)
            return relative_path
        except ExportStopped:
            self.fail_image_resource(resource_key, "stopped")
            raise
        except Exception as exc:
            self.fail_image_resource(resource_key, exc)
            self.unavailable_external_image_hosts.add(host)
            self.failures.append({"url": url, "error": f"外部图片主机不可用：{exc}"})
            return url

    def save_data(self, key: str, name: str, payload: dict[str, Any], alt: str = "") -> str:
        if key in self.saved:
            return self.saved[key]
        content_type = str(payload.get("contentType") or "")
        data = base64.b64decode(str(payload.get("base64") or ""))
        if not data:
            return ""
        self.image_count += 1
        ext = extension_from_name(name, content_type)
        base = safe_name(Path(PurePosixPath(urllib.parse.unquote(name or "")).name).stem or alt or f"image{self.image_count:03d}")
        filename = f"{self.image_count:03d}-{base}{ext}"
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        target = self.asset_dir / filename
        target.write_bytes(data)
        rel = os.path.relpath(target, self.md_path.parent).replace("\\", "/")
        self.saved[key] = rel
        return rel

    def save_collab_image(self, src: str, file_name: str = "", alt: str = "") -> str:
        key = f"collab:{src}"
        if key in self.saved:
            return self.saved[key]
        url = self.build_collab_url(src)
        if not self.is_wiz_resource_url(url):
            return self.save_external_image(key, url, file_name or src, alt)
        resource_key = self.start_image_resource(url)
        try:
            payload = self.fetch_trusted_image(url, cache_name=src)
        except ExportStopped:
            raise
        except WizPageSessionLost:
            self.fail_image_resource(resource_key, "Wiz page session lost")
            raise
        except Exception as exc:
            self.fail_image_resource(resource_key, exc)
            self.failures.append({"url": url, "error": str(exc)})
            return url
        relative_path = self.save_data(key, file_name or src, payload, alt)
        self.complete_image_resource(resource_key, relative_path)
        return relative_path

    def save_normal_image(self, src: str, alt: str = "") -> str:
        key = f"normal:{src}"
        if key in self.saved:
            return self.saved[key]
        if src.startswith("data:"):
            match = re.match(r"data:([^;,]+).*?;base64,(.*)$", src, re.I | re.S)
            if not match:
                return ""
            payload = {"contentType": match.group(1), "base64": match.group(2)}
            return self.save_data(key, alt or f"image{self.image_count + 1:03d}", payload, alt)
        url = self.build_normal_url(src)
        if not self.is_wiz_resource_url(url):
            return self.save_external_image(key, url, Path(PurePosixPath(urllib.parse.urlparse(url).path).name).name or src, alt)
        resource_key = self.start_image_resource(url)
        try:
            payload = self.fetch_trusted_image(url)
            relative_path = self.save_data(key, Path(PurePosixPath(urllib.parse.urlparse(url).path).name).name or src, payload, alt)
            self.complete_image_resource(resource_key, relative_path)
            return relative_path
        except (ExportStopped, WizPageSessionLost):
            self.fail_image_resource(resource_key, "stopped")
            raise
        except Exception as exc:  # noqa: BLE001 - keep exporting the note body.
            self.fail_image_resource(resource_key, exc)
            self.failures.append({"url": url, "error": str(exc)})
            return url


def blocks_to_markdown(doc: WizDoc, blocks: list[dict[str, Any]], saver: ResourceSaver) -> str:
    lines: list[str] = []
    for block in blocks:
        block_type = str(block.get("type") or "text")
        if block_type == "text":
            text = extract_text_ops(block.get("text"))
            if not text:
                continue
            heading = int(block.get("heading") or 0)
            if heading:
                level = min(max(heading, 1), 6)
                lines.append("#" * level + " " + text)
            elif block.get("quote"):
                lines.append("> " + text.replace("\n", "\n> "))
            elif block.get("checked") is not None:
                lines.append(("- [x] " if block.get("checked") else "- [ ] ") + text)
            elif block.get("list") or block.get("bullet"):
                lines.append("- " + text)
            elif block.get("ordered"):
                lines.append("1. " + text)
            else:
                lines.append(text)
        elif block_type == "embed" and block.get("embedType") == "image":
            data = block.get("embedData") or {}
            src = str(data.get("src") or "")
            if not src:
                continue
            file_name = str(data.get("fileName") or src)
            alt = safe_name(Path(PurePosixPath(file_name).name).stem, fallback="")
            rel = saver.save_collab_image(src, file_name, alt)
            if rel:
                lines.append(f"![{alt}]({markdown_link_path(rel)})")
        elif block_type == "embed":
            data = block.get("embedData") or {}
            label = str(data.get("fileName") or data.get("name") or block.get("embedType") or "附件")
            src = str(data.get("src") or "")
            if src:
                rel = saver.save_collab_image(src, label, label)
                if rel:
                    lines.append(f"[{label}]({markdown_link_path(rel)})")
            else:
                lines.append(f"> [!NOTE]\n> 未识别的嵌入内容：{label}")
        elif block_type == "code":
            text = extract_text_ops(block.get("text"))
            language = str(block.get("language") or "")
            lines.append(f"```{language}\n{text}\n```")
        else:
            text = extract_text_ops(block.get("text"))
            if text:
                lines.append(text)
    text = "\n\n".join(line for line in lines if line.strip()).strip()
    if text and not re.match(r"^#\s+", text):
        text = f"# {doc.title}\n\n{text}"
    return text + "\n" if text else f"# {doc.title}\n"


def is_explicitly_empty_note_html(value: str) -> bool:
    """Recognize Wiz's successful empty-note markup without accepting unknown content."""
    source = html.unescape(str(value or ""))
    source = re.sub(r"<!--.*?-->", "", source, flags=re.S)
    if not source.strip() or not re.search(r"<\s*(?:p|div|br)\b", source, flags=re.I):
        return False
    if re.search(r"<\s*(?:img|table|object|iframe|audio|video)\b", source, flags=re.I):
        return False
    text = re.sub(r"<[^>]*>", "", source)
    return not text.replace("\xa0", " ").strip()


class WizHtmlToMarkdown(HTMLParser):
    def __init__(self, save_image: Callable[[str, str], str]) -> None:
        super().__init__(convert_charrefs=True)
        self.save_image = save_image
        self.blocks: list[str] = []
        self.current: list[str] = []
        self.stack: list[str] = []
        self.skip_depth = 0
        self.href_stack: list[str] = []
        self.table_rows: list[list[str]] | None = None
        self.current_row: list[str] | None = None
        self.current_cell: list[str] | None = None
        self.in_pre = False

    def attrs_dict(self, attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key.lower(): value or "" for key, value in attrs}

    def flush_current(self) -> None:
        text = "".join(self.current)
        self.current = []
        if self.in_pre:
            if text.strip():
                self.blocks.append(f"```\n{text.strip()}\n```")
            return
        text = html.unescape(text).replace("\xa0", " ")
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text).strip()
        if not text:
            return
        tag = self.stack[-1] if self.stack else ""
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            text = "#" * int(tag[1]) + " " + text
        elif tag == "li":
            text = "- " + text
        elif tag == "blockquote":
            text = "> " + text.replace("\n", "\n> ")
        self.blocks.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr = self.attrs_dict(attrs)
        if tag in {"script", "style", "head", "title"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if self.table_rows is not None:
            if tag == "tr":
                self.current_row = []
            elif tag in {"td", "th"}:
                self.current_cell = []
            elif tag == "br" and self.current_cell is not None:
                self.current_cell.append("\n")
            elif tag == "img" and self.current_cell is not None:
                rel = self.save_image(attr.get("src", ""), attr.get("alt", ""))
                if rel:
                    self.current_cell.append(f"![{attr.get('alt', '')}]({markdown_link_path(rel)})")
            return
        if tag == "table":
            self.flush_current()
            self.table_rows = []
            return
        if tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre"}:
            self.flush_current()
            self.stack.append(tag)
            if tag == "pre":
                self.in_pre = True
            return
        if tag == "br":
            self.current.append("\n")
            return
        if tag == "img":
            self.flush_current()
            rel = self.save_image(attr.get("src", ""), attr.get("alt", ""))
            if rel:
                self.blocks.append(f"![{attr.get('alt', '')}]({markdown_link_path(rel)})")
            return
        if tag == "a" and attr.get("href"):
            self.href_stack.append(attr["href"])

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "head", "title"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if self.table_rows is not None:
            if tag in {"td", "th"} and self.current_cell is not None:
                cell = "".join(self.current_cell)
                cell = re.sub(r"\s+", " ", html.unescape(cell).replace("\xa0", " ")).strip()
                if self.current_row is not None:
                    self.current_row.append(cell)
                self.current_cell = None
            elif tag == "tr" and self.current_row is not None:
                if any(cell for cell in self.current_row):
                    self.table_rows.append(self.current_row)
                self.current_row = None
            elif tag == "table":
                self.render_table()
                self.table_rows = None
            return
        if tag == "a" and self.href_stack:
            self.href_stack.pop()
            return
        if tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre"}:
            self.flush_current()
            if tag == "pre":
                self.in_pre = False
            if self.stack:
                self.stack.pop()

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.table_rows is not None and self.current_cell is not None:
            self.current_cell.append(data)
            return
        if self.href_stack:
            href = self.href_stack[-1]
            self.current.append(f"[{data}]({href})")
        else:
            self.current.append(data)

    def render_table(self) -> None:
        rows = self.table_rows or []
        if not rows:
            return
        width = max(len(row) for row in rows)
        rows = [row + [""] * (width - len(row)) for row in rows]
        self.blocks.append("| " + " | ".join(rows[0]) + " |")
        self.blocks.append("| " + " | ".join(["---"] * width) + " |")
        for row in rows[1:]:
            self.blocks.append("| " + " | ".join(row) + " |")

    def result(self) -> str:
        self.flush_current()
        text = "\n\n".join(block for block in self.blocks if block.strip()).strip()
        return text + "\n" if text else ""


def get_kb_server(snapshot: dict[str, Any], doc: WizDoc) -> str:
    for kb in snapshot.get("kbs") or []:
        if kb.get("kbGuid") == doc.kb_guid and kb.get("kbServer"):
            return str(kb["kbServer"])
    account = snapshot.get("account") or {}
    return str(account.get("kbServer") or "")


def is_timeout_error(error: BaseException) -> bool:
    message = str(error).lower()
    return "timeout" in message or "timed out" in message or "超时" in message


def check_wiz_page_health(cdp: CDPClient) -> None:
    """Verify that a lightweight readonly IndexedDB call still completes."""
    deadline = time.time() + WIZ_PAGE_HEALTH_TIMEOUT
    install_helpers(cdp, timeout=WIZ_PAGE_HEALTH_TIMEOUT)
    result = cdp.evaluate("window.__wandaoWiz.health()", timeout=max(0.5, deadline - time.time()))
    if not isinstance(result, dict) or not (result.get("userGuid") or result.get("userId")):
        raise ExportError("为知网页健康检查未返回当前账号。")


def raise_if_wiz_page_session_lost(cdp: CDPClient, source: str, error: BaseException) -> None:
    if not is_timeout_error(error):
        return
    try:
        check_wiz_page_health(cdp)
    except Exception as health_error:
        raise WizPageSessionLost(
            f"为知网页会话无响应：{source} 超时后只读健康检查也失败：{health_error}"
        ) from error


def note_download_diagnostics(data: Any) -> str:
    if not isinstance(data, dict):
        return "无响应元数据"
    metadata = data.get("__wandaoNoteMeta")
    if not isinstance(metadata, dict):
        return "无响应元数据"
    status = metadata.get("httpStatus")
    content_type = str(metadata.get("contentType") or "unknown")
    body_length = metadata.get("bodyLength")
    return f"HTTP {status if status is not None else 'unknown'}, {content_type}, bodyLength={body_length if body_length is not None else 'unknown'}"


def document_metadata_hint(doc: WizDoc) -> str:
    raw = doc.raw or {}
    file_type = str(doc.file_type or raw.get("fileType") or "unknown")
    try:
        data_size = int(raw.get("dataSize") or 0)
    except (TypeError, ValueError):
        data_size = 0
    try:
        attachment_count = int(raw.get("attachmentCount") or 0)
    except (TypeError, ValueError):
        attachment_count = 0
    return f"fileType={file_type}, dataSize={data_size}, attachmentCount={attachment_count}"


def is_file_note(doc: WizDoc) -> bool:
    file_type = str(doc.file_type or doc.raw.get("fileType") or "").lower()
    title = str(doc.title or "").lower()
    return "pdf" in file_type or title.endswith(".pdf")


def fetch_ot_document(
    cdp: CDPClient,
    doc: WizDoc,
    *,
    timeout: float = WIZ_OT_DOCUMENT_TIMEOUT,
) -> dict[str, Any] | None:
    deadline = time.time() + timeout
    install_helpers(cdp, timeout=timeout)
    expression = f"window.__wandaoWiz.otDoc({js_string(doc.kb_guid)}, {js_string(doc.doc_guid)})"
    data = cdp.evaluate(expression, timeout=max(0.5, deadline - time.time()))
    if not data or not data.get("text"):
        return None
    return json.loads(data["text"])


def fetch_note_download(
    cdp: CDPClient,
    doc: WizDoc,
    *,
    timeout: float = WIZ_NOTE_DOWNLOAD_TIMEOUT,
) -> dict[str, Any] | None:
    deadline = time.time() + timeout
    install_helpers(cdp, timeout=timeout)
    expression = f"window.__wandaoWiz.noteDownload({js_string(doc.kb_guid)}, {js_string(doc.doc_guid)})"
    data = cdp.evaluate(expression, timeout=max(0.5, deadline - time.time()))
    if not isinstance(data, dict) or int(data.get("returnCode") or data.get("return_code") or 200) != 200:
        return None
    return data


class _DomEditorParser(HTMLParser):
    """Collect safe editor metadata while preserving the original DOM HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.title_depth = 0
        self.block_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set(str(attributes.get("class") or "").split())
        if tag.lower() == "h1" and "title-block" in classes:
            self.title_depth += 1
        if attributes.get("data-node-type") == "block":
            self.block_count += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "h1" and self.title_depth:
            self.title_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.title_depth:
            self.title_parts.append(data)


def _normalize_dom_title(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def extract_dom_editor_html(editor_html: str, expected_title: str) -> dict[str, Any] | None:
    """Accept a DOM fallback only when it belongs to the requested note."""
    if not editor_html or not expected_title:
        return None
    parser = _DomEditorParser()
    try:
        parser.feed(editor_html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed DOM is not exportable.
        return None
    title = _normalize_dom_title("".join(parser.title_parts))
    if title != _normalize_dom_title(expected_title) or parser.block_count <= 0:
        return None
    return {"title": title, "html": editor_html, "blockCount": parser.block_count}


def fetch_dom_editor_document(cdp: CDPClient | None, doc: WizDoc, args: argparse.Namespace) -> dict[str, Any] | None:
    """Read the rendered editor when Wiz's download endpoint serves an upgrade page."""
    if not cdp:
        return None
    install_helpers(cdp)
    expression = (
        f"window.__wandaoWiz.domEditorDocument({js_string(doc.doc_guid)}, "
        f"{js_string(doc.title)}, 15000)"
    )
    data = cdp.evaluate(expression, timeout=30)
    html_text = str(data.get("html") or "") if isinstance(data, dict) else ""
    validated = extract_dom_editor_html(html_text, doc.title)
    if not validated:
        emit_wiz_diagnostic(args, doc, "content.dom.missing", level="warn")
        return None
    emit_wiz_diagnostic(
        args,
        doc,
        "content.dom.loaded",
        blockCount=int(validated.get("blockCount") or 0),
    )
    return validated


def is_wiz_upgrade_page(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip().lower()
    return any(all(marker in normalized for marker in markers) for markers in WIZ_UPGRADE_PAGE_MARKERS)


def export_doc(
    cdp: CDPClient,
    snapshot: dict[str, Any],
    doc: WizDoc,
    md_path: Path,
    args: argparse.Namespace,
    checkpoint: Any | None = None,
    item_key: str = "",
) -> tuple[int, list[dict[str, str]]]:
    kb_server = get_kb_server(snapshot, doc)
    if not kb_server:
        raise ExportError(f"笔记 {doc.title} 缺少 kbServer，无法下载正文资源。")
    saver = ResourceSaver(cdp, doc, md_path, kb_server, args, checkpoint, item_key)
    markdown = ""
    html_text = ""
    source_errors: list[str] = []
    note_attempt_errors: list[str] = []
    ot_attempted = False
    upgrade_page = False

    def try_ot_document() -> None:
        nonlocal markdown, ot_attempted
        if ot_attempted:
            return
        ot_attempted = True
        try:
            ot_data = fetch_ot_document(cdp, doc)
        except (ExportStopped, WizPageSessionLost):
            raise
        except Exception as exc:
            source_errors.append(f"otDoc: {exc}")
            raise_if_wiz_page_session_lost(cdp, "otDoc", exc)
            return
        if ot_data and isinstance(ot_data.get("blocks"), list):
            markdown = blocks_to_markdown(doc, ot_data.get("blocks") or [], saver)
        else:
            source_errors.append("otDoc: 未返回本地正文数据")

    def try_note_download(*, timeout: float = WIZ_NOTE_DOWNLOAD_TIMEOUT) -> str:
        nonlocal markdown, html_text, upgrade_page
        try:
            downloaded = fetch_note_download(cdp, doc, timeout=timeout)
        except (ExportStopped, WizPageSessionLost):
            raise
        except Exception as exc:
            note_attempt_errors.append(f"noteDownload: {exc}")
            raise_if_wiz_page_session_lost(cdp, "noteDownload", exc)
            return "error"
        if not isinstance(downloaded, dict) or "html" not in downloaded:
            note_attempt_errors.append("noteDownload: 未返回可用正文（无响应元数据）")
            return "empty"
        emit_wiz_diagnostic(args, doc, "content.note_download.loaded", **note_download_diagnostic(downloaded))
        html_text = str(downloaded.get("html") or "")
        if is_wiz_upgrade_page(html_text):
            upgrade_page = True
            note_attempt_errors.append("noteDownload: 返回客户端升级提示")
            return "upgrade"
        if not html_text:
            # A successful, empty note is valid. A missing response is handled above.
            return "blank"
        parser = WizHtmlToMarkdown(saver.save_normal_image)
        parser.feed(html_text)
        markdown = parser.result()
        if not markdown:
            if is_explicitly_empty_note_html(html_text):
                return "blank"
            note_attempt_errors.append(f"noteDownload: 正文解析后为空（{note_download_diagnostics(downloaded)}）")
            return "empty"
        return "success"

    def try_dom_document() -> None:
        nonlocal markdown
        try:
            dom_data = fetch_dom_editor_document(cdp, doc, args)
        except (ExportStopped, WizPageSessionLost):
            raise
        except Exception as exc:  # noqa: BLE001 - retain other body fallbacks.
            source_errors.append(f"editor DOM: {exc}")
            raise_if_wiz_page_session_lost(cdp, "editor DOM", exc)
            return
        if not dom_data:
            source_errors.append("editor DOM: 未返回匹配当前笔记的正文")
            return
        parser = WizHtmlToMarkdown(saver.save_normal_image)
        parser.feed(str(dom_data.get("html") or ""))
        markdown = parser.result()
        if not markdown:
            source_errors.append("editor DOM: 正文解析后为空")

    if doc.note_type == "collaboration":
        try_ot_document()

    if not markdown:
        note_status = try_note_download()
        if note_status == "empty" and not is_file_note(doc) and not is_explicitly_empty_note_html(html_text):
            check_stopped(args)
            time.sleep(WIZ_EMPTY_BODY_RETRY_DELAY)
            note_status = try_note_download(timeout=WIZ_EMPTY_BODY_RETRY_TIMEOUT)
        if note_status == "blank":
            markdown = f"# {doc.title}\n"
        elif note_status != "success":
            source_errors.extend(note_attempt_errors)

    # Collaboration notes can still be available in the visible web editor
    # when /ks/note/download returns Wiz's client-upgrade placeholder.
    if not markdown:
        try_dom_document()

    if not markdown and not ot_attempted:
        try_ot_document()

    if not markdown and is_explicitly_empty_note_html(html_text):
        markdown = f"# {doc.title}\n"

    if not markdown:
        if is_file_note(doc):
            source_errors.append(f"文件型笔记元数据：{document_metadata_hint(doc)}")
        if upgrade_page:
            raise ExportError("为知协作笔记返回了客户端升级提示，未读取到正文；请升级为知网页客户端后重试。")
        details = "；".join(source_errors) or "没有可用的正文来源"
        raise ExportError(f"正文读取失败：{details}。")

    if not re.match(r"^#\s+", markdown.lstrip()):
        markdown = f"# {doc.title}\n\n{markdown.strip()}\n"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(markdown, encoding="utf-8")
    return saver.image_count, saver.failures


def write_index(output: Path, docs: list[WizDoc], doc_paths: dict[str, Path]) -> None:
    index_path = output / "00-知识库入口.md"
    lines = ["# 为知笔记导出", "", "> 从为知笔记导出的 Markdown 索引。", ""]
    for doc in sorted(docs, key=lambda item: (item.category, item.title, item.doc_guid)):
        path = doc_paths.get(doc.doc_guid)
        if not path:
            continue
        rel = os.path.relpath(path, index_path.parent).replace("\\", "/")
        prefix = "  " * max(0, len(category_parts(doc.category)) - 1)
        lines.append(f"{prefix}- [{doc.title}]({markdown_link_path(rel)})")
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def scan_wiz(args: argparse.Namespace) -> dict[str, Any]:
    cdp, chrome_proc = connect_wiz_browser(args)
    try:
        snapshot = wait_for_login_state(cdp, timeout=30)
        return toc_json(snapshot)
    finally:
        cdp.close()
        if chrome_proc and args.close_started_chrome:
            chrome_proc.terminate()


def select_wiz_documents(docs: list[WizDoc], selected_doc_ids: set[str] | None = None) -> list[WizDoc]:
    if not selected_doc_ids:
        return docs
    selected = [doc for doc in docs if doc.doc_guid in selected_doc_ids]
    if docs and not selected:
        preview = ", ".join(sorted(selected_doc_ids)[:5])
        raise ExportError(
            "选择的为知笔记文档未匹配当前目录，"
            "请重新读取目录后再试。未匹配 ID：" + preview
        )
    return selected


def should_skip_existing_doc(
    *,
    checkpoint_status: str | None = None,
    incremental: bool,
    path_exists: bool,
    retry_failed: bool,
) -> bool:
    """Respect explicit document retries without re-exporting completed Markdown."""
    if not incremental or not path_exists:
        return False
    if not retry_failed:
        return True
    return checkpoint_status == "completed"


def export_doc_with_page_recovery(
    args: argparse.Namespace,
    cdp: CDPClient,
    snapshot: dict[str, Any],
    doc: WizDoc,
    md_path: Path,
    started_chrome: list[subprocess.Popen[Any]],
    checkpoint: Any | None = None,
    item_key: str = "",
) -> tuple[CDPClient, dict[str, Any], int, list[dict[str, str]]]:
    """Retry one document once after replacing an unresponsive Wiz target."""
    for recovery_attempt in range(2):
        try:
            count, image_failures = export_doc(cdp, snapshot, doc, md_path, args, checkpoint, item_key)
            return cdp, snapshot, count, image_failures
        except WizPageSessionLost as exc:
            if recovery_attempt:
                raise WizPageSessionUnrecoverable(
                    f"为知网页会话恢复后仍无响应，当前笔记“{doc.title}”未完成。"
                    "为保留其余笔记的断点状态，导出已停止。"
                ) from exc
            emit(
                args,
                f"为知网页会话无响应，正在新建只读标签页后重试当前笔记：{doc.title}",
                event="browser.page.recovery",
                level="warn",
            )
            try:
                cdp, snapshot, chrome_proc = recover_wiz_page(args, cdp, snapshot)
            except ExportStopped:
                raise
            except Exception as recovery_error:
                raise WizPageSessionUnrecoverable(
                    f"为知网页会话无响应，无法恢复当前笔记“{doc.title}”：{recovery_error}"
                ) from recovery_error
            if chrome_proc:
                started_chrome.append(chrome_proc)
            emit(
                args,
                f"为知网页会话已恢复，正在重试当前笔记：{doc.title}",
                event="browser.page.recovered",
                level="warn",
            )
    raise AssertionError("unreachable")


def export_wiz(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = open_checkpoint_from_args(args, "wiz", "export")

    cdp, chrome_proc = connect_wiz_browser(args)
    started_chrome = [chrome_proc] if chrome_proc else []
    try:
        snapshot = wait_for_login_state(cdp, timeout=30)
        docs = docs_from_snapshot(snapshot)
        selected_ids = set(args.selected_doc_ids or [])
        docs = select_wiz_documents(docs, selected_ids)
        planner = PathPlanner(output)
        doc_paths = {doc.doc_guid: planner.markdown_path(doc) for doc in docs}
        if checkpoint:
            checkpoint.start_task(
                {
                    "source": WIZ_APP_URL,
                    "outputDir": str(output),
                    "totalDocs": len(docs),
                    "resume": bool(getattr(args, "resume", False)),
                    "retryFailed": bool(getattr(args, "retry_failed", False)),
                }
            )
            for doc in docs:
                checkpoint.upsert_item(
                    f"wiz:doc:{doc.doc_guid}",
                    title=doc.title,
                    source_url=doc.category,
                    source_id=doc.doc_guid,
                    parent_key=doc.kb_guid,
                    metadata={"docGuid": doc.doc_guid, "kbGuid": doc.kb_guid, "category": doc.category},
                )
            if getattr(args, "retry_failed", False):
                retry_docs: list[WizDoc] = []
                for doc in docs:
                    item_key = f"wiz:doc:{doc.doc_guid}"
                    if checkpoint.item_status(item_key) != "failed":
                        continue
                    md_path = doc_paths[doc.doc_guid]
                    if md_path.is_file():
                        # Earlier Wiz releases marked a fully written document as
                        # failed when only a remote image was unavailable.
                        checkpoint.complete_item(
                            item_key,
                            local_path=str(md_path),
                            metadata={"docGuid": doc.doc_guid, "recoveredExistingMarkdown": True},
                        )
                        continue
                    retry_docs.append(doc)
                docs = retry_docs

        exported = 0
        skipped = 0
        image_success = 0
        failures: list[dict[str, str]] = []
        image_failures: list[dict[str, str]] = []
        total = len(docs)
        emit(
            args,
            f"开始导出为知笔记：共 {total} 篇。",
            event="task.started",
            totals={"documents": total},
            output=str(output),
        )

        for index, doc in enumerate(docs, start=1):
            md_path = doc_paths[doc.doc_guid]
            item_key = f"wiz:doc:{doc.doc_guid}"
            try:
                checkpoint_status = checkpoint.item_status(item_key) if checkpoint else None
                if checkpoint and getattr(args, "resume", False) and checkpoint_status == "completed":
                    skipped += 1
                    continue
                if should_skip_existing_doc(
                    checkpoint_status=checkpoint_status,
                    incremental=bool(args.incremental),
                    path_exists=md_path.exists(),
                    retry_failed=bool(getattr(args, "retry_failed", False)),
                ):
                    if checkpoint:
                        checkpoint.complete_item(item_key, local_path=str(md_path), metadata={"docGuid": doc.doc_guid, "skippedExisting": True})
                    skipped += 1
                else:
                    if checkpoint:
                        checkpoint.start_item(item_key, "content")
                    emit(
                        args,
                        f"开始导出为知笔记：{doc.title}",
                        event="document.export.started",
                        doc={"id": doc.doc_guid, "title": doc.title, "index": index, "path": str(md_path)},
                    )
                    cdp, snapshot, count, img_failures = export_doc_with_page_recovery(
                        args,
                        cdp,
                        snapshot,
                        doc,
                        md_path,
                        started_chrome,
                        checkpoint,
                        item_key,
                    )
                    image_success += count
                    image_failures.extend({"docGuid": doc.doc_guid, "title": doc.title, **item} for item in img_failures)
                    for failure in img_failures:
                        emit(
                            args,
                            f"为知笔记图片下载失败：{doc.title}：{failure.get('error') or failure.get('url') or ''}",
                            event="resource.download.failed",
                            level="error",
                            doc={"id": doc.doc_guid, "title": doc.title, "index": index, "path": str(md_path)},
                            resource={"type": "image", "url": failure.get("url", "")},
                            error={"message": failure.get("error", "")},
                        )
                    exported += 1
                    if checkpoint:
                        checkpoint.complete_item(
                            item_key,
                            local_path=str(md_path),
                            metadata={"docGuid": doc.doc_guid, "imageFailureCount": len(img_failures)},
                        )
                    emit(
                        args,
                        f"为知笔记导出完成：{doc.title}",
                        event="document.export.completed",
                        doc={"id": doc.doc_guid, "title": doc.title, "index": index, "path": str(md_path)},
                        stats={"imageSuccessInDoc": count, "imageFailuresInDoc": len(img_failures)},
                    )
            except ExportStopped:
                if checkpoint:
                    checkpoint.fail_item(item_key, "stopped")
                raise
            except WizPageSessionUnrecoverable as exc:
                if checkpoint:
                    checkpoint.fail_item(item_key, str(exc))
                    checkpoint.fail_task(str(exc), status="failed")
                failures.append({"docGuid": doc.doc_guid, "title": doc.title, "error": str(exc)})
                emit(
                    args,
                    f"为知笔记导出失败：{doc.title}：{exc}",
                    event="document.export.failed",
                    level="error",
                    doc={"id": doc.doc_guid, "title": doc.title, "index": index, "path": str(md_path)},
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
                raise
            except Exception as exc:  # noqa: BLE001 - keep exporting other docs.
                if checkpoint:
                    checkpoint.fail_item(item_key, str(exc))
                failures.append({"docGuid": doc.doc_guid, "title": doc.title, "error": str(exc)})
                emit(
                    args,
                    f"为知笔记导出失败：{doc.title}：{exc}",
                    event="document.export.failed",
                    level="error",
                    doc={"id": doc.doc_guid, "title": doc.title, "index": index, "path": str(md_path)},
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
            if index % max(1, args.progress_every) == 0 or index == total:
                emit(
                    args,
                    f"progress {index}/{total} exported={exported} skipped={skipped} image_success={image_success} failures={len(failures)}",
                    event="task.progress",
                    progress={"current": index, "total": total},
                    stats={
                        "exportedDocs": exported,
                        "skippedDocs": skipped,
                        "imageSuccess": image_success,
                        "failureCount": len(failures),
                        "imageFailureCount": len(image_failures),
                    },
                )

        write_index(output, docs, doc_paths)
        report = {
            "platform": "wiz",
            "output": str(output),
            "total": total,
            "exported": exported,
            "skipped": skipped,
            "imageSuccess": image_success,
            "imageFailures": image_failures,
            "failures": failures,
            "elapsedSeconds": round(time.time() - started, 2),
        }
        if checkpoint:
            report["checkpoint"] = checkpoint.stats()
        report_path = output / "00-导出报告.json"
        report = finalize_report(report, provider="wiz", mode="export", report_file=report_path, output=output)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if checkpoint:
            if failures:
                checkpoint.fail_task(
                    f"{len(failures)} 个文档失败，{len(image_failures)} 个图片失败",
                    status="failed",
                )
            else:
                checkpoint.complete_task(report)
        emit(
            args,
            "为知笔记导出完成" if not failures else f"为知笔记导出完成，但有 {len(failures)} 个失败项",
            event="task.completed",
            level="success" if not failures and not image_failures else "warn",
            reportFile=str(report_path),
            stats={
                "exportedDocs": exported,
                "skippedDocs": skipped,
                "imageSuccess": image_success,
                "failureCount": len(failures),
                "imageFailureCount": len(image_failures),
            },
        )
        return report
    finally:
        cdp.close()
        if args.close_started_chrome:
            for proc in started_chrome:
                proc.terminate()
        if checkpoint:
            checkpoint.close()


def run_login(args: argparse.Namespace) -> dict[str, Any]:
    cdp, chrome_proc = connect_wiz_browser(args, WIZ_APP_URL)
    try:
        cdp.navigate(WIZ_APP_URL)
        emit(args, "请在浏览器中完成为知笔记登录，并等待左侧目录加载完成。")
        input()
        return save_auth_state(args, cdp)
    finally:
        cdp.close()
        if chrome_proc and args.close_started_chrome:
            chrome_proc.terminate()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出为知笔记为 Markdown")
    parser.add_argument("--gui", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--login", action="store_true", help="打开为知网页版并保存登录状态摘要")
    parser.add_argument("--scan-toc", action="store_true", help="读取为知目录并输出 JSON")
    parser.add_argument("--output", default=str(default_data_dir() / "exports" / "wiz"), help="输出目录")
    parser.add_argument("--doc-id", action="append", dest="selected_doc_ids", default=[], help="只导出指定笔记 ID，可重复")
    parser.add_argument("--doc-id-file", default="", help="从文件读取要导出的笔记 ID，JSON 数组或逐行文本均可")
    parser.add_argument("--incremental", action="store_true", help="目标 Markdown 已存在时跳过")
    add_checkpoint_args(parser)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Chrome 调试端口")
    parser.add_argument("--profile-dir", default=str(default_profile_path()), help="浏览器配置目录")
    parser.add_argument("--browser-path", default="", help="Chrome/Edge 可执行文件路径")
    parser.add_argument("--auth-file", default=str(default_auth_path()), help="登录状态摘要文件")
    parser.add_argument("--progress-every", type=int, default=1, help="每处理多少篇输出一次进度")
    parser.add_argument("--request-delay", type=float, default=0.0, help="资源请求延迟秒")
    parser.add_argument("--request-jitter", type=float, default=0.0, help="资源请求随机浮动秒")
    parser.add_argument("--close-started-chrome", action="store_true", help="任务结束后关闭本工具启动的浏览器")
    args = parser.parse_args(argv)
    load_doc_id_file(args)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.login:
            print(json.dumps(run_login(args), ensure_ascii=False, indent=2))
            return 0
        if args.scan_toc:
            print(json.dumps(scan_wiz(args), ensure_ascii=False, indent=2))
            return 0
        result = export_wiz(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not result.get("failures") else 1
    except ExportStopped as exc:
        emit(args, f"为知笔记导出已停止：{exc}", event="task.stopped", level="warn")
        print(str(exc), file=sys.stderr, flush=True)
        return 130
    except ExportError as exc:
        emit(
            args,
            f"为知笔记导出失败：{exc}",
            event="task.failed",
            level="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    except Exception as exc:  # noqa: BLE001
        emit(
            args,
            f"为知笔记导出失败：{exc}",
            event="task.failed",
            level="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        print(f"为知笔记导出失败：{exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
