#!/usr/bin/env python3
# Author: tllovesxs
"""
Standalone Feishu Wiki exporter.

GUI:
  python export_feishu.py --gui

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

from export_aliyun_thoughts import (
    CDPClient,
    DEFAULT_PORT,
    ExportError,
    ExportStopped,
    check_stopped,
    chrome_debug_available,
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


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR
DEFAULT_PROFILE = ".feishu-chrome-profile"
DEFAULT_AUTH_FILE = ".feishu_auth.json"
DEFAULT_WIKI_URL = ""


def default_auth_path() -> Path:
    return PROJECT_DIR / DEFAULT_AUTH_FILE


def default_profile_path() -> Path:
    env_profile = os.environ.get("FEISHU_PROFILE_DIR")
    if env_profile:
        return Path(env_profile).expanduser().resolve()
    return PROJECT_DIR / DEFAULT_PROFILE


def auth_path_from_args(args: argparse.Namespace) -> Path:
    return Path(args.auth_file).resolve() if args.auth_file else default_auth_path().resolve()


def start_chrome(port: int, profile_dir: Path, url: str) -> subprocess.Popen[Any]:
    chrome = find_chrome()
    if not chrome:
        raise ExportError("Chrome/Edge executable was not found.")
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
    urllib.request.urlopen(f"http://127.0.0.1:{port}/json/new?{encoded}", timeout=5).close()


def normalize_wiki_url(wiki_url: str) -> str:
    parsed = urllib.parse.urlparse(wiki_url.strip())
    if not parsed.scheme or not parsed.netloc:
        raise ExportError("请填写完整的飞书 Wiki URL")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0] != "wiki":
        raise ExportError("飞书 Wiki URL 需要形如 https://xxx.feishu.cn/wiki/<wiki_token>")
    return f"{parsed.scheme}://{parsed.netloc}/wiki/{parts[1]}"


def parse_wiki_url(wiki_url: str) -> tuple[str, str, str, str]:
    normalized = normalize_wiki_url(wiki_url)
    parsed = urllib.parse.urlparse(normalized)
    token = [part for part in parsed.path.split("/") if part][1]
    return parsed.netloc, f"{parsed.scheme}://{parsed.netloc}", token, normalized


def page_for_wiki(port: int, host: str, wiki_token: str) -> dict[str, Any] | None:
    pages = http_json(f"http://127.0.0.1:{port}/json/list", timeout=5)
    token_needle = f"{host}/wiki/{wiki_token}"
    host_needle = f"{host}/wiki/"
    for page in pages:
        if page.get("type") == "page" and token_needle in page.get("url", ""):
            return page
    for page in pages:
        if page.get("type") == "page" and host_needle in page.get("url", ""):
            return page
    return None


def connect_wiki_browser(
    args: argparse.Namespace,
    wiki_url: str,
    host: str,
    wiki_token: str,
) -> tuple[CDPClient, subprocess.Popen[Any] | None]:
    chrome_proc: subprocess.Popen[Any] | None = None
    if not chrome_debug_available(args.port):
        profile = Path(args.profile_dir).resolve() if args.profile_dir else default_profile_path()
        chrome_proc = start_chrome(args.port, profile, wiki_url)
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
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    auth_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"cookieCount": len(cookies), "authFile": str(auth_file)}


def load_auth_state(cdp: CDPClient, auth_file: Path) -> int:
    if not auth_file.exists():
        raise ExportError(f"Auth file does not exist: {auth_file}")
    payload = json.loads(auth_file.read_text(encoding="utf-8"))
    cookies = [prepare_cookie_for_set(cookie) for cookie in payload.get("cookies", [])]
    cookies = [cookie for cookie in cookies if cookie.get("name") and cookie.get("value")]
    if not cookies:
        raise ExportError(f"No cookies found in auth file: {auth_file}")
    cdp.send("Network.enable")
    cdp.send("Network.setCookies", {"cookies": cookies}, timeout=30)
    return len(cookies)


def wait_for_wiki_ready(cdp: CDPClient, timeout: int, args: argparse.Namespace | None = None) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        check_stopped(args)
        ready = cdp.evaluate(
            "document.readyState === 'complete' && !!document.body && document.body.innerText.length > 20",
            timeout=10,
        )
        if ready:
            text = cdp.evaluate("(document.body && document.body.innerText || '').slice(0, 500)", timeout=10) or ""
            if "登录" not in text[:80] and "没有权限访问" not in text[:120]:
                return
        time.sleep(0.5)
    raise ExportError("飞书页面没有加载完成，可能未登录、无权限或页面结构变化")


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
    const json = await res.json();
    return assertOk(json, url);
  }

  let spaceId = "";
  try {
    const node = await getJson(`/space/api/wiki/v2/tree/get_node/?wiki_token=${encodeURIComponent(startToken)}&space_id=&expand_shortcut=true&with_deleted=true`);
    spaceId = node && node.data && node.data.space_id || "";
  } catch (_) {}
  if (!spaceId) {
    const entry = performance.getEntriesByType("resource")
      .map(e => e.name)
      .find(url => url.includes("/space/api/wiki/v2/tree/get_info/") && url.includes("space_id="));
    if (entry) spaceId = new URL(entry).searchParams.get("space_id") || "";
  }
  if (!spaceId) throw new Error("Could not detect Feishu Wiki space_id.");

  const seen = new Set();
  const nodes = {};
  const childMap = {};
  let rootList = [];
  let space = {};

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
    };
  }
  return {spaceId, space, rootList, childMap, nodes: compactNodes};
}
"""


def load_wiki_tree(
    cdp: CDPClient,
    wiki_url: str,
    start_token: str,
    args: argparse.Namespace | None = None,
) -> dict[str, Any]:
    throttle_request(args)
    cdp.navigate(wiki_url)
    wait_for_wiki_ready(cdp, timeout=30, args=args)
    value = cdp.evaluate(f"({FEISHU_TREE_LOADER_JS})({js_string(start_token)})", timeout=120)
    if not isinstance(value, dict) or not value.get("nodes"):
        raise ExportError("Failed to load Feishu Wiki tree")
    return value


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
    return ordered


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
  return {title: pageTitle, markdown, images: [...new Set(images)], blockCount: rendered.length, textLength: body.length};
}
"""


def wait_for_doc_ready(cdp: CDPClient, timeout: int, args: argparse.Namespace | None = None) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        check_stopped(args)
        ready = cdp.evaluate(
            "!!(document.querySelector('.root-render-unit-container [data-block-type]') || document.querySelector('.page-main-item.editor'))",
            timeout=8,
        )
        if ready:
            return
        time.sleep(0.5)
    raise ExportError("飞书文档正文没有加载完成")


def materialize_doc_dom(cdp: CDPClient) -> None:
    cdp.evaluate(
        "(async () => {"
        "const h = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);"
        "for (const p of [0, 0.25, 0.5, 0.75, 1, 0]) { window.scrollTo(0, Math.floor(h * p)); await new Promise(r => setTimeout(r, 160)); }"
        "})()",
        timeout=10,
    )


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
    wait_for_doc_ready(cdp, timeout=35, args=args)
    value = cdp.evaluate(f"({FEISHU_CONVERTER_JS})({js_string(node.get('title') or '未命名')})", timeout=60)
    if not isinstance(value, dict):
        raise ExportError(f"Unexpected Feishu doc response: {node.get('title')}")
    return value


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
        if not token or not item.get("url"):
            continue
        parent_token = item.get("parent_wiki_token") or "ROOT"
        if parent_token in root_parents:
            parent_token = "ROOT"
        parent_dir = ensure_container(parent_token)
        index = index_in_parent.get(token, fallback_index)
        doc_paths[token] = parent_dir / f"{pad(index)}-{sanitize_filename(item.get('title') or '未命名')}.md"
    return doc_paths


def scan_exported_docs(output: Path) -> dict[str, Path]:
    exported: dict[str, Path] = {}
    if not output.exists():
        return exported
    pattern = re.compile(r"飞书WikiToken:\s*([A-Za-z0-9]+)")
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
        rel_path = os.path.relpath(doc_path, index_path.parent).replace("\\", "/") if doc_path else ""
        label = item.get("title") or "未命名"
        lines.append(f"{indent}- [{label}]({rel_path})")
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def scan_wiki_toc(args: argparse.Namespace) -> dict[str, Any]:
    host, _origin, start_token, wiki_url = parse_wiki_url(args.wiki_url)
    cdp, chrome_proc = connect_wiki_browser(args, wiki_url, host, start_token)
    try:
        auth_file = auth_path_from_args(args)
        if auth_file.exists() and not args.skip_auth_load:
            cookie_count = load_auth_state(cdp, auth_file)
            emit(args, f"Loaded {cookie_count} auth cookies from {auth_file}")
            cdp.navigate(wiki_url)
            time.sleep(2)
        emit(args, "开始读取飞书目录。")
        tree = load_wiki_tree(cdp, wiki_url, start_token, args)
        ordered = order_tree(tree)
        return {
            "tree": tree,
            "ordered": ordered,
            "totalDocs": sum(1 for item in ordered if item.get("url")),
        }
    finally:
        cdp.close()
        if chrome_proc and args.close_started_chrome:
            chrome_proc.terminate()


def export_wiki(args: argparse.Namespace) -> dict[str, Any]:
    host, origin, start_token, wiki_url = parse_wiki_url(args.wiki_url)
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    cdp, chrome_proc = connect_wiki_browser(args, wiki_url, host, start_token)
    try:
        auth_file = auth_path_from_args(args)
        if auth_file.exists() and not args.skip_auth_load:
            cookie_count = load_auth_state(cdp, auth_file)
            emit(args, f"Loaded {cookie_count} auth cookies from {auth_file}")
            cdp.navigate(wiki_url)
            time.sleep(2)

        emit(args, "Chrome page is ready. If login is required, finish login in Chrome.")
        if args.wait_login:
            input("Press Enter after the Feishu Wiki page is logged in and visible...")

        tree = load_wiki_tree(cdp, wiki_url, start_token, args)
        ordered = order_tree(tree)
        selected_doc_ids = set(getattr(args, "selected_doc_ids", None) or [])
        docs = [item for item in ordered if item.get("url") and item.get("obj_type") == 22]
        if not docs:
            docs = [item for item in ordered if item.get("url")]
        if selected_doc_ids:
            docs = [item for item in docs if item.get("wiki_token") in selected_doc_ids]
        doc_paths = build_doc_paths(ordered, tree, output)
        existing = scan_exported_docs(output)
        for token, old_path in existing.items():
            doc_paths[token] = old_path

        exported = 0
        skipped = 0
        image_success = 0
        failures: list[dict[str, str]] = []
        image_failures: list[dict[str, Any]] = []
        stopped = False

        for index, doc in enumerate(docs, start=1):
            if stop_requested(args):
                stopped = True
                emit(args, "收到停止请求，正在结束并写入已完成的导出结果。")
                break
            token = doc["wiki_token"]
            md_path = doc_paths[token]
            if args.incremental and token in existing and not args.update_existing:
                skipped += 1
                continue
            try:
                result = fetch_doc_markdown(cdp, doc, args)
                markdown = result.get("markdown") or f"# {doc.get('title') or '未命名'}\n"
                markdown += (
                    f"\n---\n\n来源: {doc.get('url') or origin + '/wiki/' + token}\n"
                    f"飞书WikiToken: {token}\n"
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
                image_success += count
                if img_errors:
                    image_failures.append({"document": doc.get("title"), "path": str(md_path), "failures": img_errors})
                md_path.parent.mkdir(parents=True, exist_ok=True)
                md_path.write_text(markdown, encoding="utf-8")
                exported += 1
            except ExportStopped:
                stopped = True
                emit(args, "收到停止请求，当前文档未写入，正在结束。")
                break
            except Exception as exc:
                failures.append({"title": doc.get("title") or "", "wiki_token": token, "error": str(exc)})

            if index % args.progress_every == 0 or index == len(docs):
                emit(
                    args,
                    f"progress {index}/{len(docs)} exported={exported} skipped={skipped} "
                    f"image_success={image_success} failures={len(failures)}",
                )

        write_index(output, tree, ordered, doc_paths, selected_doc_ids or None)
        report = {
            "provider": "feishu",
            "mode": "incremental" if args.incremental else "full",
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
            "requestCount": int(getattr(args, "_request_count", 0) or 0),
            "requestDelaySeconds": float(getattr(args, "request_delay", 0.8) or 0),
            "requestJitterSeconds": float(getattr(args, "request_jitter", 0.4) or 0),
            "failures": failures,
            "imageFailures": image_failures,
            "exportedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        (output / "00-导出报告.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    finally:
        cdp.close()
        if chrome_proc and args.close_started_chrome:
            chrome_proc.terminate()


def login_and_save_auth(args: argparse.Namespace, wait_callback: Callable[[], None] | None = None) -> dict[str, Any]:
    host, _origin, start_token, wiki_url = parse_wiki_url(args.wiki_url)
    auth_file = auth_path_from_args(args)
    cdp, chrome_proc = connect_wiki_browser(args, wiki_url, host, start_token)
    try:
        emit(args, "Chrome opened. Log in to Feishu in the browser.")
        if wait_callback:
            wait_callback()
        else:
            input("After login is complete and the Feishu Wiki page is visible, press Enter...")
        check_stopped(args)
        cdp.navigate(wiki_url)
        time.sleep(2)
        check_stopped(args)
        result = save_auth_state(cdp, auth_file, wiki_url, host)
        emit(args, f"Saved {result['cookieCount']} auth cookies to {auth_file}")
        return result
    finally:
        cdp.close()
        if chrome_proc and args.close_started_chrome:
            chrome_proc.terminate()


def run_gui() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk

    root = tk.Tk()
    root.title("飞书知识库导出工具")
    root.geometry("980x780")

    wiki_var = tk.StringVar(value=DEFAULT_WIKI_URL)
    output_var = tk.StringVar(value=str((PROJECT_DIR / "exports" / "feishu").resolve()))
    auth_var = tk.StringVar(value=str(default_auth_path()))
    profile_var = tk.StringVar(value=str(default_profile_path()))
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
        docs = [item for item in ordered if item.get("url") and item.get("obj_type") == 22]
        if not docs:
            docs = [item for item in ordered if item.get("url")]
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

    form = tk.Frame(root, padx=14, pady=12)
    form.pack(fill="x")
    form.columnconfigure(1, weight=1)

    def row(label: str, variable: tk.StringVar, row_index: int, browse: Callable[[], None] | None = None) -> None:
        tk.Label(form, text=label, anchor="w").grid(row=row_index, column=0, sticky="w", pady=5)
        tk.Entry(form, textvariable=variable).grid(row=row_index, column=1, sticky="ew", padx=8, pady=5)
        if browse:
            tk.Button(form, text="选择", command=browse).grid(row=row_index, column=2, pady=5)

    row("飞书知识库 URL", wiki_var, 0)
    row("输出目录", output_var, 1, browse_output)
    row("凭证文件", auth_var, 2, browse_auth)
    row("浏览器配置目录", profile_var, 3, browse_profile)
    row("调试端口", port_var, 4)
    row("请求延迟秒", request_delay_var, 5)
    row("请求随机浮动秒", request_jitter_var, 6)
    tk.Checkbutton(form, text="导出后关闭本工具启动的浏览器", variable=close_chrome_var).grid(row=7, column=1, sticky="w", pady=5)

    actions = tk.Frame(root, padx=14, pady=4)
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

    toc_frame = tk.LabelFrame(root, text="目录选择", padx=10, pady=8)
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
        root,
        text="说明：先读取目录可选择导出范围；未读取目录时默认导出全部。凭证文件保存的是登录 Cookie，不保存密码。",
        anchor="w",
        padx=14,
    )
    note.pack(fill="x")

    log_text = scrolledtext.ScrolledText(root, height=12, state="disabled")
    log_text.pack(fill="both", expand=True, padx=14, pady=12)
    poll_log()
    root.mainloop()
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Feishu Wiki to Markdown.")
    parser.add_argument("--gui", action="store_true", help="Open the graphical interface")
    parser.add_argument("--login", action="store_true", help="Open browser, let you log in, then save auth cookies")
    parser.add_argument("--wiki-url", help="Feishu Wiki URL, for example https://xxx.feishu.cn/wiki/<token>")
    parser.add_argument("--output", help="Output directory")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Chrome remote debugging port")
    parser.add_argument("--profile-dir", help=f"Chrome profile dir. Omit to auto-use {default_profile_path()}")
    parser.add_argument("--auth-file", help=f"Auth cookie file. Omit to auto-use {default_auth_path()}")
    parser.add_argument("--skip-auth-load", action="store_true", help="Do not load saved auth cookies before export")
    parser.add_argument("--wait-login", action="store_true", help="Pause for manual login before exporting")
    parser.add_argument("--incremental", action="store_true", help="Only export documents missing from local Markdown")
    parser.add_argument("--update-existing", action="store_true", help="With --incremental, update existing documents too")
    parser.add_argument("--download-timeout", type=int, default=45, help="Seconds to wait for each image download")
    parser.add_argument("--progress-every", type=int, default=10, help="Print progress after N documents")
    parser.add_argument("--request-delay", type=float, default=0.8, help="Fixed seconds to wait before each document/API request")
    parser.add_argument("--request-jitter", type=float, default=0.4, help="Extra random seconds added before each document/API request")
    parser.add_argument("--keep-remote-images", action="store_true", default=True, help="Keep remote image URLs when download fails")
    parser.add_argument("--drop-failed-images", dest="keep_remote_images", action="store_false", help="Remove image URL when download fails")
    parser.add_argument("--close-started-chrome", action="store_true", help="Close Chrome started by this script after export")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.gui:
        return run_gui()
    if not args.wiki_url:
        raise ExportError("--wiki-url is required")
    if not args.output:
        args.output = str((PROJECT_DIR / "exports" / "feishu").resolve())
    try:
        if args.login:
            result = login_and_save_auth(args)
        else:
            result = export_wiki(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ExportStopped as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
