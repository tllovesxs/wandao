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
import html
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from wandao_core.browser import (
    CDPClient,
    ExportError,
    ExportStopped,
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


def js_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def page_for_wiz(port: int) -> dict[str, Any] | None:
    pages = http_json(f"http://127.0.0.1:{port}/json/list", timeout=5)
    for page in pages:
        url = page.get("url", "")
        if "wiz.cn" in url and page.get("type") == "page":
            return page
    return None


def connect_wiz_browser(args: argparse.Namespace, initial_url: str = WIZ_APP_URL) -> tuple[CDPClient, subprocess.Popen[Any] | None]:
    chrome_proc: subprocess.Popen[Any] | None = None
    if not chrome_debug_available(args.port):
        profile = Path(args.profile_dir).resolve() if args.profile_dir else default_profile_path()
        chrome_proc = start_chrome(args.port, profile, initial_url, getattr(args, "browser_path", None))
        wait_for_debug_port(args.port, timeout=30)

    page = page_for_wiz(args.port)
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


WIZ_HELPER_JS = r"""
(() => {
  if (window.__wandaoWiz && window.__wandaoWiz.version === 1) return true;

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
    let data = null;
    try { data = JSON.parse(text); } catch (_error) {}
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${text.slice(0, 200)}`);
    }
    return data || { html: text };
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

  const fetchBase64 = async (url) => {
    const headers = await tokenHeaders().catch(() => ({}));
    let response = null;
    let firstError = null;
    try {
      response = await fetch(url, { headers, credentials: "include" });
    } catch (error) {
      firstError = error;
    }
    if (!response || !response.ok) {
      try {
        response = await fetch(url, { credentials: "include" });
      } catch (error) {
        throw firstError || error;
      }
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}: ${url}`);
    const buffer = await response.arrayBuffer();
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

  window.__wandaoWiz = {
    version: 1,
    snapshot,
    noteDownload,
    otDoc,
    resourceCache,
    fetchBase64,
  };
  return true;
})()
"""


def install_helpers(cdp: CDPClient) -> None:
    cdp.evaluate(WIZ_HELPER_JS, timeout=30)


def read_snapshot(cdp: CDPClient) -> dict[str, Any]:
    install_helpers(cdp)
    data = cdp.evaluate("window.__wandaoWiz.snapshot()", timeout=60)
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
            return read_snapshot(cdp)
        except Exception as exc:  # noqa: BLE001 - keep polling after login redirect.
            last_error = str(exc)
            time.sleep(1)
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
    def __init__(self, cdp: CDPClient, doc: WizDoc, md_path: Path, kb_server: str, args: argparse.Namespace) -> None:
        self.cdp = cdp
        self.doc = doc
        self.md_path = md_path
        self.kb_server = kb_server.rstrip("/")
        self.args = args
        self.asset_dir = md_path.parent / f"{md_path.stem}_assets"
        self.saved: dict[str, str] = {}
        self.image_count = 0
        self.failures: list[dict[str, str]] = []

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

    def fetch_base64(self, url: str) -> dict[str, Any]:
        throttle_request(self.args)
        expression = f"window.__wandaoWiz.fetchBase64({js_string(url)})"
        return self.cdp.evaluate(expression, timeout=120)

    def fetch_cache_base64(self, name: str) -> dict[str, Any] | None:
        expression = f"window.__wandaoWiz.resourceCache({js_string(name)})"
        return self.cdp.evaluate(expression, timeout=60)

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
        try:
            payload = self.fetch_base64(url)
        except Exception:
            payload = self.fetch_cache_base64(src)
        if not payload:
            self.failures.append({"url": url, "error": "图片下载失败"})
            return ""
        return self.save_data(key, file_name or src, payload, alt)

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
        try:
            payload = self.fetch_base64(url)
            return self.save_data(key, Path(PurePosixPath(urllib.parse.urlparse(url).path).name).name or src, payload, alt)
        except Exception as exc:  # noqa: BLE001 - keep exporting the note body.
            self.failures.append({"url": url, "error": str(exc)})
            return ""


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


def fetch_ot_document(cdp: CDPClient, doc: WizDoc) -> dict[str, Any] | None:
    install_helpers(cdp)
    expression = f"window.__wandaoWiz.otDoc({js_string(doc.kb_guid)}, {js_string(doc.doc_guid)})"
    data = cdp.evaluate(expression, timeout=60)
    if not data or not data.get("text"):
        return None
    return json.loads(data["text"])


def fetch_note_download(cdp: CDPClient, doc: WizDoc) -> dict[str, Any] | None:
    install_helpers(cdp)
    expression = f"window.__wandaoWiz.noteDownload({js_string(doc.kb_guid)}, {js_string(doc.doc_guid)})"
    data = cdp.evaluate(expression, timeout=90)
    if not isinstance(data, dict) or int(data.get("returnCode") or data.get("return_code") or 200) != 200:
        return None
    return data


def export_doc(cdp: CDPClient, snapshot: dict[str, Any], doc: WizDoc, md_path: Path, args: argparse.Namespace) -> tuple[int, list[dict[str, str]]]:
    kb_server = get_kb_server(snapshot, doc)
    if not kb_server:
        raise ExportError(f"笔记 {doc.title} 缺少 kbServer，无法下载正文资源。")
    saver = ResourceSaver(cdp, doc, md_path, kb_server, args)
    markdown = ""

    if doc.note_type == "collaboration":
        ot_data = fetch_ot_document(cdp, doc)
        if ot_data and isinstance(ot_data.get("blocks"), list):
            markdown = blocks_to_markdown(doc, ot_data.get("blocks") or [], saver)

    if not markdown:
        downloaded = fetch_note_download(cdp, doc)
        html_text = str((downloaded or {}).get("html") or "")
        if html_text:
            parser = WizHtmlToMarkdown(saver.save_normal_image)
            parser.feed(html_text)
            markdown = parser.result()

    if not markdown:
        ot_data = fetch_ot_document(cdp, doc)
        if ot_data and isinstance(ot_data.get("blocks"), list):
            markdown = blocks_to_markdown(doc, ot_data.get("blocks") or [], saver)

    if not markdown:
        raise ExportError("未能读取正文，可能是笔记尚未同步或登录态已失效。")

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


def export_wiz(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = open_checkpoint_from_args(args, "wiz", "export")

    cdp, chrome_proc = connect_wiz_browser(args)
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
                docs = [doc for doc in docs if checkpoint.item_status(f"wiz:doc:{doc.doc_guid}") == "failed"]

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
                if checkpoint and getattr(args, "resume", False) and checkpoint.item_status(item_key) == "completed":
                    skipped += 1
                    continue
                if args.incremental and md_path.exists():
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
                    count, img_failures = export_doc(cdp, snapshot, doc, md_path, args)
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
                        if img_failures:
                            checkpoint.fail_item(item_key, f"{len(img_failures)} 个图片下载失败")
                        else:
                            checkpoint.complete_item(item_key, local_path=str(md_path), metadata={"docGuid": doc.doc_guid})
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
            if failures or image_failures:
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
        if chrome_proc and args.close_started_chrome:
            chrome_proc.terminate()
        if checkpoint:
            checkpoint.close()


def run_login(args: argparse.Namespace) -> dict[str, Any]:
    cdp, chrome_proc = connect_wiz_browser(args, WIZ_APP_URL)
    try:
        cdp.navigate(WIZ_APP_URL)
        emit(args, "请在浏览器中完成为知笔记登录，并等待左侧目录加载完成。")
        input("Press Enter after WizNote is logged in and visible...")
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
