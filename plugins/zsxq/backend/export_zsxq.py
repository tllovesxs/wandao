#!/usr/bin/env python3
# Author: tllovesxs
"""
Standalone exporter for ZSXQ (知识星球) column/topic/article pages.

Desktop UI:
  Use start-wandao.cmd or ./start-wandao.sh. The old Python GUI is deprecated.

Small test:
  python export_zsxq.py \
    --entry-url "https://wx.zsxq.com/columns/<group_id>?column_id=<column_id>" \
    --output "./exports/zsxq" \
    --toc-mode toc \
    --limit 3

The exporter controls Chrome/Edge through Chrome DevTools Protocol. It follows
the left column directory, supports user-selected exports, follows ZSXQ short
links, optionally exports visible comments, and prefers articles.zsxq.com pages
when a topic page points to a full article.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
import hashlib
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
    wait_for_debug_port,
)
from wandao_core.report import finalize_report
from wandao_core.checkpoint import WandaoCheckpoint
from wandao_core.credentials import write_private_json


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR
DEFAULT_PROFILE = ".zsxq-chrome-profile"
DEFAULT_AUTH_FILE = ".zsxq_auth.json"
DEFAULT_ENTRY_URL = ""
DEFAULT_GROUP_LIMIT = 50
DEFAULT_GROUP_BATCH_SIZE = 20
MAX_GROUP_BATCH_SIZE = 20
GROUP_LONG_EXPORT_WARNING_LIMIT = 1000
DEFAULT_REQUEST_DELAY = 2.5
DEFAULT_REQUEST_JITTER = 2.5
MIN_REQUEST_DELAY = 1.0
MIN_REQUEST_JITTER = 1.0
DEFAULT_COMMENT_BATCH_SIZE = 30
DEFAULT_COMMENT_REQUEST_DELAY = 3.0
DEFAULT_COMMENT_REQUEST_JITTER = 2.0
GROUP_CURSOR_NAME = "zsxq-group"
EXPORT_SEQUENCE_RE = re.compile(r"^(?P<sequence>\d+)-")
MAX_GROUP_NEWEST_REFRESH_PAGES = 250


class SkipDocument(Exception):
    def __init__(self, reason: str, title: str = "", href: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.title = title
        self.href = href


def default_auth_path() -> Path:
    return default_state_path(DEFAULT_AUTH_FILE)


def default_profile_path() -> Path:
    env_profile = os.environ.get("ZSXQ_PROFILE_DIR")
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
        return
    except urllib.error.HTTPError as exc:
        if exc.code != 405:
            raise
    request = urllib.request.Request(endpoint, method="PUT")
    urllib.request.urlopen(request, timeout=5).close()


def normalize_entry_url(entry_url: str) -> str:
    parsed = urllib.parse.urlparse(entry_url.strip())
    if not parsed.scheme or not parsed.netloc:
        raise ExportError("请填写完整的知识星球 URL")
    host = (parsed.hostname or "").lower()
    if host != "zsxq.com" and not host.endswith(".zsxq.com"):
        raise ExportError("当前工具只支持 zsxq.com 知识星球页面")
    return entry_url.strip()


def page_for_zsxq(port: int) -> dict[str, Any] | None:
    pages = http_json(f"http://127.0.0.1:{port}/json/list", timeout=5)
    for page in pages:
        url = page.get("url", "")
        if page.get("type") == "page" and is_zsxq_page_url(url):
            return page
    for page in pages:
        if page.get("type") == "page" and page.get("webSocketDebuggerUrl"):
            return page
    return None


def connect_browser(args: argparse.Namespace, entry_url: str) -> tuple[CDPClient, subprocess.Popen[Any] | None]:
    chrome_proc: subprocess.Popen[Any] | None = None
    if not chrome_debug_available(args.port):
        profile = Path(args.profile_dir).resolve() if args.profile_dir else default_profile_path()
        chrome_proc = start_chrome(args.port, profile, entry_url, getattr(args, "browser_path", None))
        wait_for_debug_port(args.port, timeout=30)

    page = page_for_zsxq(args.port)
    if not page:
        open_tab(args.port, entry_url)
        time.sleep(4)
        page = page_for_zsxq(args.port)
    if not page:
        raise ExportError("Could not find or create a ZSXQ page in Chrome.")

    cdp = CDPClient(page["webSocketDebuggerUrl"])
    cdp.connect()
    cdp.send("Runtime.enable")
    cdp.send("Page.enable")
    return cdp, chrome_proc


def is_zsxq_cookie(cookie: dict[str, Any]) -> bool:
    domain = (cookie.get("domain") or "").lower().lstrip(".")
    return (
        domain == "zsxq.com"
        or domain.endswith(".zsxq.com")
        or domain == "zsxq.cn"
        or domain.endswith(".zsxq.cn")
        or domain.startswith("zsxq-img")
        or domain.startswith("zsxqpic")
    )


def save_auth_state(cdp: CDPClient, auth_file: Path, entry_url: str) -> dict[str, Any]:
    cdp.send("Network.enable")
    cookies = cdp.send("Network.getAllCookies", timeout=20).get("result", {}).get("cookies", [])
    cookies = [cookie for cookie in cookies if is_zsxq_cookie(cookie)]
    if not cookies:
        raise ExportError("No ZSXQ cookies found. Make sure login is complete in Chrome.")
    payload = {
        "version": 1,
        "entryUrl": entry_url,
        "savedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "cookies": cookies,
    }
    write_private_json(auth_file, payload)
    return {"cookieCount": len(cookies), "authFile": str(auth_file)}


def annotate_auth_state(auth_file: Path, account: dict[str, Any]) -> None:
    if not account:
        return
    try:
        payload = json.loads(auth_file.read_text(encoding="utf-8"))
        payload["account"] = account
        payload["verifiedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        write_private_json(auth_file, payload)
    except Exception:
        return


def load_auth_state(cdp: CDPClient, auth_file: Path) -> int:
    if not auth_file.exists():
        raise ExportError(f"知识星球登录凭证不存在：{auth_file}。请先点击“登录并保存凭证”。")
    payload = json.loads(auth_file.read_text(encoding="utf-8"))
    cookies = [prepare_cookie_for_set(cookie) for cookie in payload.get("cookies", [])]
    cookies = [cookie for cookie in cookies if cookie.get("name") and cookie.get("value")]
    if not cookies:
        raise ExportError(f"知识星球登录凭证里没有可用 Cookie：{auth_file}。请重新登录并保存凭证。")
    cdp.send("Network.enable")
    cdp.send("Network.setCookies", {"cookies": cookies}, timeout=30)
    return len(cookies)


def wait_eval(
    cdp: CDPClient,
    expression: str,
    predicate: Callable[[Any], bool],
    timeout: int,
    args: argparse.Namespace | None = None,
) -> Any:
    deadline = time.time() + timeout
    last_value: Any = None
    while time.time() < deadline:
        check_stopped(args)
        try:
            last_value = cdp.evaluate(expression, timeout=8)
            if predicate(last_value):
                return last_value
        except Exception as exc:
            last_value = {"error": str(exc)}
        time.sleep(0.5)
    return last_value


def wait_with_stop(args: argparse.Namespace | None, seconds: float) -> None:
    deadline = time.time() + max(0, seconds)
    while time.time() < deadline:
        check_stopped(args)
        time.sleep(min(1.0, deadline - time.time()))


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "--:--"
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def throttle_request(args: argparse.Namespace | None) -> None:
    if not args:
        return
    delay = max(0.0, float(getattr(args, "request_delay", 0) or 0))
    jitter = max(0.0, float(getattr(args, "request_jitter", 0) or 0))
    pause = delay + (random.uniform(0, jitter) if jitter else 0)
    if pause > 0:
        wait_with_stop(args, pause)
    args._request_count = int(getattr(args, "_request_count", 0) or 0) + 1


def throttle_comment_request(args: argparse.Namespace | None) -> None:
    if not args:
        return
    delay = max(
        float(getattr(args, "request_delay", 0) or 0),
        float(getattr(args, "comment_request_delay", 3.0) or 0),
    )
    jitter = max(
        float(getattr(args, "request_jitter", 0) or 0),
        float(getattr(args, "comment_request_jitter", 2.0) or 0),
    )
    pause = delay + (random.uniform(0, jitter) if jitter else 0)
    if pause > 0:
        wait_with_stop(args, pause)
    args._comment_request_count = int(getattr(args, "_comment_request_count", 0) or 0) + 1


def detect_rate_limited_page(cdp: CDPClient) -> dict[str, Any]:
    try:
        return cdp.evaluate(
            r"""(() => {
              const text = (document.body && document.body.innerText || "").trim();
              const title = document.title || "";
              const compact = text.replace(/\s+/g, " ").trim();
              const hasContentRoot = !!document.querySelector(".content.ql-editor, .talk-content-container, .answer-content-container, .column-topic-detail");
              const limitedPhrase = /Too Many Requests|请求过于频繁|请求太频繁|访问过于频繁/i.test(compact + "\n" + title);
              const shortError = compact.length <= 300 && !hasContentRoot;
              const exactError = /^(?:HTTP\s*)?429(?:\s+Too Many Requests)?$/i.test(compact)
                || /^Too Many Requests$/i.test(compact)
                || /^(请求过于频繁|请求太频繁|访问过于频繁)$/.test(compact)
                || /^(?:HTTP\s*)?429(?:\s+Too Many Requests)?$/i.test(title);
              const limited = exactError || (limitedPhrase && shortError);
              return {limited, href: location.href, title, hasContentRoot, textLength: compact.length, text: text.slice(0, 200)};
            })()""",
            timeout=10,
        ) or {}
    except Exception:
        return {"limited": False}


def detect_auth_required_page(cdp: CDPClient) -> dict[str, Any]:
    try:
        return cdp.evaluate(
            r"""(() => {
              const href = location.href;
              const text = (document.body && document.body.innerText || "").trim();
              const required = /\/login(?:$|\?)/.test(location.pathname)
                || /登录知识星球|获取登录二维码|切换至账号登录/.test(text);
              return {required, href, text: text.slice(0, 200)};
            })()""",
            timeout=10,
        ) or {}
    except Exception:
        return {"required": False}


def rate_limit_pause_seconds(args: argparse.Namespace | None, attempt: int) -> float:
    base = max(5.0, float(getattr(args, "rate_limit_pause", 90) if args else 90))
    return min(base * (2 ** max(0, attempt - 1)), 15 * 60)


def pause_for_rate_limit(args: argparse.Namespace | None, label: str, attempt: int, retries: int) -> None:
    if args:
        args._rate_limit_events = int(getattr(args, "_rate_limit_events", 0) or 0) + 1
    if attempt >= retries:
        raise ExportError(f"请求过于频繁，重试仍失败：{label}")
    pause = rate_limit_pause_seconds(args, attempt + 1)
    emit(args, f"触发请求频率限制，暂停 {format_duration(pause)} 后重试：{label}")
    wait_with_stop(args, pause)


def pause_between_group_pages(args: argparse.Namespace | None, page_count: int, total_topics: int) -> None:
    if not args:
        return
    delay = max(0.0, float(getattr(args, "group_page_delay", 4.0) or 0))
    jitter = max(0.0, float(getattr(args, "group_page_jitter", 4.0) or 0))
    pause = delay + (random.uniform(0, jitter) if jitter else 0)
    if pause <= 0:
        return
    emit(
        args,
        f"目录读取保护：已读取 {page_count} 页/{total_topics} 篇，暂停 {format_duration(pause)} 后继续。",
        event="task.paused",
        level="info",
        stats={"groupPage": page_count, "tocTopicCount": total_topics, "pauseSeconds": round(pause, 1)},
    )
    wait_with_stop(args, pause)


def should_long_sleep_after_export(args: argparse.Namespace, exported_count: int) -> bool:
    after = int(getattr(args, "long_sleep_after", 25) or 0)
    every = int(getattr(args, "long_sleep_every", 12) or 0)
    return after > 0 and every > 0 and exported_count > after and exported_count % every == 0


def navigate_with_retry(cdp: CDPClient, url: str, args: argparse.Namespace | None = None) -> None:
    retries = max(0, int(getattr(args, "rate_limit_retries", 5) if args else 5))
    for attempt in range(retries + 1):
        check_stopped(args)
        throttle_request(args)
        cdp.navigate(url)
        wait_with_stop(args, 1.0)
        limited = detect_rate_limited_page(cdp)
        if not limited.get("limited"):
            auth = detect_auth_required_page(cdp)
            if auth.get("required"):
                raise ExportError("知识星球需要重新登录：请先运行登录保存凭证，或在浏览器中完成登录后重试。")
            return
        pause_for_rate_limit(args, url, attempt, retries)


ZSXQ_CONVERTER_JS = r"""
(fallbackTitle, rootSelector) => {
  const images = [];
  const links = [];
  function clean(s) {
    return (s || "")
      .replace(/\u00a0/g, " ")
      .replace(/[\u200b\u200c\u200d\ufeff]/g, "")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }
  function hidden(node) {
    if (!node || node.nodeType !== 1) return false;
    if (node.hidden || node.getAttribute("aria-hidden") === "true") return true;
    const style = window.getComputedStyle ? window.getComputedStyle(node) : null;
    return !!style && (style.display === "none" || style.visibility === "hidden");
  }
  const codeLanguages = [
    "typescript", "javascript", "markdown", "plaintext", "objectivec",
    "csharp", "python", "shell", "bash", "json", "html", "java",
    "rust", "ruby", "yaml", "yml", "css", "xml", "sql", "php",
    "cpp", "go", "c", "text"
  ];
  function isCodeToolbarText(text) {
    const compacted = clean(text).replace(/\s+/g, "").toLowerCase();
    if (!compacted.endsWith("copy")) return false;
    let rest = compacted.slice(0, -4);
    if (rest.length < 12 || rest.length > 240) return false;
    let hits = 0;
    while (rest) {
      const lang = codeLanguages.find(item => rest.startsWith(item));
      if (!lang) return false;
      rest = rest.slice(lang.length);
      hits += 1;
    }
    return hits >= 3;
  }
  function stripCodeToolbarText(text) {
    const lines = (text || "").replace(/\r\n?/g, "\n").split("\n");
    return lines.filter(line => !isCodeToolbarText(line)).join("\n").replace(/\n$/, "");
  }
  function codeBlockText(el) {
    const candidates = [...el.querySelectorAll("code, textarea")]
      .map(node => node.innerText || node.textContent || "")
      .filter(text => clean(text) && !isCodeToolbarText(text));
    const text = candidates.sort((a, b) => b.length - a.length)[0] || el.innerText || el.textContent || "";
    return stripCodeToolbarText(text);
  }
  function inline(node) {
    if (!node) return "";
    if (node.nodeType === 3) return node.nodeValue.replace(/[\u200b\u200c\u200d\ufeff]/g, "");
    if (node.nodeType !== 1) return "";
    if (hidden(node)) return "";
    const tag = node.tagName.toLowerCase();
    if (tag === "br") return "\n";
    if (tag === "img") {
      const src = node.getAttribute("src") || node.getAttribute("data-src") || "";
      const alt = node.getAttribute("alt") || "image";
      if (src) images.push(src);
      return src ? `![${alt}](${src})` : "";
    }
    let inner = [...node.childNodes].map(inline).join("");
    if (tag === "a") {
      const href = node.getAttribute("href") || "";
      if (href && /(?:t|wx|articles)\.zsxq\.com/.test(href)) links.push({text: clean(inner), href});
      return href && clean(inner) ? `[${clean(inner)}](${href})` : inner;
    }
    if (tag === "strong" || tag === "b") return clean(inner) ? `**${inner}**` : inner;
    if (tag === "em" || tag === "i") return clean(inner) ? `*${inner}*` : inner;
    if (tag === "code") return "`" + inner.replace(/`/g, "\\`") + "`";
    return inner;
  }
  function table(el) {
    const rows = [...el.querySelectorAll("tr")]
      .map(tr => [...tr.children].map(td => clean(td.innerText).replace(/\|/g, "\\|").replace(/\n+/g, "<br>")))
      .filter(row => row.length);
    if (!rows.length) return "";
    const max = Math.max(...rows.map(row => row.length));
    rows.forEach(row => { while (row.length < max) row.push(""); });
    return "| " + rows[0].join(" | ") + " |\n| " + Array(max).fill("---").join(" | ") + " |\n"
      + rows.slice(1).map(row => "| " + row.join(" | ") + " |").join("\n");
  }
  function block(el) {
    if (!el || el.nodeType !== 1) return "";
    if (hidden(el)) return "";
    const tag = el.tagName.toLowerCase();
    if (["script", "style", "meta", "button"].includes(tag)) return "";
    if (isCodeToolbarText(el.innerText || el.textContent || "")) return "";
    if (el.classList && (el.classList.contains("comment-container") || el.classList.contains("comment-item"))) return "";
    if (/^h[1-6]$/.test(tag)) return "#".repeat(+tag[1] + 1) + " " + clean(inline(el));
    if (tag === "p") return clean(inline(el));
    if (tag === "pre") return "```\n" + codeBlockText(el) + "\n```";
    if (tag === "blockquote") return clean(el.innerText).split("\n").map(x => "> " + x).join("\n");
    if (tag === "ul" || tag === "ol") {
      const items = [...el.children].flatMap(child => {
        if (!child.tagName) return [];
        if (child.tagName.toLowerCase() === "li") return [child];
        return [...child.children].filter(grand => grand.tagName && grand.tagName.toLowerCase() === "li");
      });
      return items
        .map((li, i) => {
          const text = clean(inline(li));
          return text ? (tag === "ol" ? `${i + 1}. ` : "- ") + text : "";
        })
        .filter(Boolean)
        .join("\n");
    }
    if (tag === "table") return table(el);
    if (tag === "img") return inline(el);
    if (tag === "div" && el.classList && el.classList.contains("content") && !el.classList.contains("ql-editor")) {
      return clean(inline(el) || el.innerText || "");
    }
    const childBlocks = [...el.children].map(block).filter(Boolean).join("\n\n");
    if (childBlocks) return childBlocks;
    return clean(inline(el) || el.innerText || "");
  }
  const root = document.querySelector(rootSelector)
    || document.querySelector(".content.ql-editor")
    || document.querySelector(".answer-content-container")
    || document.querySelector(".talk-content-container")
    || document.querySelector("article")
    || document.querySelector("main")
    || document.body;
  const rootFirstLine = clean(root.innerText || "").split("\n").map(x => clean(x)).find(Boolean) || "";
  const pageTitle = clean(
    (document.querySelector("h1.title") || document.querySelector("h1") || {}).innerText
    || rootFirstLine
    || fallbackTitle
    || document.title.replace(/-知识星球$/, "")
  );
  const markdown = [...root.children].map(block).filter(Boolean).join("\n\n").replace(/\n{3,}/g, "\n\n").trim();
  const uniqueLinks = [];
  const seen = new Set();
  for (const link of links) {
    if (!link.href || seen.has(link.href)) continue;
    seen.add(link.href);
    uniqueLinks.push(link);
  }
  return {
    title: pageTitle,
    markdown: "# " + pageTitle + "\n\n" + markdown + "\n",
    images: [...new Set(images)],
    zsxqLinks: uniqueLinks,
    textLen: clean(root.innerText || "").length,
  };
}
"""


ZSXQ_EXPAND_CONTENT_JS = r"""
async () => {
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const clean = s => (s || "").replace(/\s+/g, " ").trim();
  const root = document.querySelector(".topic-detail, .column-topic-detail, .talk-content-container, .answer-content-container") || document.body;
  const before = clean(root.innerText || "").length;
  let clicked = 0;
  for (let round = 0; round < 3; round += 1) {
    const candidates = [...document.querySelectorAll(".showAll, button, a, span, div, p")]
      .filter(el => /展开全部|展开全文|查看全部/.test(clean(el.innerText || el.textContent || "")));
    if (!candidates.length) break;
    for (const el of candidates.slice(0, 3)) {
      try {
        el.scrollIntoView({block: "center", inline: "nearest"});
        el.click();
        clicked += 1;
        await wait(1200);
      } catch (err) {}
    }
  }
  const after = clean(root.innerText || "").length;
  return {clicked, before, after};
}
"""


ZSXQ_COMMENTS_JS = r"""
async () => {
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const clean = s => (s || "")
    .replace(/\u00a0/g, " ")
    .replace(/[\u200b\u200c\u200d\ufeff]/g, "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  const visible = el => {
    if (!el || el.nodeType !== 1 || el.hidden || el.getAttribute("aria-hidden") === "true") return false;
    const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
    return !style || (style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || "1") !== 0);
  };
  const activate = async el => {
    try {
      el.scrollIntoView({block: "center", inline: "nearest"});
      for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
        el.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
      }
      await wait(800);
    } catch (err) {}
  };
  for (let round = 0; round < 4; round += 1) {
    window.scrollTo(0, document.body.scrollHeight);
    await wait(700);
    const buttons = [...document.querySelectorAll("button, a, span, div")]
      .filter(visible)
      .filter(el => /更多评论|查看全部评论|展开.*评论|加载更多|更多回复|查看.*回复|展开.*回复/.test(clean(el.innerText || el.textContent || "")));
    if (!buttons.length) continue;
    for (const el of buttons.slice(0, 5)) {
      await activate(el);
    }
  }

  const selectors = [
    ".comment-item",
    ".reply-item",
    ".comment-list-item",
    ".topic-comment-item",
    "[class*='comment-item']",
    "[class*='CommentItem']",
    "[class*='reply-item']",
    "[class*='ReplyItem']"
  ];
  const containerSelectors = [
    ".comment-container",
    ".comments-container",
    ".comment-list",
    "[class*='comment-container']",
    "[class*='CommentContainer']",
    "[class*='comment-list']",
    "[class*='CommentList']"
  ];
  let nodes = [...document.querySelectorAll(selectors.join(","))].filter(visible);
  if (!nodes.length) {
    for (const container of [...document.querySelectorAll(containerSelectors.join(","))].filter(visible)) {
      const children = [...container.children].filter(visible);
      if (children.length) nodes.push(...children);
    }
  }
  nodes = nodes.filter((node, index, arr) => arr.findIndex(other => other === node) === index);
  nodes = nodes.filter(node => !(looksLikeContainer(node) && arrHasDescendant(node, nodes)));

  function arrHasDescendant(node, arr) {
    return arr.some(other => other !== node && node.contains(other));
  }
  function looksLikeContainer(node) {
    const cls = String(node.className || "");
    return /container|list/i.test(cls) && !/item/i.test(cls);
  }
  function lineLooksLikeUi(line) {
    return /^(回复|点赞|赞|举报|删除|取消|发送|评论|写评论|发表|收起|展开|查看更多|加载更多|全部评论|暂无评论|[0-9]+\s*赞?|[·.])$/.test(line);
  }
  function extract(node) {
    const lines = clean(node.innerText || node.textContent || "")
      .split("\n")
      .map(line => clean(line))
      .filter(Boolean)
      .filter(line => !lineLooksLikeUi(line));
    if (!lines.length) return null;
    const timeIndex = lines.findIndex(line => /20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}|昨天|今天|刚刚|\d{1,2}:\d{2}/.test(line));
    const time = timeIndex >= 0 ? lines[timeIndex] : "";
    let author = "";
    const authorEl = node.querySelector(".name, .nickname, .user-name, [class*='nickname'], [class*='Nickname'], [class*='userName'], [class*='UserName'], [class*='author'], [class*='Author']");
    if (authorEl && visible(authorEl)) author = clean(authorEl.innerText || authorEl.textContent || "").split("\n")[0] || "";
    if (!author && lines.length > 1 && lines[0].length <= 32 && timeIndex !== 0) author = lines[0];
    const textLines = lines.filter((line, index) => {
      if (author && index === 0 && line === author) return false;
      if (timeIndex >= 0 && index === timeIndex) return false;
      return true;
    });
    const text = clean(textLines.join("\n"));
    if (!text || text.length < 1 || text.length > 5000) return null;
    return {author, time, text};
  }

  const comments = [];
  const seen = new Set();
  for (const node of nodes) {
    const item = extract(node);
    if (!item) continue;
    const key = [item.author, item.time, item.text].join("\n").replace(/\s+/g, " ").slice(0, 800);
    if (seen.has(key)) continue;
    seen.add(key);
    comments.push(item);
  }
  return {
    href: location.href,
    count: comments.length,
    comments,
  };
}
"""


ZSXQ_TOC_JS = r"""
async () => {
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const clean = s => (s || "").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
  const activate = async el => {
    if (!el) return;
    el.scrollIntoView({block: "center", inline: "nearest"});
    for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      el.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
    }
    try { el.click(); } catch (err) {}
    await wait(350);
  };
  const groupId = (location.pathname.match(/\/columns\/(\d+)/) || [])[1] || "";
  const parseZsxqJson = raw => JSON.parse(raw.replace(
    /("(?:topic_id|topic_uid|group_id|user_id|uid|file_id|comment_id|column_id|image_id|video_id|owner_user_id|repliee_user_id)"\s*:\s*)(\d{15,})/g,
    '$1"$2"'
  ));
  const apiGet = async (url, retries = 2) => {
    for (let attempt = 0; attempt <= retries; attempt += 1) {
      try {
        const r = await fetch(url, {credentials: "include", headers: {accept: "application/json, text/plain, */*"}});
        const text = await r.text();
        const data = parseZsxqJson(text);
        if (data && data.succeeded) return data.resp_data || {};
      } catch (err) {}
      if (attempt < retries) await wait(900 + attempt * 1200);
    }
    return null;
  };
  const sourceOfTopic = topic => topic && (topic.task || topic.talk || topic.question || topic.solution || topic.answer || {}) || {};
  const titleOfTopic = topic => {
    const source = sourceOfTopic(topic);
    return clean(topic && (topic.title || topic.text) || source.title || source.text || "");
  };
  const previewOfTopic = topic => {
    const source = sourceOfTopic(topic);
    return clean(topic && (topic.text || topic.title) || source.text || "");
  };
  const topicIdOfTopic = topic => String(topic && (topic.topic_id || topic.topic_uid || "") || "");
  const makeItem = (topic, gi, ti, groupTitle, columnId) => ({
    key: `toc:${gi}:${ti}`,
    groupIndex: gi,
    topicIndex: ti,
    groupTitle,
    title: titleOfTopic(topic),
    preview: previewOfTopic(topic),
    topicId: topicIdOfTopic(topic),
    topicUid: topicIdOfTopic(topic),
    columnId: String(columnId || ""),
  });
  const expectedCount = title => {
    const match = clean(title).match(/[（(]\s*(\d+)\s*[）)]/);
    return match ? parseInt(match[1], 10) : 0;
  };
  const lists = () => [...document.querySelectorAll(".list-container .list")];
  const topicItems = list => [...list.querySelectorAll(".topic-item")];
  const apiGroups = async () => {
    if (!groupId) return [];
    const columnsData = await apiGet(`https://api.zsxq.com/v2/groups/${groupId}/columns`);
    const columns = columnsData && columnsData.columns || [];
    if (!columns.length) return [];
    const fetchColumnTopics = async (columnId, expected) => {
      const url = `https://api.zsxq.com/v2/groups/${groupId}/columns/${columnId}/topics?count=100&sort=default&direction=desc`;
      let best = null;
      for (let attempt = 0; attempt < 4; attempt += 1) {
        if (attempt > 0) await wait(1200 + attempt * 1200);
        const data = await apiGet(url, 1);
        const topics = data && data.topics || [];
        if (!best || topics.length > ((best && best.topics) || []).length) best = data;
        if (!expected || topics.length >= expected) return data;
      }
      return best;
    };
    const topicBatches = [];
    for (const column of columns) {
      const columnId = String(column.column_id || "");
      const count = column.statistics && column.statistics.topics_count || 0;
      await wait(900);
      topicBatches.push(await fetchColumnTopics(columnId, count));
    }
    return columns.map((column, gi) => {
      const count = column.statistics && column.statistics.topics_count || 0;
      const name = clean(column.name || `目录 ${gi + 1}`);
      const groupTitle = count ? `${name}（${count}）` : name;
      const columnId = String(column.column_id || "");
      const topics = ((topicBatches[gi] && topicBatches[gi].topics) || [])
        .map((topic, ti) => makeItem(topic, gi, ti, groupTitle, columnId))
        .filter(item => item.title);
      return {
        key: `group:${gi}`,
        groupIndex: gi,
        groupTitle,
        expectedCount: count,
        topicCount: topics.length,
        topics,
        columnId,
      };
    }).filter(group => group.groupTitle || group.topics.length);
  };
  const domGroups = async () => {
    const groups = [];
  for (let gi = 0; gi < lists().length; gi += 1) {
    let list = lists()[gi];
    const groupTitle = clean((list.querySelector(".info .name") || list.querySelector(".name") || {}).innerText || `目录 ${gi + 1}`);
    const expected = expectedCount(groupTitle);
    const trigger = list.querySelector(".info .container") || list.querySelector(".info .name") || list.querySelector(".info") || list;
    if (expected && topicItems(list).length < expected) {
      await activate(trigger);
      const deadline = Date.now() + 2500;
      while (Date.now() < deadline) {
        await wait(250);
        list = lists()[gi];
        const current = topicItems(list).length;
        if (current >= expected || current > 0) break;
      }
    }
    const topics = topicItems(list).map((topic, ti) => {
      const content = topic.querySelector(".content") || topic;
      const title = clean(content.innerText || content.textContent || "");
      return {
        key: `toc:${gi}:${ti}`,
        groupIndex: gi,
        topicIndex: ti,
        groupTitle,
        title,
      };
    }).filter(item => item.title);
    groups.push({
      key: `group:${gi}`,
      groupIndex: gi,
      groupTitle,
      expectedCount: expected,
      topicCount: topics.length,
      topics,
      columnId: "",
    });
  }
    return groups.filter(group => group.groupTitle || group.topics.length);
  };
  let groups = await apiGroups();
  if (!groups.length || !groups.some(group => group.topics.some(topic => topic.topicId))) {
    groups = await domGroups();
  }
  return {
    href: location.href,
    title: clean((document.querySelector(".group-name") || document.querySelector(".title") || {}).innerText || document.title || "知识星球目录"),
    groups: groups.filter(group => group.groupTitle || group.topics.length),
    totalTopics: groups.reduce((sum, group) => sum + group.topics.length, 0),
  };
}
"""


ZSXQ_OPEN_TOC_ITEM_JS = r"""
async (target) => {
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const clean = s => (s || "").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
  const visible = el => {
    if (!el || el.nodeType !== 1 || el.hidden || el.getAttribute("aria-hidden") === "true") return false;
    const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
    return !style || (style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || "1") !== 0);
  };
  const activate = async el => {
    if (!el) return false;
    el.scrollIntoView({block: "center", inline: "nearest"});
    for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      el.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
    }
    try { el.click(); } catch (err) {}
    await wait(450);
    return true;
  };
  const lists = () => [...document.querySelectorAll(".list-container .list")];
  const topicItems = list => [...list.querySelectorAll(".topic-item")];
  const detailRoot = () => [...document.querySelectorAll([
    ".column-topic-detail .talk-content-container",
    ".column-topic-detail .answer-content-container",
    ".column-topic-detail",
    "#topic-detail-container",
    ".topic-detail",
    "[class*='TopicDetail']",
    "[class*='topic-detail']",
    ".talk-content-container",
    ".answer-content-container"
  ].join(","))].find(visible);
  const expectedCount = title => {
    const match = clean(title).match(/[（(]\s*(\d+)\s*[）)]/);
    return match ? parseInt(match[1], 10) : 0;
  };
  let list = lists()[target.groupIndex];
  if (!list) return {ok: false, error: "目录分组不存在", href: location.href};
  const groupTitle = clean((list.querySelector(".info .name") || list.querySelector(".name") || {}).innerText || "");
  const expected = expectedCount(groupTitle);
  if (expected && topicItems(list).length < expected) {
    const trigger = list.querySelector(".info .container") || list.querySelector(".info .name") || list.querySelector(".info") || list;
    await activate(trigger);
    const deadline = Date.now() + 8000;
    while (Date.now() < deadline) {
      await wait(250);
      list = lists()[target.groupIndex];
      if (topicItems(list).length >= expected || topicItems(list).length > target.topicIndex) break;
    }
  }
  list = lists()[target.groupIndex];
  const topic = topicItems(list)[target.topicIndex];
  if (!topic) return {ok: false, error: "目录条目不存在", href: location.href, groupTitle};
  const content = topic.querySelector(".content") || topic;
  const topicTitle = clean(content.innerText || content.textContent || target.title || "");
  const clickTargets = [
    topic.querySelector(".content"),
    topic.querySelector("a[href], button, [role='button']"),
    topic
  ].filter(Boolean);
  const deadline = Date.now() + 12000;
  let detailText = "";
  let detailSelector = "";
  let selected = "";
  for (const candidate of clickTargets) {
    await activate(candidate);
    const clickDeadline = Date.now() + 5000;
    while (Date.now() < clickDeadline) {
      await wait(350);
      const root = detailRoot();
      detailText = clean(root && root.innerText || "");
      detailSelector = root ? (root.id ? `#${root.id}` : root.className || root.tagName || "") : "";
      selected = clean((document.querySelector(".topic-item .content.selected, .topic-item.selected .content, .topic-item.active .content, .topic-item .active") || {}).innerText || "");
      if (detailText.length > 20 && (!target.title || detailText.includes(target.title.slice(0, 12)) || selected === topicTitle || selected.includes(topicTitle.slice(0, 12)))) {
        return {
          ok: true,
          href: location.href,
          groupTitle,
          topicTitle,
          detailTextLen: detailText.length,
          detailSelector,
        };
      }
    }
  }
  while (Date.now() < deadline) {
    await wait(350);
    const root = detailRoot();
    detailText = clean(root && root.innerText || "");
    detailSelector = root ? (root.id ? `#${root.id}` : root.className || root.tagName || "") : "";
    selected = clean((document.querySelector(".topic-item .content.selected, .topic-item.selected .content, .topic-item.active .content, .topic-item .active") || {}).innerText || "");
    if (detailText.length > 20 && (!target.title || detailText.includes(target.title.slice(0, 12)) || selected === topicTitle || selected.includes(topicTitle.slice(0, 12)))) {
      break;
    }
  }
  return {
    ok: detailText.length > 0,
    href: location.href,
    groupTitle,
    topicTitle,
    detailTextLen: detailText.length,
    detailSelector,
    selectedText: selected,
  };
}
"""


def expand_current_content(cdp: CDPClient, args: argparse.Namespace | None = None) -> dict[str, Any]:
    check_stopped(args)
    try:
        return cdp.evaluate(f"({ZSXQ_EXPAND_CONTENT_JS})()", timeout=20) or {}
    except Exception:
        return {}


def collect_current_comments(cdp: CDPClient, args: argparse.Namespace | None = None) -> list[dict[str, str]]:
    if not getattr(args, "include_comments", False):
        return []
    check_stopped(args)
    try:
        result = cdp.evaluate(f"({ZSXQ_COMMENTS_JS})()", timeout=45) or {}
    except Exception as exc:
        emit(args, f"评论区读取失败，已跳过：{exc}")
        return []
    comments: list[dict[str, str]] = []
    for item in result.get("comments") or []:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        comments.append(
            {
                "author": str(item.get("author") or "").strip(),
                "time": str(item.get("time") or "").strip(),
                "text": text,
            }
        )
    return comments


def append_comments_markdown(markdown: str, comments: list[dict[str, str]]) -> str:
    if not comments:
        return markdown
    lines = ["", "## 评论区", "", "> 以下为导出时页面可见的评论区内容。", ""]
    for index, comment in enumerate(comments, 1):
        author = comment.get("author") or f"评论 {index}"
        time_text = comment.get("time") or ""
        header = f"{author}（{time_text}）" if time_text else author
        lines.append(f"{index}. **{header}**")
        for line in (comment.get("text") or "").splitlines():
            line = line.strip()
            if line:
                lines.append(f"   {line}")
        lines.append("")
    return markdown.rstrip() + "\n\n" + "\n".join(lines).rstrip() + "\n"


def attach_comments_to_content(
    content: dict[str, Any],
    comments: list[dict[str, str]],
    include_comments: bool,
) -> dict[str, Any]:
    content["commentsIncluded"] = bool(include_comments)
    content["comments"] = comments if include_comments else []
    content["commentCount"] = len(comments) if include_comments else 0
    if include_comments and comments:
        content["markdown"] = append_comments_markdown(str(content.get("markdown") or ""), comments)
    return content


def collect_entry_links(
    cdp: CDPClient,
    entry_url: str,
    link_pattern: str | None,
    args: argparse.Namespace | None = None,
) -> dict[str, Any]:
    is_column_entry = "/columns/" in urllib.parse.urlparse(entry_url).path
    navigate_with_retry(cdp, entry_url, args)
    wait_eval(
        cdp,
        """(() => {
          const text = document.body && document.body.innerText || "";
          const links = [...document.querySelectorAll("a")]
            .filter(a => /(?:t|wx|articles)\\.zsxq\\.com/.test(a.href || a.getAttribute("href") || ""));
          return {
            href: location.href,
            text,
            linkCount: links.length,
            hasContent: !!document.querySelector(".talk-content-container, .answer-content-container, .content.ql-editor, .column-topic-detail"),
          };
        })()""",
        lambda value: len(value.get("text", "")) > 200
        and (
            value.get("linkCount", 0) > 0
            or (not is_column_entry and not link_pattern and bool(value.get("hasContent")))
        ),
        timeout=35,
        args=args,
    )
    expand_current_content(cdp, args)
    result = cdp.evaluate(
        f"({ZSXQ_CONVERTER_JS})({js_string('知识星球专栏正文')}, {js_string('.column-topic-detail .talk-content-container, .column-topic-detail .answer-content-container, .talk-content-container, .answer-content-container')})",
        timeout=60,
    )
    result = ensure_converter_content(result, "知识星球专栏正文")
    comments = collect_current_comments(cdp, args)
    attach_comments_to_content(result, comments, bool(getattr(args, "include_comments", False)))
    links = result.get("zsxqLinks") or []
    filtered: list[dict[str, str]] = []
    seen: set[str] = set()
    pattern = re.compile(link_pattern) if link_pattern else None
    for link in links:
        href = link.get("href") or ""
        text = link.get("text") or href
        if not href:
            continue
        if pattern and not pattern.search(text) and not pattern.search(href):
            continue
        if href in seen:
            continue
        seen.add(href)
        filtered.append({"text": text, "href": href})
    result["zsxqLinks"] = filtered
    result["url"] = cdp.evaluate("location.href", timeout=10)
    return result


def is_column_entry_url(entry_url: str) -> bool:
    return "/columns/" in urllib.parse.urlparse(entry_url).path


def group_id_from_url(entry_url: str) -> str:
    parsed = urllib.parse.urlparse(entry_url or "")
    candidates = [parsed.path or "", parsed.fragment or ""]
    for key in ("group_id", "groupId", "group"):
        value = urllib.parse.parse_qs(parsed.query).get(key, [""])[0]
        if value:
            candidates.append(value)
    for candidate in candidates:
        match = re.search(r"(?:^|/)(?:group|groups|columns)/(\d+)(?:/|$)", candidate)
        if match:
            return match.group(1)
        if re.fullmatch(r"\d{6,}", candidate.strip()):
            return candidate.strip()
    return ""


def is_group_entry_url(entry_url: str) -> bool:
    parsed = urllib.parse.urlparse(entry_url or "")
    return bool(group_id_from_url(entry_url)) and not parsed.path.startswith("/columns/")


def group_scope_from_args(entry_url: str, args: argparse.Namespace | None = None) -> str:
    scope = str((getattr(args, "group_scope", "auto") if args else "auto") or "auto").strip()
    if scope and scope != "auto":
        return scope
    parsed = urllib.parse.urlparse(entry_url or "")
    if "/digests" in parsed.path or "/digests" in parsed.fragment:
        return "digests"
    return "all"


def normalize_group_limit(args: argparse.Namespace | None = None) -> int:
    raw_limit = int((getattr(args, "limit", 0) if args else 0) or 0)
    if raw_limit <= 0:
        return DEFAULT_GROUP_LIMIT
    return raw_limit


def normalize_group_page_size(args: argparse.Namespace | None = None) -> int:
    raw = getattr(args, "group_page_size", DEFAULT_GROUP_BATCH_SIZE) if args else DEFAULT_GROUP_BATCH_SIZE
    try:
        value = int(raw or DEFAULT_GROUP_BATCH_SIZE)
    except (TypeError, ValueError):
        value = DEFAULT_GROUP_BATCH_SIZE
    return max(1, min(MAX_GROUP_BATCH_SIZE, value))


def normalize_group_max_pages(args: argparse.Namespace | None, limit: int, page_size: int) -> int:
    raw = getattr(args, "group_max_pages", 200) if args else 200
    try:
        value = max(1, int(raw or 200))
    except (TypeError, ValueError):
        value = 200
    if limit > 0 and page_size > 0:
        needed = max(1, (limit + page_size - 1) // page_size)
        return max(value, needed)
    return value


def warn_large_group_export(args: argparse.Namespace | None, limit: int) -> None:
    if not args or limit <= GROUP_LONG_EXPORT_WARNING_LIMIT or getattr(args, "_large_group_export_warned", False):
        return
    setattr(args, "_large_group_export_warned", True)
    emit(
        args,
        f"知识星球 Group 本次计划导出 {limit} 条。连续长时间导出可能触发风控甚至封号，尽量不要让单次任务超过 24 小时。",
        event="task.warning",
        level="warn",
        risk={"type": "long-running-zsxq-export", "limit": limit, "threshold": GROUP_LONG_EXPORT_WARNING_LIMIT},
    )


def apply_zsxq_safety_defaults(args: argparse.Namespace) -> argparse.Namespace:
    args.group_page_size = normalize_group_page_size(args)
    try:
        args.request_delay = max(MIN_REQUEST_DELAY, float(getattr(args, "request_delay", DEFAULT_REQUEST_DELAY) or 0))
    except (TypeError, ValueError):
        args.request_delay = DEFAULT_REQUEST_DELAY
    try:
        args.request_jitter = max(MIN_REQUEST_JITTER, float(getattr(args, "request_jitter", DEFAULT_REQUEST_JITTER) or 0))
    except (TypeError, ValueError):
        args.request_jitter = DEFAULT_REQUEST_JITTER
    try:
        args.comment_request_delay = max(
            DEFAULT_COMMENT_REQUEST_DELAY,
            float(getattr(args, "comment_request_delay", DEFAULT_COMMENT_REQUEST_DELAY) or 0),
        )
    except (TypeError, ValueError):
        args.comment_request_delay = DEFAULT_COMMENT_REQUEST_DELAY
    try:
        args.comment_request_jitter = max(
            DEFAULT_COMMENT_REQUEST_JITTER,
            float(getattr(args, "comment_request_jitter", DEFAULT_COMMENT_REQUEST_JITTER) or 0),
        )
    except (TypeError, ValueError):
        args.comment_request_jitter = DEFAULT_COMMENT_REQUEST_JITTER
    return args


def group_scope_title(scope: str) -> str:
    return {
        "all": "全部主题",
        "digests": "精华主题",
        "by_owner": "只看星主",
    }.get(scope, "全部主题")


def group_topic_to_item(topic: dict[str, Any], scope: str, index: int, include_raw: bool = True) -> dict[str, Any]:
    topic_id = str(topic.get("topic_id") or topic.get("topic_uid") or "").strip()
    item = {
        "key": f"group:{scope}:{index}:{topic_id or index}",
        "groupIndex": 0,
        "topicIndex": index,
        "groupTitle": group_scope_title(scope),
        "title": topic_title_for_list(topic),
        "preview": topic_preview_text(topic),
        "topicId": topic_id,
        "topicUid": topic_id,
        "topicUrl": f"https://wx.zsxq.com/topic/{topic_id}" if topic_id else "",
        "createTime": topic.get("create_time") or "",
        "digested": bool(topic.get("digested")),
    }
    if include_raw:
        item["rawTopic"] = topic
    return item


def previous_zsxq_end_time(create_time: str) -> str:
    create_time = str(create_time or "").strip()
    match = re.match(r"^(.+?T\d{2}:\d{2}:\d{2}\.)(\d{3})(.*)$", create_time)
    if not match:
        return create_time
    prefix, millis, suffix = match.groups()
    value = int(millis)
    if value > 0:
        return f"{prefix}{value - 1:03d}{suffix}"
    dt_match = re.match(r"^(?P<body>.+?T\d{2}:\d{2}:\d{2})\.000(?P<tz>Z|[+-]\d{2}:?\d{2})?$", create_time)
    if not dt_match:
        return f"{prefix}000{suffix}"
    body = dt_match.group("body")
    tz = dt_match.group("tz") or ""
    try:
        previous = datetime.strptime(body, "%Y-%m-%dT%H:%M:%S") - timedelta(milliseconds=1)
    except ValueError:
        return f"{prefix}000{suffix}"
    return f"{previous.strftime('%Y-%m-%dT%H:%M:%S')}.999{tz}"


def export_sequence_from_name(name: str) -> int | None:
    """Return a positive numeric export prefix, excluding reserved ``00-*`` entries."""
    match = EXPORT_SEQUENCE_RE.match(str(name or ""))
    if not match:
        return None
    sequence = int(match.group("sequence"))
    return sequence if sequence > 0 else None


def scan_max_export_sequence(base_dir: Path) -> int:
    """Find the highest immediate child sequence without walking nested folders."""
    if not base_dir.exists() or not base_dir.is_dir():
        return 0
    maximum = 0
    try:
        entries = base_dir.iterdir()
        for entry in entries:
            if not entry.is_dir() and entry.suffix.lower() != ".md":
                continue
            sequence = export_sequence_from_name(entry.name)
            if sequence is not None:
                maximum = max(maximum, sequence)
    except OSError:
        return maximum
    return maximum


def compare_zsxq_create_time(left: str, right: str) -> int:
    """Compare API timestamps while tolerating both ``+0800`` and ``+08:00`` forms."""
    def parse(value: str) -> datetime | None:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        elif re.search(r"[+-]\d{4}$", normalized):
            normalized = normalized[:-5] + normalized[-5:-2] + ":" + normalized[-2:]
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    left_dt = parse(left)
    right_dt = parse(right)
    if left_dt is not None and right_dt is not None:
        return (left_dt > right_dt) - (left_dt < right_dt)
    left_text = str(left or "")
    right_text = str(right or "")
    return (left_text > right_text) - (left_text < right_text)


def group_topic_reaches_watermark(
    topic: dict[str, Any],
    latest_create_time: str,
    latest_topic_id: str,
) -> bool:
    """Return true once a newest-first listing reaches the previous run's boundary."""
    topic_id = str(topic.get("topic_id") or topic.get("topic_uid") or "").strip()
    create_time = str(topic.get("create_time") or "").strip()
    if latest_topic_id and topic_id == str(latest_topic_id):
        return True
    return bool(
        latest_create_time
        and create_time
        and compare_zsxq_create_time(create_time, latest_create_time) < 0
    )


def load_compatible_group_cursor(
    checkpoint: WandaoCheckpoint,
    group_id: str,
    scope: str,
    source: str,
    output: Path,
) -> tuple[dict[str, Any], str]:
    """Load the newest compatible cursor, including one written by an earlier UI job id."""
    current = checkpoint.load_cursor(GROUP_CURSOR_NAME, {})
    if (
        isinstance(current, dict)
        and str(current.get("group_id") or "") == str(group_id)
        and str(current.get("scope") or "") == str(scope)
    ):
        return current, checkpoint.task_id

    row = checkpoint.conn.execute(
        """
        SELECT c.task_id, c.cursor_value_json
        FROM cursors c
        JOIN tasks t ON t.task_id = c.task_id
        WHERE c.cursor_name = ?
          AND c.task_id != ?
          AND t.provider_id = 'zsxq'
          AND t.action = 'export'
          AND lower(COALESCE(t.source, '')) = lower(?)
          AND lower(COALESCE(t.output_dir, '')) = lower(?)
        ORDER BY c.updated_at DESC, t.updated_at DESC, c.rowid DESC
        LIMIT 20
        """,
        (GROUP_CURSOR_NAME, checkpoint.task_id, str(source), str(output.resolve())),
    ).fetchall()
    for candidate in row:
        try:
            cursor = json.loads(candidate["cursor_value_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if (
            isinstance(cursor, dict)
            and str(cursor.get("group_id") or "") == str(group_id)
            and str(cursor.get("scope") or "") == str(scope)
        ):
            return cursor, str(candidate["task_id"] or "")
    return {}, ""


def inherit_unresolved_group_items(checkpoint: WandaoCheckpoint, source_task_id: str) -> int:
    """Copy unfinished listed items when the desktop app starts a new job id."""
    if not source_task_id or source_task_id == checkpoint.task_id:
        return 0
    rows = checkpoint.conn.execute(
        """
        SELECT * FROM items
        WHERE task_id = ? AND status IN ('pending', 'running', 'interrupted', 'failed')
        ORDER BY created_at, item_key
        """,
        (source_task_id,),
    ).fetchall()
    inherited = 0
    for raw_row in rows:
        row = dict(raw_row)
        try:
            metadata = json.loads(row.get("metadata_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        checkpoint.upsert_item(
            str(row.get("item_key") or ""),
            title=str(row.get("title") or ""),
            source_url=str(row.get("source_url") or ""),
            source_id=str(row.get("source_id") or ""),
            parent_key=str(row.get("parent_key") or ""),
            metadata=metadata if isinstance(metadata, dict) else {},
        )
        local_path = str(row.get("local_path") or "").strip()
        if local_path:
            with checkpoint.conn:
                checkpoint.conn.execute(
                    "UPDATE items SET local_path = ? WHERE task_id = ? AND item_key = ?",
                    (local_path, checkpoint.task_id, str(row.get("item_key") or "")),
                )
        inherited += 1
    return inherited


def topic_preview_text(topic: dict[str, Any]) -> str:
    _, primary = topic_source(topic)
    text = str(topic.get("title") or primary.get("title") or primary.get("text") or topic.get("text") or "").strip()
    text = re.sub(r"<e\b[^>]*/>", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def topic_title_for_list(topic: dict[str, Any], fallback: str = "知识星球主题") -> str:
    text = topic_preview_text(topic)
    if text:
        return text[:80]
    topic_id = str(topic.get("topic_id") or topic.get("topic_uid") or "").strip()
    return f"{fallback}-{topic_id}" if topic_id else fallback


def zsxq_api_endpoint_label(url: str) -> str:
    parsed = urllib.parse.urlparse(url or "")
    path = parsed.path or ""
    if "/columns/" in path and "/topics" in path:
        return "专栏目录接口"
    if "/groups/" in path and "/topics" in path:
        return "帖子列表接口"
    if "/topics/" in path and "/info" in path:
        return "帖子正文接口"
    if "/comments" in path:
        return "评论接口"
    return parsed.netloc or "接口"


def summarize_zsxq_api_failure(result: dict[str, Any], action: str) -> str:
    attempts = [item for item in (result.get("attempts") or []) if isinstance(item, dict)]
    joined = " ".join(
        str(value or "")
        for item in attempts + [result]
        for value in (
            item.get("message"),
            item.get("error"),
            item.get("text"),
            item.get("textPreview"),
            item.get("code"),
        )
    )
    if result.get("rateLimited") or re.search(r"Too Many Requests|请求过于频繁|请求太频繁|访问过于频繁|非官方工具|1059", joined, re.I):
        reason = "触发了知识星球频率限制或风控，请稍后重试，并适当调大请求延迟。"
    elif result.get("authRequired") or re.search(r"登录|请登录|扫码|unauthorized|not authorized|未授权", joined, re.I):
        reason = "登录状态没有生效或已过期，请在浏览器确认能看到目标内容后重新保存凭证。"
    elif result.get("permissionDenied") or re.search(r"无权|权限|不是成员|加入星球|会员|购买|续费|已过期|forbidden", joined, re.I):
        reason = "当前账号可能无权访问该星球/专栏，或会员状态已过期。"
    elif re.search(r"无效的count|invalid count|14001", joined, re.I):
        reason = f"接口参数 count 超出知识星球当前允许范围，已建议单批不超过 {MAX_GROUP_BATCH_SIZE} 条。"
    elif re.search(r"版本太旧|下载并安装最新的版本", joined, re.I):
        reason = "备用旧版接口已不可用，应优先使用新版接口；如果新版接口也失败，请查看同一条摘要里的新版接口错误。"
    elif re.search(r"主题不存在|已被删除|not found|1007", joined, re.I):
        reason = "目标帖子不存在、已删除，或接口返回的帖子 ID 不可用。"
    elif re.search(r"Failed to fetch|NetworkError|ERR_", joined, re.I):
        reason = "浏览器内请求接口失败，通常和登录态、网络拦截、CORS 或平台临时限制有关。"
    else:
        reason = "接口没有返回可用的数据结构，可能是入口 URL 不正确、接口格式变化或账号状态异常。"

    details: list[str] = []
    for item in attempts:
        label = zsxq_api_endpoint_label(str(item.get("url") or ""))
        status = item.get("status")
        code = str(item.get("code") or "").strip()
        message = str(item.get("message") or item.get("error") or item.get("textPreview") or "").strip()
        topic_count = item.get("topicCount")
        pieces = [label]
        if status:
            pieces.append(f"HTTP {status}")
        if code:
            pieces.append(f"code={code}")
        if topic_count is not None:
            pieces.append(f"topics={topic_count}")
        if message:
            pieces.append(message[:180])
        details.append(" ".join(pieces))

    if not details:
        fallback = str(result.get("error") or result.get("text") or "").strip()
        if fallback:
            details.append(fallback[:260])

    detail_text = "；".join(details)
    return f"{action}：{reason}" + (f" 接口摘要：{detail_text}" if detail_text else "")


def ensure_zsxq_api_origin(
    cdp: CDPClient,
    args: argparse.Namespace | None = None,
    fallback_url: str = "https://wx.zsxq.com/",
) -> None:
    """Keep browser-side API fetches on wx.zsxq.com, otherwise CORS can block api.zsxq.com."""
    try:
        host = str(cdp.evaluate("location.hostname", timeout=5) or "").lower()
    except Exception:
        host = ""
    if host == "wx.zsxq.com":
        return
    navigate_with_retry(cdp, fallback_url or "https://wx.zsxq.com/", args)
    wait_eval(
        cdp,
        "({host: location.hostname, textLen: (document.body && document.body.innerText || '').length})",
        lambda value: value.get("host") == "wx.zsxq.com" or value.get("textLen", 0) > 20,
        timeout=20,
        args=args,
    )


def validate_zsxq_auth(cdp: CDPClient, args: argparse.Namespace | None = None) -> dict[str, Any]:
    """Best-effort account check after saving cookies; do not treat network failures as fatal."""
    ensure_zsxq_api_origin(cdp, args, "https://wx.zsxq.com/")
    expression = r"""
    (async () => {
      const urls = [
        "https://api.zsxq.com/v3/users/self",
        "https://api.zsxq.com/v2/users/self"
      ];
      const parseZsxqJson = (raw) => JSON.parse(raw.replace(
        /("(?:topic_id|topic_uid|group_id|user_id|uid|file_id|comment_id|column_id|image_id|video_id|owner_user_id|repliee_user_id)"\s*:\s*)(\d{15,})/g,
        '$1"$2"'
      ));
      const compact = raw => String(raw || "").replace(/\s+/g, " ").trim().slice(0, 240);
      const attempts = [];
      for (const url of urls) {
        try {
          const r = await fetch(url, {
            credentials: "include",
            headers: {accept: "application/json, text/plain, */*"}
          });
          const text = await r.text();
          let data = {};
          try { data = parseZsxqJson(text); } catch (err) {}
          const payload = data && data.resp_data || {};
          const user = payload.user || payload.user_info || payload.owner || payload;
          const code = data && (data.code || data.error_code || data.status) || "";
          const message = data && (data.msg || data.error || data.message || data.info) || "";
          const account = {
            userId: String(user.user_id || user.uid || user.id || ""),
            name: String(user.name || user.nickname || user.display_name || user.alias || "")
          };
          attempts.push({
            url,
            status: r.status,
            ok: !!data.succeeded,
            code: String(code || ""),
            message: String(message || ""),
            textPreview: compact(text),
            account
          });
          if (data && data.succeeded && (account.userId || account.name)) {
            return {ok: true, status: r.status, url, account, attempts};
          }
        } catch (err) {
          attempts.push({url, ok: false, error: String(err)});
        }
      }
      const text = attempts.map(x => [
        x.status ? "HTTP " + x.status : "",
        x.code ? "code=" + x.code : "",
        x.message || x.error || x.textPreview || ""
      ].filter(Boolean).join(" ")).join("; ").slice(0, 700);
      return {ok: false, attempts, authRequired: true, text};
    })()
    """
    try:
        result = cdp.evaluate(expression, timeout=30) or {}
    except Exception as exc:
        return {"ok": False, "text": str(exc), "attempts": [{"error": str(exc)}]}
    return result if isinstance(result, dict) else {"ok": False, "text": str(result)}


def fetch_group_topics_page(
    cdp: CDPClient,
    group_id: str,
    scope: str,
    end_time: str,
    count: int,
    args: argparse.Namespace | None = None,
) -> dict[str, Any]:
    scope_param = "" if scope == "all" else scope
    expression = f"""
    (async () => {{
      const groupId = {json.dumps(group_id)};
      const count = {int(count)};
      const scope = {json.dumps(scope_param)};
      const endTime = {json.dumps(end_time or "")};
      const makeUrl = (base) => {{
        const url = new URL(base);
        url.searchParams.set("count", String(count));
        if (scope) url.searchParams.set("scope", scope);
        if (endTime) url.searchParams.set("end_time", endTime);
        return url.toString();
      }};
      const urls = [
        makeUrl(`https://api.zsxq.com/v2/groups/${{groupId}}/topics`),
        makeUrl(`https://api.zsxq.com/v1.10/groups/${{groupId}}/topics`)
      ];
      const parseZsxqJson = (raw) => JSON.parse(raw.replace(
        /("(?:topic_id|topic_uid|group_id|user_id|uid|file_id|comment_id|column_id|image_id|video_id|owner_user_id|repliee_user_id)"\\s*:\\s*)(\\d{{15,}})/g,
        '$1"$2"'
      ));
      const compact = raw => String(raw || "").replace(/\\s+/g, " ").trim().slice(0, 240);
      const looksAuth = raw => /登录|请登录|扫码|unauthorized|not authorized|未授权/i.test(String(raw || ""));
      const looksPermission = raw => /无权|权限|不是成员|加入星球|会员|购买|续费|已过期|forbidden/i.test(String(raw || ""));
      const attempts = [];
      for (const url of urls) {{
        try {{
          const r = await fetch(url, {{
            credentials: "include",
            headers: {{accept: "application/json, text/plain, */*"}}
          }});
          const text = await r.text();
          const rateLimited = r.status === 429 || /Too Many Requests|请求过于频繁|请求太频繁|访问过于频繁/i.test(text);
          let data = {{}};
          try {{ data = parseZsxqJson(text); }} catch (err) {{}}
          const code = data && (data.code || data.error_code || data.status) || "";
          const message = data && (data.msg || data.error || data.message || data.info) || "";
          const textPreview = compact(text);
          const authRequired = looksAuth(message) || looksAuth(textPreview);
          const permissionDenied = looksPermission(message) || looksPermission(textPreview);
          const antiCrawl = String(code || "") === "1059" || /非官方工具|official Skill|garden[.]zsxq[.]com[/]skill/i.test(text);
          const topics = data && data.resp_data && Array.isArray(data.resp_data.topics) ? data.resp_data.topics : [];
          attempts.push({{
            url,
            status: r.status,
            ok: !!data.succeeded,
            topicCount: topics.length,
            code: String(code || ""),
            message: String(message || ""),
            textPreview,
            contentType: r.headers.get("content-type") || "",
            authRequired,
            permissionDenied
          }});
          if (rateLimited || antiCrawl) return {{ok: false, rateLimited: true, status: r.status, text: textPreview, attempts}};
          if (data && data.succeeded && Array.isArray(data.resp_data && data.resp_data.topics)) {{
            return {{
              ok: true,
              status: r.status,
              url,
              topics,
              attempts,
              text: text.slice(0, 240)
            }};
          }}
        }} catch (err) {{
          attempts.push({{url, ok: false, error: String(err)}});
        }}
      }}
      const authRequired = attempts.some(x => x.authRequired);
      const permissionDenied = attempts.some(x => x.permissionDenied);
      const text = attempts.map(x => [
        x.status ? "HTTP " + x.status : "",
        x.code ? "code=" + x.code : "",
        x.message || x.error || x.textPreview || ""
      ].filter(Boolean).join(" ")).join("; ").slice(0, 700);
      return {{ok: false, attempts, authRequired, permissionDenied, text}};
    }})()
    """
    retries = max(0, int(getattr(args, "rate_limit_retries", 5) if args else 5))
    result: dict[str, Any] = {}
    for attempt in range(retries + 1):
        check_stopped(args)
        ensure_zsxq_api_origin(cdp, args, f"https://wx.zsxq.com/group/{group_id}")
        throttle_request(args)
        result = cdp.evaluate(expression, timeout=60) or {}
        if result.get("rateLimited"):
            pause_for_rate_limit(args, f"知识星球 group topics {group_id}", attempt, retries)
            continue
        return result
    return result


def collect_group_toc(cdp: CDPClient, entry_url: str, args: argparse.Namespace | None = None) -> dict[str, Any]:
    group_id = group_id_from_url(entry_url)
    if not group_id:
        raise ExportError("无法从知识星球 group URL 中识别星球 ID")
    scope = group_scope_from_args(entry_url, args)
    count = normalize_group_page_size(args)
    limit = normalize_group_limit(args)
    max_pages = normalize_group_max_pages(args, limit, count)
    navigate_with_retry(cdp, entry_url, args)

    topics: list[dict[str, Any]] = []
    seen_topic_ids: set[str] = set()
    end_time = ""
    page_count = 0
    while page_count < max_pages:
        check_stopped(args)
        result = fetch_group_topics_page(cdp, group_id, scope, end_time, count, args)
        if not result.get("ok"):
            raise ExportError(summarize_zsxq_api_failure(result, f"知识星球 group 主题列表读取失败：{group_id}"))
        batch = [topic for topic in result.get("topics") or [] if isinstance(topic, dict)]
        page_count += 1
        added = 0
        for topic in batch:
            topic_id = str(topic.get("topic_id") or topic.get("topic_uid") or "").strip()
            if topic_id and topic_id in seen_topic_ids:
                continue
            if topic_id:
                seen_topic_ids.add(topic_id)
            topics.append(topic)
            added += 1
            if limit and len(topics) >= limit:
                break
        emit(
            args,
            f"知识星球 group 帖子列表读取：page={page_count} batch={len(batch)} total={len(topics)} target={limit} scope={scope}",
            event="task.progress",
            progress={"current": len(topics), "total": limit},
            stats={"groupPage": page_count, "groupBatchTopics": len(batch)},
        )
        if limit and len(topics) >= limit:
            break
        if not batch or added == 0 or len(batch) < count:
            break
        last_time = str(batch[-1].get("create_time") or "").strip()
        if not last_time or last_time == end_time:
            break
        end_time = previous_zsxq_end_time(last_time)
        pause_between_group_pages(args, page_count, len(topics))

    group_title = group_scope_title(scope)
    items: list[dict[str, Any]] = []
    for index, topic in enumerate(topics):
        item = group_topic_to_item(topic, scope, index, include_raw=not getattr(args, "scan_toc", False))
        item["entryUrl"] = entry_url
        items.append(item)
    return {
        "href": entry_url,
        "title": f"知识星球 {group_title}",
        "groupId": group_id,
        "scope": scope,
        "pageCount": page_count,
        "groups": [
            {
                "key": f"group:{scope}",
                "groupIndex": 0,
                "groupTitle": group_title,
                "expectedCount": len(items),
                "topicCount": len(items),
                "topics": items,
            }
        ],
        "totalTopics": len(items),
    }


def collect_toc(cdp: CDPClient, entry_url: str, args: argparse.Namespace | None = None) -> dict[str, Any]:
    navigate_with_retry(cdp, entry_url, args)
    wait_eval(
        cdp,
        """(() => ({
          href: location.href,
          listCount: document.querySelectorAll(".list-container .list").length,
          textLen: (document.body && document.body.innerText || "").length,
        }))()""",
        lambda value: value.get("listCount", 0) > 0 and value.get("textLen", 0) > 200,
        timeout=35,
        args=args,
    )
    toc = cdp.evaluate(f"({ZSXQ_TOC_JS})()", timeout=90) or {}
    groups = toc.get("groups") or []
    for group in groups:
        for item in group.get("topics") or []:
            item["key"] = f"toc:{item.get('groupIndex')}:{item.get('topicIndex')}"
            item["groupTitle"] = item.get("groupTitle") or group.get("groupTitle") or ""
            item["entryUrl"] = toc.get("href") or entry_url
            topic_id = str(item.get("topicId") or item.get("topicUid") or "").strip()
            if topic_id and not item.get("topicUrl"):
                item["topicUrl"] = f"https://wx.zsxq.com/topic/{topic_id}"
    toc["groups"] = groups
    toc["totalTopics"] = sum(len(group.get("topics") or []) for group in groups)
    return toc


def flatten_toc(toc: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for group in toc.get("groups") or []:
        for item in group.get("topics") or []:
            item = dict(item)
            item.setdefault("groupTitle", group.get("groupTitle") or "")
            items.append(item)
    return items


def select_toc_items(toc: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    items = flatten_toc(toc)
    selected_keys = set(getattr(args, "selected_toc_keys", None) or [])
    if selected_keys:
        selected_items = [item for item in items if item.get("key") in selected_keys]
        if items and not selected_items:
            preview = ", ".join(sorted(selected_keys)[:5])
            raise ExportError(
                "选择的知识星球专栏文档未匹配当前目录，"
                "请重新读取目录后再试。未匹配 ID：" + preview
            )
        items = selected_items
    group_pattern_text = getattr(args, "toc_group_pattern", None)
    if group_pattern_text:
        group_pattern = re.compile(group_pattern_text)
        items = [item for item in items if group_pattern.search(item.get("groupTitle") or "")]
    title_pattern_text = getattr(args, "toc_title_pattern", None) or getattr(args, "link_pattern", None)
    if title_pattern_text:
        title_pattern = re.compile(title_pattern_text)
        items = [
            item
            for item in items
            if title_pattern.search(item.get("title") or "") or title_pattern.search(item.get("groupTitle") or "")
        ]
    if args.limit and args.limit > 0:
        items = items[: args.limit]
    return items


def resolve_toc_item(cdp: CDPClient, source: dict[str, Any], args: argparse.Namespace | None = None) -> dict[str, Any]:
    check_stopped(args)
    if source.get("topicId") or source.get("topicUid"):
        try:
            return resolve_toc_item_api(cdp, source, args)
        except (ExportStopped, SkipDocument):
            raise
        except Exception as exc:
            if str(source.get("key") or "").startswith("group:"):
                raise ExportError(f"知识星球 group 主题 API 读取失败：{source.get('title') or source.get('topicId') or source.get('key')} ({exc})") from exc
            emit(args, f"目录 API 读取失败，改用页面点击：{source.get('title') or source.get('key')} ({exc})", event="log.message", level="warn")
            if source.get("entryUrl"):
                navigate_with_retry(cdp, str(source.get("entryUrl")), args)
    info = cdp.evaluate(f"({ZSXQ_OPEN_TOC_ITEM_JS})({json.dumps(source, ensure_ascii=False)})", timeout=60) or {}
    if not info.get("ok"):
        raise ExportError(f"无法打开目录条目：{source.get('groupTitle', '')} / {source.get('title', '')} ({info.get('error') or '未知错误'})")
    expand_current_content(cdp, args)
    topic_url = cdp.evaluate("location.href", timeout=10)
    video_info = should_skip_video_topic(cdp, topic_url, args)
    if video_info:
        raise SkipDocument(
            "video-topic",
            title=source.get("title") or video_info.get("title") or topic_url,
            href=topic_url,
        )
    article_info = find_article_url_on_topic(cdp, args)
    article_url = article_info.get("article") or ""
    if article_url:
        topic_comments = collect_current_comments(cdp, args)
        navigate_with_retry(cdp, article_url, args)
        wait_eval(
            cdp,
            "({href: location.href, ok: !!document.querySelector('.content.ql-editor'), len: (document.body && document.body.innerText || '').length})",
            lambda value: bool(value.get("ok")) or value.get("len", 0) > 1000,
            timeout=35,
            args=args,
        )
        if detect_rate_limited_page(cdp).get("limited"):
            raise ExportError(f"目录条目触发请求频率限制：{source.get('title') or source.get('key')}")
        content = cdp.evaluate(
            f"({ZSXQ_CONVERTER_JS})({js_string(source.get('title') or '知识星球文章')}, {js_string('.content.ql-editor')})",
            timeout=90,
        )
        content = ensure_converter_content(content, source.get("title") or source.get("key") or "目录文章")
        if content_is_rate_limited(content):
            raise ExportError(f"目录条目触发请求频率限制：{source.get('title') or source.get('key')}")
        attach_comments_to_content(content, topic_comments, bool(getattr(args, "include_comments", False)))
        content["sourceType"] = "article"
        content["shortUrl"] = topic_url
        content["articleUrl"] = cdp.evaluate("location.href", timeout=10)
        content["topicUrl"] = topic_url
        content["sourceText"] = source.get("title") or ""
        content["tocKey"] = source.get("key") or ""
        content["tocGroup"] = source.get("groupTitle") or info.get("groupTitle") or ""
        content["tocTitle"] = source.get("title") or info.get("topicTitle") or ""
        return content
    content = cdp.evaluate(
        f"({ZSXQ_CONVERTER_JS})({js_string(source.get('title') or '知识星球文档')}, {js_string('.column-topic-detail .talk-content-container, .column-topic-detail .answer-content-container, .column-topic-detail, #topic-detail-container, .topic-detail, [class*=TopicDetail], [class*=topic-detail], .talk-content-container, .answer-content-container')})",
        timeout=90,
    )
    content = ensure_converter_content(content, source.get("title") or source.get("key") or "目录文章")
    if content_is_rate_limited(content):
        raise ExportError(f"目录条目触发请求频率限制：{source.get('title') or source.get('key')}")
    comments = collect_current_comments(cdp, args)
    attach_comments_to_content(content, comments, bool(getattr(args, "include_comments", False)))
    content["sourceType"] = "column-topic"
    content["shortUrl"] = ""
    content["articleUrl"] = ""
    content["topicUrl"] = topic_url
    content["sourceText"] = source.get("title") or ""
    content["tocKey"] = source.get("key") or ""
    content["tocGroup"] = source.get("groupTitle") or info.get("groupTitle") or ""
    content["tocTitle"] = source.get("title") or info.get("topicTitle") or ""
    return content


def find_article_url_on_topic(cdp: CDPClient, args: argparse.Namespace | None = None) -> dict[str, Any]:
    return wait_eval(
        cdp,
        r"""(() => {
          const topic = document.querySelector(".talk-content-container, .answer-content-container") || document.querySelector("#topic-detail-container");
          const article = [...document.querySelectorAll("a")]
            .map(a => a.href || a.getAttribute("href") || "")
            .find(h => /articles\.zsxq\.com\/.+\.html/.test(h)) || "";
          return {
            href: location.href,
            title: document.title,
            article,
            topicText: (topic && topic.innerText || "").slice(0, 1200),
            ready: !!topic || document.body.innerText.length > 300,
          };
        })()""",
        lambda value: bool(value.get("article")) or is_zsxq_article_url(str(value.get("href") or "")) or bool(value.get("ready")),
        timeout=35,
        args=args,
    )


def topic_id_from_url(url: str) -> str:
    match = re.search(r"/topic/(\d+)", url or "")
    return match.group(1) if match else ""


def inspect_topic_api(cdp: CDPClient, topic_id: str, args: argparse.Namespace | None = None) -> dict[str, Any]:
    if not topic_id:
        return {}
    expression = f"""
    (async () => {{
      try {{
        const r = await fetch('https://api.zsxq.com/v2/topics/{topic_id}/info', {{
          credentials: 'include',
          headers: {{accept: 'application/json, text/plain, */*'}}
        }});
        const text = await r.text();
        const rateLimited = r.status === 429 || /Too Many Requests|请求过于频繁|请求太频繁|访问过于频繁/i.test(text);
        const parseZsxqJson = (raw) => JSON.parse(raw.replace(
          /("(?:topic_id|topic_uid|group_id|user_id|uid|file_id|comment_id|column_id|image_id|video_id|owner_user_id|repliee_user_id)"\\s*:\\s*)(\\d{{15,}})/g,
          '$1"$2"'
        ));
        let data = {{}};
        try {{ data = parseZsxqJson(text); }} catch (err) {{}}
        const antiCrawl = String(data && data.code || "") === "1059" || /非官方工具|official Skill|garden[.]zsxq[.]com[/]skill/i.test(text);
        if (rateLimited || antiCrawl) {{
          return {{ok: false, rateLimited: true, status: r.status, text: text.slice(0, 200)}};
        }}
        const topic = data && data.resp_data && data.resp_data.topic || {{}};
        const source = topic.talk || topic.question || topic.task || topic.solution || {{}};
        return {{
          ok: !!data.succeeded,
          status: r.status,
          title: topic.title || '',
          type: topic.type || '',
          hasVideo: !!source.video,
          videoId: source.video && source.video.video_id || '',
          hasArticle: !!source.article,
          articleUrl: source.article && (source.article.article_url || source.article.inline_article_url) || '',
          textLen: (source.text || '').length
        }};
      }} catch (err) {{
        return {{ok: false, error: String(err)}};
      }}
    }})()
    """
    retries = max(0, int(getattr(args, "rate_limit_retries", 5) if args else 5))
    result: dict[str, Any] = {}
    for attempt in range(retries + 1):
        check_stopped(args)
        throttle_request(args)
        result = cdp.evaluate(expression, timeout=20) or {}
        if not result.get("rateLimited"):
            return result
        pause_for_rate_limit(args, f"topic API {topic_id}", attempt, retries)
    return result


def fetch_topic_info_api(cdp: CDPClient, topic_id: str, args: argparse.Namespace | None = None) -> dict[str, Any]:
    if not topic_id:
        return {}
    expression = f"""
    (async () => {{
      try {{
        const r = await fetch('https://api.zsxq.com/v2/topics/{topic_id}/info', {{
          credentials: 'include',
          headers: {{accept: 'application/json, text/plain, */*'}}
        }});
        const text = await r.text();
        const rateLimited = r.status === 429 || /Too Many Requests|请求过于频繁|请求太频繁|访问过于频繁/i.test(text);
        const parseZsxqJson = (raw) => JSON.parse(raw.replace(
          /("(?:topic_id|topic_uid|group_id|user_id|uid|file_id|comment_id|column_id|image_id|video_id|owner_user_id|repliee_user_id)"\\s*:\\s*)(\\d{{15,}})/g,
          '$1"$2"'
        ));
        let data = {{}};
        try {{ data = parseZsxqJson(text); }} catch (err) {{}}
        const code = data && (data.code || data.error_code || data.status) || "";
        const message = data && (data.msg || data.error || data.message || data.info) || "";
        const textPreview = String(text || "").replace(/\\s+/g, " ").trim().slice(0, 300);
        const authRequired = /登录|请登录|扫码|unauthorized|not authorized|未授权/i.test(message || textPreview);
        const permissionDenied = /无权|权限|不是成员|加入星球|会员|购买|续费|已过期|forbidden/i.test(message || textPreview);
        const antiCrawl = String(code || "") === "1059" || /非官方工具|official Skill|garden[.]zsxq[.]com[/]skill/i.test(text);
        return {{
          ok: !!data.succeeded,
          rateLimited: rateLimited || antiCrawl,
          status: r.status,
          code: String(code || ""),
          message: String(message || ""),
          authRequired,
          permissionDenied,
          text: textPreview,
          attempts: [{{
            url: 'https://api.zsxq.com/v2/topics/{topic_id}/info',
            status: r.status,
            ok: !!data.succeeded,
            code: String(code || ""),
            message: String(message || ""),
            textPreview,
            authRequired,
            permissionDenied
          }}],
          topic: data && data.resp_data && data.resp_data.topic || {{}}
        }};
      }} catch (err) {{
        return {{ok: false, error: String(err), attempts: [{{url: 'https://api.zsxq.com/v2/topics/{topic_id}/info', ok: false, error: String(err)}}]}};
      }}
    }})()
    """
    retries = max(0, int(getattr(args, "rate_limit_retries", 5) if args else 5))
    result: dict[str, Any] = {}
    for attempt in range(retries + 1):
        check_stopped(args)
        throttle_request(args)
        result = cdp.evaluate(expression, timeout=45) or {}
        if result.get("rateLimited"):
            pause_for_rate_limit(args, f"topic API {topic_id}", attempt, retries)
            continue
        return result
    return result


def decode_zsxq_attr(value: str) -> str:
    return urllib.parse.unquote(html.unescape(value or "")).strip()


def zsxq_rich_text_to_markdown(text: str) -> str:
    text = text or ""

    def replace_entity(match: re.Match[str]) -> str:
        raw = match.group(1) or ""
        attrs = {name: decode_zsxq_attr(value) for name, value in re.findall(r'(\w+)="([^"]*)"', raw)}
        typ = attrs.get("type", "")
        title = attrs.get("title") or attrs.get("text") or ""
        href = attrs.get("href") or attrs.get("url") or ""
        if typ in {"web", "web_url"} and href:
            return f"[{title or href}]({href})"
        if typ in {"text_bold", "bold"}:
            return f"**{title}**" if title else ""
        if typ in {"text_italic", "italic"}:
            return f"*{title}*" if title else ""
        if typ in {"text_strikethrough", "strikethrough"}:
            return f"~~{title}~~" if title else ""
        if typ in {"text_underline", "underline"}:
            return title
        if typ in {"hashtag", "tag"}:
            return title
        if typ == "mention":
            return title
        if href:
            return f"[{title or href}]({href})"
        return title

    text = re.sub(r"<e\b([^>]*)/>", replace_entity, text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def topic_source(topic: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    for key in ("task", "talk", "question", "solution", "answer"):
        value = topic.get(key)
        if isinstance(value, dict) and value:
            return key, value
    return str(topic.get("type") or "topic"), {}


def topic_has_exportable_content(topic: dict[str, Any]) -> bool:
    if not isinstance(topic, dict) or not topic:
        return False
    source_type, primary = topic_source(topic)
    if not source_type or not isinstance(primary, dict) or not primary:
        return False
    if str(primary.get("text") or "").strip():
        return True
    if primary.get("images") or primary.get("files") or primary.get("video"):
        return True
    article = primary.get("article") if isinstance(primary.get("article"), dict) else {}
    if article.get("article_url") or article.get("inline_article_url"):
        return True
    for key in ("question", "answer", "solution"):
        value = topic.get(key)
        if isinstance(value, dict) and str(value.get("text") or "").strip():
            return True
    return False


def image_urls_from_sources(*sources: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for source in sources:
        for image in source.get("images") or []:
            if not isinstance(image, dict):
                continue
            for key in ("original", "large", "thumbnail"):
                value = image.get(key) or {}
                url = value.get("url") if isinstance(value, dict) else ""
                if url:
                    urls.append(url)
                    break
    return list(dict.fromkeys(urls))


def files_from_sources(*sources: dict[str, Any]) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for source in sources:
        for file_item in source.get("files") or []:
            if not isinstance(file_item, dict):
                continue
            name = str(
                file_item.get("name")
                or file_item.get("title")
                or file_item.get("file_name")
                or file_item.get("filename")
                or "知识星球文件"
            ).strip()
            url = str(
                file_item.get("download_url")
                or file_item.get("url")
                or file_item.get("file_url")
                or file_item.get("href")
                or ""
            ).strip()
            files.append(
                {
                    "name": name,
                    "url": url,
                    "size": str(file_item.get("size") or ""),
                    "downloadCount": str(file_item.get("download_count") or ""),
                }
            )
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for file_item in files:
        key = file_item.get("url") or file_item.get("name") or ""
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(file_item)
    return unique


def format_zsxq_size(value: Any) -> str:
    try:
        size = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.2f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def topic_owner_name(topic: dict[str, Any], primary: dict[str, Any]) -> str:
    for owner in (primary.get("owner"), topic.get("owner")):
        if isinstance(owner, dict):
            name = str(owner.get("name") or owner.get("alias") or "").strip()
            if name:
                return name
    return ""


def topic_meta_line(topic: dict[str, Any], primary: dict[str, Any]) -> str:
    parts: list[str] = []
    owner = topic_owner_name(topic, primary)
    if owner:
        parts.append(owner)
    if topic.get("create_time"):
        parts.append(str(topic.get("create_time")))
    if topic.get("likes_count"):
        parts.append(f"赞 {topic.get('likes_count')}")
    if topic.get("comments_count"):
        parts.append(f"评论 {topic.get('comments_count')}")
    if topic.get("readers_count") or topic.get("reading_count"):
        parts.append(f"阅读 {topic.get('readers_count') or topic.get('reading_count')}")
    return " · ".join(parts)


def markdown_links(markdown: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for match in re.finditer(r"\[([^\]]{0,200})\]\((https?://[^)\s]+)\)", markdown):
        links.append({"text": match.group(1) or match.group(2), "href": match.group(2)})
    for match in re.finditer(r"(?<!\()https?://(?:t\.zsxq\.com|wx\.zsxq\.com|articles\.zsxq\.com)/[^\s)>\]]+", markdown):
        href = match.group(0).rstrip(".,;，。；")
        links.append({"text": href, "href": href})
    return unique_zsxq_links(links)


def comments_from_zsxq_items(items: list[Any]) -> list[dict[str, str]]:
    comments: list[dict[str, str]] = []

    def append_item(item: Any, parent_author: str = "") -> None:
        if not isinstance(item, dict):
            return
        text = zsxq_rich_text_to_markdown(str(item.get("text") or "")).strip()
        owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
        repliee = item.get("repliee") if isinstance(item.get("repliee"), dict) else {}
        author = str(owner.get("name") or "").strip()
        if repliee.get("name"):
            author = f"{author} 回复 {repliee.get('name')}".strip()
        elif parent_author and author:
            author = f"{author} 回复 {parent_author}".strip()
        if text:
            comments.append(
                {
                    "author": author,
                    "time": str(item.get("create_time") or ""),
                    "text": text,
                }
            )
        for reply in item.get("replied_comments") or []:
            append_item(reply, author or parent_author)

    for item in items or []:
        append_item(item)
    return comments


def comments_from_topic_api(topic: dict[str, Any]) -> list[dict[str, str]]:
    return comments_from_zsxq_items(topic.get("show_comments") or [])


def content_from_topic_api(
    topic: dict[str, Any],
    source: dict[str, Any],
    args: argparse.Namespace | None = None,
    comments: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    source_type, primary = topic_source(topic)
    section_sources: list[tuple[str, dict[str, Any]]] = []
    if source_type in {"question", "answer", "solution", "q&a"} or isinstance(topic.get("question"), dict):
        question = topic.get("question") if isinstance(topic.get("question"), dict) else {}
        answer = topic.get("answer") if isinstance(topic.get("answer"), dict) else {}
        solution = topic.get("solution") if isinstance(topic.get("solution"), dict) else {}
        if question:
            section_sources.append(("问题", question))
        if answer:
            section_sources.append(("回答", answer))
        elif solution:
            section_sources.append(("回答", solution))
    if not section_sources:
        section_sources.append(("", primary))
    extra_sources = [part for _label, part in section_sources if part]

    body_parts: list[str] = []
    for label, part in section_sources:
        text = zsxq_rich_text_to_markdown(str(part.get("text") or "")).strip()
        if not text:
            continue
        if label:
            body_parts.append(f"## {label}\n\n{text}")
        else:
            body_parts.append(text)
    body = "\n\n".join(body_parts).strip()
    title = str(topic.get("title") or source.get("title") or "").strip()
    if not title:
        title = re.sub(r"\s+", " ", body).strip()[:80] or "知识星球文档"

    image_urls = image_urls_from_sources(*extra_sources)
    file_items = files_from_sources(*extra_sources)

    article_url = ""
    for part in extra_sources:
        article = part.get("article") if isinstance(part.get("article"), dict) else {}
        article_url = article.get("article_url") or article.get("inline_article_url") or article_url

    lines: list[str] = [f"# {title}", ""]
    meta = topic_meta_line(topic, primary)
    if meta:
        lines.extend([f"> {meta}", ""])
    if body:
        lines.extend([body, ""])
    if article_url:
        article_title = ""
        for part in extra_sources:
            article = part.get("article") if isinstance(part.get("article"), dict) else {}
            article_title = article.get("title") or article_title
        lines.extend([f"> 完整文章：[{article_title or article_url}]({article_url})", ""])
    if image_urls:
        lines.extend(["## 图片", ""])
        for index, url in enumerate(image_urls, 1):
            lines.extend([f"![图片 {index}]({url})", ""])
    if file_items:
        lines.extend(["## 附件", ""])
        for file_item in file_items:
            name = file_item.get("name") or "知识星球文件"
            url = file_item.get("url") or ""
            extra = []
            size_text = format_zsxq_size(file_item.get("size"))
            if size_text:
                extra.append(size_text)
            if file_item.get("downloadCount"):
                extra.append(f"下载 {file_item.get('downloadCount')} 次")
            label = f"{name}（{' · '.join(extra)}）" if extra else name
            lines.append(f"- [{label}]({url})" if url else f"- {label}")
        lines.append("")
    markdown = "\n".join(lines).rstrip() + "\n"

    topic_id = str(topic.get("topic_id") or topic.get("topic_uid") or source.get("topicId") or source.get("topicUid") or "")
    topic_url = f"https://wx.zsxq.com/topic/{topic_id}" if topic_id else ""
    content: dict[str, Any] = {
        "title": title,
        "markdown": markdown,
        "images": image_urls,
        "files": file_items,
        "zsxqLinks": markdown_links(markdown),
        "sourceType": f"topic-api:{source_type}",
        "shortUrl": "",
        "articleUrl": article_url,
        "topicUrl": topic_url,
        "sourceText": source.get("title") or title,
        "tocKey": source.get("key") or "",
        "tocGroup": source.get("groupTitle") or "",
        "tocTitle": source.get("title") or title,
        "topicId": topic_id,
        "topicUid": topic_id,
    }
    if getattr(args, "include_comments", False):
        attach_comments_to_content(content, comments if comments is not None else comments_from_topic_api(topic), True)
    return content


def fetch_topic_comments_api(
    cdp: CDPClient,
    topic_id: str,
    args: argparse.Namespace | None = None,
) -> list[dict[str, str]]:
    if not topic_id or not getattr(args, "include_comments", False):
        return []
    expression = f"""
    (async () => {{
      const topicId = {json.dumps(topic_id)};
      const makeUrl = (base) => {{
        const url = new URL(base);
        url.searchParams.set("count", "{DEFAULT_COMMENT_BATCH_SIZE}");
        url.searchParams.set("sort", "asc");
        url.searchParams.set("sort_type", "by_create_time");
        url.searchParams.set("with_sticky", "true");
        return url.toString();
      }};
      const urls = [
        makeUrl(`https://api.zsxq.com/v2/topics/${{topicId}}/comments`),
        makeUrl(`https://api.zsxq.com/v1.10/topics/${{topicId}}/comments`)
      ];
      const parseZsxqJson = (raw) => JSON.parse(raw.replace(
        /("(?:topic_id|topic_uid|group_id|user_id|uid|file_id|comment_id|column_id|image_id|video_id|owner_user_id|repliee_user_id)"\\s*:\\s*)(\\d{{15,}})/g,
        '$1"$2"'
      ));
      const attempts = [];
      for (const url of urls) {{
        try {{
          const r = await fetch(url, {{
            credentials: "include",
            headers: {{accept: "application/json, text/plain, */*"}}
          }});
          const text = await r.text();
          const rateLimited = r.status === 429 || /Too Many Requests|请求过于频繁|请求太频繁|访问过于频繁/i.test(text);
          let data = {{}};
          try {{ data = parseZsxqJson(text); }} catch (err) {{}}
          const antiCrawl = String(data && data.code || "") === "1059" || /非官方工具|official Skill|garden[.]zsxq[.]com[/]skill/i.test(text);
          const comments = data && data.resp_data && Array.isArray(data.resp_data.comments) ? data.resp_data.comments : [];
          attempts.push({{url, status: r.status, ok: !!data.succeeded, commentCount: comments.length, message: data && data.msg || ""}});
          if (rateLimited || antiCrawl) return {{ok: false, rateLimited: true, status: r.status, text: text.slice(0, 240), attempts}};
          if (data && data.succeeded && Array.isArray(data.resp_data && data.resp_data.comments)) {{
            return {{ok: true, status: r.status, comments, attempts}};
          }}
        }} catch (err) {{
          attempts.push({{url, ok: false, error: String(err)}});
        }}
      }}
      return {{ok: false, attempts}};
    }})()
    """
    retries = max(0, int(getattr(args, "rate_limit_retries", 5) if args else 5))
    result: dict[str, Any] = {}
    for attempt in range(retries + 1):
        check_stopped(args)
        throttle_comment_request(args)
        result = cdp.evaluate(expression, timeout=45) or {}
        if result.get("rateLimited"):
            pause_for_rate_limit(args, f"topic comments API {topic_id}", attempt, retries)
            continue
        break
    if not result.get("ok"):
        return []
    return comments_from_zsxq_items(result.get("comments") or [])


def collect_article_content(
    cdp: CDPClient,
    article_url: str,
    fallback_title: str,
    args: argparse.Namespace | None = None,
) -> dict[str, Any]:
    retries = max(0, int(getattr(args, "rate_limit_retries", 5) if args else 5))
    for attempt in range(retries + 1):
        check_stopped(args)
        navigate_with_retry(cdp, article_url, args)
        wait_eval(
            cdp,
            "({href: location.href, ok: !!document.querySelector('.content.ql-editor'), len: (document.body && document.body.innerText || '').length})",
            lambda value: bool(value.get("ok")) or value.get("len", 0) > 1000,
            timeout=35,
            args=args,
        )
        if detect_rate_limited_page(cdp).get("limited"):
            pause_for_rate_limit(args, article_url, attempt, retries)
            continue
        content = cdp.evaluate(
            f"({ZSXQ_CONVERTER_JS})({js_string(fallback_title or '知识星球文章')}, {js_string('.content.ql-editor')})",
            timeout=90,
        )
        content = ensure_converter_content(content, fallback_title or article_url)
        if content_is_rate_limited(content):
            pause_for_rate_limit(args, article_url, attempt, retries)
            continue
        content["sourceType"] = "article"
        content["articleUrl"] = cdp.evaluate("location.href", timeout=10) or article_url
        return content
    raise ExportError(f"知识星球文章页触发频率限制，重试仍失败：{article_url}")


def resolve_toc_item_api(cdp: CDPClient, source: dict[str, Any], args: argparse.Namespace | None = None) -> dict[str, Any]:
    topic_id = str(source.get("topicId") or source.get("topicUid") or "").strip()
    if not topic_id:
        raise ExportError("目录条目缺少 topicId，无法走 API")
    raw_topic = source.get("rawTopic") if isinstance(source.get("rawTopic"), dict) else {}
    topic = raw_topic if topic_has_exportable_content(raw_topic) else {}
    if not topic:
        ensure_zsxq_api_origin(cdp, args, str(source.get("entryUrl") or source.get("topicUrl") or "https://wx.zsxq.com/"))
        result = fetch_topic_info_api(cdp, topic_id, args)
        if result.get("rateLimited"):
            raise ExportError(f"目录条目触发请求频率限制：{source.get('title') or topic_id}")
        if not result.get("ok"):
            raise ExportError(summarize_zsxq_api_failure(result, f"topic API 读取失败：{topic_id}"))
        topic = result.get("topic") or {}
    source_type, primary = topic_source(topic)
    has_video = any(bool(part.get("video")) for part in [primary])
    has_article = any(bool((part.get("article") or {}).get("article_url") or (part.get("article") or {}).get("inline_article_url")) for part in [primary])
    if has_video and not has_article and getattr(args, "skip_video_topics", True):
        raise SkipDocument("video-topic", title=source.get("title") or topic.get("title") or topic_id, href=f"https://wx.zsxq.com/topic/{topic_id}")
    comments = None
    if getattr(args, "include_comments", False):
        comments = comments_from_topic_api(topic)
        if getattr(args, "fetch_full_comments", False):
            ensure_zsxq_api_origin(cdp, args, str(source.get("entryUrl") or source.get("topicUrl") or "https://wx.zsxq.com/"))
            comments = fetch_topic_comments_api(cdp, topic_id, args) or comments
    topic_content = content_from_topic_api(topic, source, args, comments=comments)
    article_url = topic_content.get("articleUrl") or ""
    if article_url:
        try:
            article_content = collect_article_content(cdp, article_url, topic_content.get("title") or source.get("title") or "", args)
            attach_comments_to_content(article_content, comments or [], bool(getattr(args, "include_comments", False)))
            article_content["shortUrl"] = ""
            article_content["topicUrl"] = topic_content.get("topicUrl") or f"https://wx.zsxq.com/topic/{topic_id}"
            article_content["topicId"] = topic_id
            article_content["topicUid"] = topic_id
            article_content["sourceText"] = source.get("title") or topic_content.get("title") or topic_id
            article_content["tocKey"] = source.get("key") or ""
            article_content["tocGroup"] = source.get("groupTitle") or ""
            article_content["tocTitle"] = source.get("title") or topic_content.get("title") or ""
            return article_content
        except (ExportStopped, SkipDocument):
            raise
        except Exception as exc:
            emit(args, f"知识星球文章页读取失败，保留主题摘要：{source.get('title') or topic_id} ({exc})", event="log.message", level="warn")
    return topic_content



def current_page_has_video(cdp: CDPClient) -> dict[str, Any]:
    return cdp.evaluate(
        r"""(() => {
          const nodes = [...document.querySelectorAll('video, iframe[src*="video"], [class*="video"], [class*="Video"], [src*="video"]')];
          const resources = performance.getEntriesByType('resource')
            .map(e => e.name || '')
            .filter(u => /\/videos?\/|m3u8|\.mp4|video/i.test(u));
          return {
            hasVideo: nodes.length > 0,
            nodeCount: nodes.length,
            resourceCount: resources.length
          };
        })()""",
        timeout=10,
    ) or {}


def should_skip_video_topic(cdp: CDPClient, topic_url: str, args: argparse.Namespace | None = None) -> dict[str, Any] | None:
    if not getattr(args, "skip_video_topics", True):
        return None
    topic_id = topic_id_from_url(topic_url)
    api_info = inspect_topic_api(cdp, topic_id, args) if topic_id else {}
    if api_info.get("hasVideo") and not api_info.get("hasArticle"):
        return api_info
    dom_info = current_page_has_video(cdp)
    if dom_info.get("hasVideo") and not api_info.get("hasArticle"):
        merged = dict(api_info)
        merged.update(dom_info)
        return merged
    return None


def ensure_converter_content(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExportError(
            f"知识星球正文解析结果异常：{context}。页面可能改版、未加载完成或当前账号无访问权限。"
        )
    return value


def content_is_rate_limited(content: dict[str, Any]) -> bool:
    title = str(content.get("title") or "")
    markdown = str(content.get("markdown") or "")
    plain = re.sub(r"\s+", " ", f"{title}\n{markdown}").strip()
    if re.fullmatch(r"(?:HTTP\s*)?429(?:\s+Too Many Requests)?|Too Many Requests", plain, re.I):
        return True
    if re.fullmatch(r"请求过于频繁|请求太频繁|访问过于频繁", plain):
        return True
    return len(plain) <= 300 and bool(re.search(r"Too Many Requests|请求过于频繁|请求太频繁|访问过于频繁", plain, re.I))


def is_zsxq_page_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url or "")
    host = parsed.netloc.lower()
    if parsed.scheme not in {"http", "https"}:
        return False
    if host == "t.zsxq.com":
        return True
    if host == "articles.zsxq.com":
        return parsed.path.endswith(".html") or bool(parsed.path.strip("/"))
    if host == "wx.zsxq.com":
        return True
    return False


def is_zsxq_article_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url or "")
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == "articles.zsxq.com"


def should_follow_zsxq_link(link: dict[str, Any], args: argparse.Namespace | None = None) -> bool:
    scope = str(getattr(args, "follow_link_scope", "all") if args else "all").strip() or "all"
    href = str(link.get("href") or "").strip()
    if scope == "none":
        return False
    if scope == "articles":
        return is_zsxq_article_url(href)
    return True


def filter_follow_zsxq_links(links: list[dict[str, Any]], args: argparse.Namespace | None = None) -> list[dict[str, Any]]:
    return [link for link in unique_zsxq_links(links) if should_follow_zsxq_link(link, args)]


def resolve_link(cdp: CDPClient, link: dict[str, str], args: argparse.Namespace | None = None) -> dict[str, Any]:
    href = link["href"]
    if not is_zsxq_page_url(href):
        raise SkipDocument("non-zsxq-page-link", title=link.get("text") or href, href=href)
    retries = max(0, int(getattr(args, "rate_limit_retries", 5) if args else 5))
    for attempt in range(retries + 1):
        navigate_with_retry(cdp, href, args)
        current_url = cdp.evaluate("location.href", timeout=10)
        if not is_zsxq_page_url(current_url):
            raise SkipDocument("non-zsxq-page-link", title=link.get("text") or href, href=current_url or href)
        current_host = urllib.parse.urlparse(current_url or "").netloc.lower()
        if current_host == "articles.zsxq.com":
            info = {"href": current_url, "article": ""}
        else:
            info = find_article_url_on_topic(cdp, args)
        if detect_rate_limited_page(cdp).get("limited"):
            pause_for_rate_limit(args, href, attempt, retries)
            continue
        topic_url = info.get("href") or href
        topic_id = topic_id_from_url(topic_url)
        if topic_id:
            api_source = {
                "key": link.get("href") or topic_url,
                "title": link.get("text") or "知识星球帖子",
                "topicId": topic_id,
                "topicUid": topic_id,
                "topicUrl": topic_url,
                "groupTitle": "",
            }
            try:
                content = resolve_toc_item_api(cdp, api_source, args)
                content["shortUrl"] = href
                content["topicUrl"] = content.get("topicUrl") or topic_url
                content["sourceText"] = link.get("text") or href
                return content
            except (ExportStopped, SkipDocument):
                raise
            except Exception as exc:
                emit(
                    args,
                    f"知识星球 topic API 读取失败，改用页面兜底：{link.get('text') or href} ({exc})",
                    event="log.message",
                    level="warn",
                )
        article_url = info.get("article") or (topic_url if is_zsxq_article_url(topic_url) else "")
        topic_comments = collect_current_comments(cdp, args) if article_url else []
        if article_url:
            navigate_with_retry(cdp, article_url, args)
            wait_eval(
                cdp,
                "({href: location.href, ok: !!document.querySelector('.content.ql-editor'), len: (document.body && document.body.innerText || '').length})",
                lambda value: bool(value.get("ok")) or value.get("len", 0) > 1000,
                timeout=35,
                args=args,
            )
            if detect_rate_limited_page(cdp).get("limited"):
                pause_for_rate_limit(args, article_url, attempt, retries)
                continue
            content = cdp.evaluate(
                f"({ZSXQ_CONVERTER_JS})({js_string(link.get('text') or '知识星球文章')}, {js_string('.content.ql-editor')})",
                timeout=90,
            )
            content = ensure_converter_content(content, link.get("text") or href)
            if content_is_rate_limited(content):
                pause_for_rate_limit(args, article_url, attempt, retries)
                continue
            attach_comments_to_content(content, topic_comments, bool(getattr(args, "include_comments", False)))
            content["sourceType"] = "article"
            content["articleUrl"] = cdp.evaluate("location.href", timeout=10)
            content["topicUrl"] = topic_url
        else:
            video_info = should_skip_video_topic(cdp, topic_url, args)
            if video_info:
                raise SkipDocument(
                    "video-topic",
                    title=link.get("text") or video_info.get("title") or topic_url,
                    href=topic_url,
                )
            expand_current_content(cdp, args)
            content = cdp.evaluate(
                f"({ZSXQ_CONVERTER_JS})({js_string(link.get('text') or '知识星球帖子')}, {js_string('.talk-content-container, .answer-content-container')})",
                timeout=60,
            )
            content = ensure_converter_content(content, link.get("text") or href)
            if content_is_rate_limited(content):
                pause_for_rate_limit(args, topic_url, attempt, retries)
                continue
            comments = collect_current_comments(cdp, args)
            attach_comments_to_content(content, comments, bool(getattr(args, "include_comments", False)))
            content["sourceType"] = "topic"
            content["articleUrl"] = ""
            content["topicUrl"] = topic_url
        content["shortUrl"] = href
        content["sourceText"] = link.get("text") or href
        return content
    raise ExportError(f"请求过于频繁，重试仍失败：{href}")


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


def download_image(url: str, dest_dir: Path, timeout: int) -> Path:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://wx.zsxq.com/"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
        ext = guess_extension(url, response.headers.get("Content-Type"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / (hashlib.sha1(url.encode("utf-8")).hexdigest()[:12] + "." + ext)
    if not target.exists():
        target.write_bytes(data)
    return target


def guess_file_extension(url: str, content_type: str | None) -> str:
    path = urllib.parse.urlparse(url).path.lower()
    match = re.search(r"\.([a-z0-9]{2,8})$", path)
    if match:
        return match.group(1)
    content_type = (content_type or "").lower()
    if "pdf" in content_type:
        return "pdf"
    if "zip" in content_type:
        return "zip"
    if "word" in content_type or "docx" in content_type:
        return "docx"
    if "excel" in content_type or "spreadsheet" in content_type or "xlsx" in content_type:
        return "xlsx"
    if "powerpoint" in content_type or "presentation" in content_type or "pptx" in content_type:
        return "pptx"
    if "text/" in content_type:
        return "txt"
    return "bin"


def download_file(url: str, filename: str, dest_dir: Path, timeout: int) -> Path:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://wx.zsxq.com/"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
        ext = guess_file_extension(url, response.headers.get("Content-Type"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_filename(filename or Path(urllib.parse.urlparse(url).path).name or "知识星球附件")
    if "." not in safe_name:
        safe_name = f"{safe_name}.{ext}"
    target = dest_dir / f"{hashlib.sha1(url.encode('utf-8')).hexdigest()[:12]}-{safe_name}"
    if not target.exists():
        target.write_bytes(data)
    return target


def localize_images(
    markdown: str,
    images: list[str],
    md_path: Path,
    timeout: int,
    keep_remote: bool,
    args: argparse.Namespace | None = None,
    checkpoint: WandaoCheckpoint | None = None,
    item_key: str = "",
) -> tuple[str, int, list[dict[str, str]]]:
    success = 0
    failures: list[dict[str, str]] = []
    for url in sorted(set(images)):
        check_stopped(args)
        if not url.startswith(("http://", "https://")):
            continue
        resource_key = zsxq_resource_key("image", url)
        if checkpoint and resource_key:
            checkpoint.upsert_resource(item_key, resource_key, "image", url)
        try:
            if checkpoint and resource_key and checkpoint.resource_status(resource_key) == "completed":
                record = checkpoint.resource_record(resource_key) or {}
                local_path = record.get("local_path")
                if local_path and Path(local_path).exists():
                    markdown = markdown.replace(url, os.path.relpath(Path(local_path), md_path.parent).replace("\\", "/"))
                    continue
            if checkpoint and resource_key:
                checkpoint.start_resource(resource_key)
            target = download_image(url, md_path.parent / "assets", timeout)
            markdown = markdown.replace(url, os.path.relpath(target, md_path.parent).replace("\\", "/"))
            if checkpoint and resource_key:
                checkpoint.complete_resource(resource_key, local_path=str(target))
            success += 1
        except Exception as exc:
            if checkpoint and resource_key:
                checkpoint.fail_resource(resource_key, str(exc))
            failures.append({"url": url, "error": str(exc)})
            if not keep_remote:
                markdown = markdown.replace(url, "")
    return markdown, success, failures


def localize_files(
    markdown: str,
    files: list[dict[str, str]],
    md_path: Path,
    timeout: int,
    args: argparse.Namespace | None = None,
    checkpoint: WandaoCheckpoint | None = None,
    item_key: str = "",
) -> tuple[str, int, list[dict[str, str]]]:
    success = 0
    failures: list[dict[str, str]] = []
    for file_item in files:
        check_stopped(args)
        url = str(file_item.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        resource_key = zsxq_resource_key("attachment", url)
        if checkpoint and resource_key:
            checkpoint.upsert_resource(item_key, resource_key, "attachment", url, metadata={"name": file_item.get("name", "")})
        try:
            if checkpoint and resource_key and checkpoint.resource_status(resource_key) == "completed":
                record = checkpoint.resource_record(resource_key) or {}
                local_path = record.get("local_path")
                if local_path and Path(local_path).exists():
                    markdown = markdown.replace(url, os.path.relpath(Path(local_path), md_path.parent).replace("\\", "/"))
                    continue
            if checkpoint and resource_key:
                checkpoint.start_resource(resource_key)
            target = download_file(url, file_item.get("name") or "知识星球附件", md_path.parent / "assets" / "files", timeout)
            markdown = markdown.replace(url, os.path.relpath(target, md_path.parent).replace("\\", "/"))
            if checkpoint and resource_key:
                checkpoint.complete_resource(resource_key, local_path=str(target))
            success += 1
        except Exception as exc:
            if checkpoint and resource_key:
                checkpoint.fail_resource(resource_key, str(exc))
            failures.append({"url": url, "name": file_item.get("name", ""), "error": str(exc)})
    return markdown, success, failures


def scan_exported_docs(output: Path) -> dict[str, Path]:
    exported: dict[str, Path] = {}
    if not output.exists():
        return exported
    for md_file in output.rglob("*.md"):
        try:
            text = md_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pattern in SOURCE_META_PATTERNS:
            for match in pattern.finditer(text):
                for key in canonical_url_keys(match.group(1)):
                    exported[key] = md_file
    return exported


def normalize_url_for_seen(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    host = parsed.netloc.lower()
    path = re.sub(r"/+$", "", parsed.path or "/")
    query = ""
    if host == "wx.zsxq.com" and parsed.path.startswith("/columns/"):
        query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query = urllib.parse.urlencode(sorted(query_items))
    return urllib.parse.urlunparse((parsed.scheme.lower(), host, path, "", query, ""))


def canonical_url_keys(*urls: str | None) -> set[str]:
    keys: set[str] = set()
    for url in urls:
        if not url:
            continue
        raw = str(url).strip()
        if not raw:
            continue
        keys.add(raw)
        normalized = normalize_url_for_seen(raw)
        if normalized:
            keys.add(normalized)
    return keys


def zsxq_item_key_from_topic_id(topic_id: str) -> str:
    topic_id = str(topic_id or "").strip()
    return f"zsxq:topic:{topic_id}" if topic_id else ""


def zsxq_item_key_from_source(source: dict[str, Any]) -> str:
    topic_id = str(source.get("topicId") or source.get("topicUid") or "").strip()
    if topic_id:
        return zsxq_item_key_from_topic_id(topic_id)
    for value in (source.get("topicUrl"), source.get("articleUrl"), source.get("href"), source.get("key")):
        text = str(value or "").strip()
        if text:
            return f"zsxq:source:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
    return ""


def zsxq_resource_key(resource_type: str, source: str) -> str:
    source = str(source or "").strip()
    if not source:
        return ""
    return f"{resource_type}:{hashlib.sha256(source.encode('utf-8')).hexdigest()}"


def item_seen(item: dict[str, Any], seen_urls: set[str]) -> bool:
    keys = canonical_url_keys(item.get("topicUrl"), item.get("articleUrl"))
    return bool(keys & seen_urls)


def mark_item_seen(item: dict[str, Any], seen_urls: set[str]) -> None:
    seen_urls.update(canonical_url_keys(item.get("shortUrl"), item.get("topicUrl"), item.get("articleUrl")))


def link_seen(link: dict[str, str], seen_urls: set[str]) -> bool:
    return bool(canonical_url_keys(link.get("href")) & seen_urls)


def mark_link_seen(link: dict[str, str], seen_urls: set[str]) -> None:
    seen_urls.update(canonical_url_keys(link.get("href")))


CODE_TOOLBAR_LANGS = [
    "typescript",
    "javascript",
    "markdown",
    "plaintext",
    "objectivec",
    "csharp",
    "python",
    "shell",
    "bash",
    "json",
    "html",
    "java",
    "rust",
    "ruby",
    "yaml",
    "yml",
    "css",
    "xml",
    "sql",
    "php",
    "cpp",
    "go",
    "c",
    "text",
]


def is_code_toolbar_line(line: str) -> bool:
    compacted = re.sub(r"\s+", "", html.unescape(line or "").strip().lower())
    if not compacted.endswith("copy"):
        return False
    rest = compacted[:-4]
    if len(rest) < 12 or len(rest) > 240:
        return False
    hits = 0
    while rest:
        matched = ""
        for lang in CODE_TOOLBAR_LANGS:
            if rest.startswith(lang):
                matched = lang
                break
        if not matched:
            return False
        rest = rest[len(matched) :]
        hits += 1
    return hits >= 3


def strip_code_toolbar_lines(markdown: str) -> str:
    lines = (markdown or "").splitlines()
    result: list[str] = []
    in_fence = False
    for index, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            result.append(line)
            continue
        if is_code_toolbar_line(line):
            next_index = index + 1
            while next_index < len(lines) and not lines[next_index].strip():
                next_index += 1
            before_fence = next_index < len(lines) and lines[next_index].lstrip().startswith("```")
            if in_fence or before_fence:
                continue
        result.append(line)
    suffix = "\n" if markdown.endswith("\n") else ""
    return "\n".join(result) + suffix


def append_source_meta(markdown: str, item: dict[str, Any]) -> str:
    markdown = strip_code_toolbar_lines(markdown or "")
    return (
        markdown
        + "\n---\n\n"
        + (f"知识星球目录键: {item.get('tocKey') or ''}\n" if item.get("tocKey") else "")
        + (f"知识星球目录分组: {item.get('tocGroup') or ''}\n" if item.get("tocGroup") else "")
        + (f"知识星球目录标题: {item.get('tocTitle') or ''}\n" if item.get("tocTitle") else "")
        + f"知识星球短链: {item.get('shortUrl') or ''}\n"
        + f"知识星球帖子页: {item.get('topicUrl') or ''}\n"
        + f"知识星球文章页: {item.get('articleUrl') or ''}\n"
        + f"知识星球页面类型: {item.get('sourceType') or ''}\n"
        + (
            f"知识星球评论区导出: {'true' if item.get('commentsIncluded') else 'false'}\n"
            if "commentsIncluded" in item
            else ""
        )
        + (f"知识星球评论数: {item.get('commentCount')}\n" if "commentCount" in item else "")
    )


def write_index(output: Path, title: str, rows: list[dict[str, Any]]) -> None:
    index_path = output / "00-知识星球入口.md"
    lines = [f"# {title or '知识星球导出'}", "", "> 从知识星球导出。", ""]
    for row in rows:
        rel = os.path.relpath(Path(row["path"]), index_path.parent).replace("\\", "/")
        lines.append(f"- [{row['title']}]({rel})")
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


LOCAL_LINK_RE = re.compile(r"(?P<prefix>\]\()(?P<url>https://(?:t|wx|articles)\.zsxq\.com[^)\s]+)(?P<suffix>\))")
REMOTE_MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]{0,200})\]\((https://(?:t|wx|articles)\.zsxq\.com[^)\s]+)\)")
SOURCE_META_PATTERNS = [
    re.compile(r"知识星球目录键:\s*(\S+)"),
    re.compile(r"知识星球入口:\s*(\S+)"),
    re.compile(r"知识星球短链:\s*(\S+)"),
    re.compile(r"知识星球文章页:\s*(https?://\S+)"),
    re.compile(r"知识星球帖子页:\s*(https?://\S+)"),
]


def source_meta_start(text: str) -> int:
    markers = [
        "\n---\n\n知识星球",
        "\n---\r\n\r\n知识星球",
        "\r\n---\r\n\r\n知识星球",
    ]
    positions = [text.find(marker) for marker in markers if text.find(marker) >= 0]
    return min(positions) if positions else len(text)


def markdown_comments_attempted(md_path: Path) -> bool:
    try:
        text = md_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    return "知识星球评论区导出: true" in text or bool(re.search(r"^##\s+评论区\s*$", text, re.M))


def should_update_existing_for_comments(args: argparse.Namespace, md_path: Path) -> bool:
    return bool(getattr(args, "include_comments", False)) and not markdown_comments_attempted(md_path)


def markdown_title_from_text(text: str, fallback: str = "") -> str:
    match = re.search(r"^\s*#\s+(.+?)\s*$", text, re.M)
    return sanitize_filename(match.group(1)) if match else sanitize_filename(fallback or "知识星球文档")


def extract_remote_zsxq_links_from_markdown(md_path: Path) -> list[dict[str, str]]:
    try:
        text = md_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    body = text[: source_meta_start(text)]
    links: list[dict[str, str]] = []
    for match in REMOTE_MARKDOWN_LINK_RE.finditer(body):
        label = re.sub(r"\s+", " ", match.group(1) or "").strip()
        href = (match.group(2) or "").strip().rstrip(".,;，。；")
        if href:
            links.append({"text": label or href, "href": href})
    return unique_zsxq_links(links)


def source_keys_from_markdown(md_path: Path) -> set[str]:
    try:
        text = md_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return set()
    keys: set[str] = set()
    for pattern in SOURCE_META_PATTERNS:
        for match in pattern.finditer(text):
            keys.update(canonical_url_keys(match.group(1)))
    return keys


def rewrite_local_zsxq_links(output: Path) -> int:
    exported = scan_exported_docs(output)
    changed = 0
    for md_file in output.rglob("*.md"):
        try:
            text = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        def replace(match: re.Match[str]) -> str:
            url = match.group("url")
            target = next((exported[key] for key in canonical_url_keys(url) if key in exported), None)
            if not target or target.resolve() == md_file.resolve():
                return match.group(0)
            rel = os.path.relpath(target, md_file.parent).replace("\\", "/")
            return f"{match.group('prefix')}{rel}{match.group('suffix')}"

        new_text = LOCAL_LINK_RE.sub(replace, text)
        if new_text != text:
            md_file.write_text(new_text, encoding="utf-8")
            changed += 1
    return changed


def unique_zsxq_links(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for link in links:
        href = str(link.get("href") or "").strip()
        if not href or not is_zsxq_page_url(href):
            continue
        keys = canonical_url_keys(href)
        if keys & seen:
            continue
        seen.update(keys)
        unique.append(link)
    return unique


def export_entry(args: argparse.Namespace) -> dict[str, Any]:
    entry_url = normalize_entry_url(args.entry_url)
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    checkpoint: WandaoCheckpoint | None = None
    checkpoint_file = str(getattr(args, "checkpoint_file", "") or "").strip()
    if checkpoint_file:
        checkpoint_path = Path(checkpoint_file).expanduser().resolve()
        if getattr(args, "reset_checkpoint", False) and checkpoint_path.exists():
            checkpoint_path.unlink()
        checkpoint = WandaoCheckpoint.open(
            checkpoint_path,
            task_id=str(getattr(args, "checkpoint_task_id", "") or "default"),
            provider_id="zsxq",
            action="export",
        )
    args.folder_link_threshold = max(0, int(getattr(args, "folder_link_threshold", 9) or 0))
    args.skip_video_topics = bool(getattr(args, "skip_video_topics", True))
    args.include_comments = bool(getattr(args, "include_comments", False))
    args.fetch_full_comments = bool(getattr(args, "fetch_full_comments", False))
    args.download_files = bool(getattr(args, "download_files", False))
    args.request_delay = max(0.0, float(getattr(args, "request_delay", 1.5) or 0))
    args.request_jitter = max(0.0, float(getattr(args, "request_jitter", 0.6) or 0))
    args.comment_request_delay = max(0.0, float(getattr(args, "comment_request_delay", 3.0) or 0))
    args.comment_request_jitter = max(0.0, float(getattr(args, "comment_request_jitter", 2.0) or 0))
    args.rate_limit_pause = max(5.0, float(getattr(args, "rate_limit_pause", 90) or 90))
    args.rate_limit_retries = max(0, int(getattr(args, "rate_limit_retries", 5) or 0))
    args.max_depth = max(0, int(getattr(args, "max_depth", 2) or 0))
    args.group_page_delay = max(0.0, float(getattr(args, "group_page_delay", 4.0) or 0))
    args.group_page_jitter = max(0.0, float(getattr(args, "group_page_jitter", 4.0) or 0))
    args.long_sleep_after = max(0, int(getattr(args, "long_sleep_after", 25) or 0))
    args.long_sleep_every = max(0, int(getattr(args, "long_sleep_every", 12) or 0))
    args.long_sleep_min = max(0.0, float(getattr(args, "long_sleep_min", 120) or 0))
    args.long_sleep_max = max(args.long_sleep_min, float(getattr(args, "long_sleep_max", 300) or 0))
    if getattr(args, "follow_link_scope", "all") not in {"all", "articles", "none"}:
        args.follow_link_scope = "all"
    cdp, chrome_proc = connect_browser(args, entry_url)
    try:
        auth_file = auth_path_from_args(args)
        if auth_file.exists() and not args.skip_auth_load:
            cookie_count = load_auth_state(cdp, auth_file)
            emit(args, f"Loaded {cookie_count} auth cookies from {auth_file}")
            navigate_with_retry(cdp, entry_url, args)
            time.sleep(2)

        emit(args, "Chrome page is ready. If login is required, finish login in Chrome.")
        if args.wait_login:
            input("Press Enter after the ZSXQ page is logged in and visible...")

        toc: dict[str, Any] = {}
        toc_items: list[dict[str, Any]] = []
        toc_mode = getattr(args, "toc_mode", "auto")
        use_toc = False
        use_group_topics = False
        group_id = ""
        group_scope = ""
        group_page_size = DEFAULT_GROUP_BATCH_SIZE
        group_max_pages = 0
        if toc_mode != "off" and (is_column_entry_url(entry_url) or is_group_entry_url(entry_url)):
            try:
                use_group_topics = is_group_entry_url(entry_url)
                if use_group_topics:
                    args.limit = normalize_group_limit(args)
                    warn_large_group_export(args, args.limit)
                    group_id = group_id_from_url(entry_url)
                    if not group_id:
                        raise ExportError("无法从知识星球 group URL 中识别星球 ID")
                    group_scope = group_scope_from_args(entry_url, args)
                    group_page_size = normalize_group_page_size(args)
                    group_max_pages = normalize_group_max_pages(args, args.limit, group_page_size)
                    if checkpoint:
                        checkpoint.start_task(
                            {
                                "source": entry_url,
                                "outputDir": str(output),
                                "groupId": group_id,
                                "groupScope": group_scope,
                                "limit": args.limit,
                                "pageSize": group_page_size,
                                "resume": bool(getattr(args, "resume", False)),
                            }
                        )
                    navigate_with_retry(cdp, entry_url, args)
                    toc = {
                        "href": entry_url,
                        "title": f"知识星球 {group_scope_title(group_scope)}",
                        "groupId": group_id,
                        "scope": group_scope,
                        "pageCount": 0,
                        "totalTopics": 0,
                        "groups": [],
                    }
                    use_toc = True
                    emit(
                        args,
                        f"知识星球 Group 将按批次导出：总数最多 {args.limit} 条，每批最多读取 {group_page_size} 条。",
                        event="log.message",
                        level="info",
                    )
                else:
                    toc = collect_toc(cdp, entry_url, args)
                    toc_items = select_toc_items(toc, args)
                    use_toc = bool(toc_items)
                    emit(
                        args,
                        f"目录读取完成：groups={len(toc.get('groups') or [])} total_topics={toc.get('totalTopics', 0)} selected={len(toc_items)}",
                    )
                if toc_mode == "toc" and not use_group_topics and not toc_items:
                    raise ExportError("目录已读取，但没有匹配的导出条目。")
            except Exception as exc:
                if toc_mode == "toc":
                    raise
                emit(args, f"目录读取失败，改用正文链接导出：{exc}")
                toc = {}
                toc_items = []
                use_toc = False
                use_group_topics = False

        include_overview = bool(args.include_overview) and not use_group_topics
        if use_toc and not include_overview:
            entry = {
                "title": toc.get("title") or "知识星球导出",
                "url": toc.get("href") or entry_url,
                "zsxqLinks": [],
                "images": [],
                "markdown": "",
            }
            links: list[dict[str, str]] = []
        else:
            entry = collect_entry_links(cdp, entry_url, args.link_pattern, args)
            links = filter_follow_zsxq_links(entry.get("zsxqLinks") or [], args)
            if args.limit and args.limit > 0 and not use_toc:
                links = links[: args.limit]
        existing = scan_exported_docs(output)
        exported_rows: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        image_failures: list[dict[str, Any]] = []
        image_success = 0
        file_failures: list[dict[str, Any]] = []
        file_success = 0
        exported = 0
        skipped = 0
        skipped_video = 0
        total_comments = 0
        comments_updated_existing = 0
        stopped = False
        folderized = 0
        deepened_existing = 0
        queued_from_existing = 0
        long_sleep_count = 0
        long_sleep_seconds = 0.0
        started_at = time.time()
        # Bootstrap every run from the existing directory so a new task continues
        # numbering instead of silently restarting at 01.
        root_sequence = scan_max_export_sequence(output)
        folder_sequences: dict[str, int] = {}
        output_key = str(output.resolve()).lower()
        run_id = checkpoint.run_id if checkpoint else hashlib.sha256(
            f"zsxq:{entry_url}:{output}:{time.time_ns()}".encode("utf-8")
        ).hexdigest()[:16]
        run_exported_items: list[dict[str, Any]] = []
        run_skipped_items: list[dict[str, Any]] = []
        source_count = args.limit if use_group_topics else (len(toc_items) if use_toc else len(links))
        source_mode = "group" if use_group_topics else ("toc" if use_toc else "links")
        if checkpoint and not use_group_topics:
            checkpoint.start_task(
                {
                    "source": entry_url,
                    "outputDir": str(output),
                    "sourceMode": source_mode,
                    "totalDocs": source_count,
                    "resume": bool(getattr(args, "resume", False)),
                    "retryFailed": bool(getattr(args, "retry_failed", False)),
                }
            )
        emit(
            args,
            f"开始导出知识星球内容：共 {source_count} 个来源条目。",
            event="task.started",
            totals={"documents": source_count},
            output=str(output),
            sourceMode=source_mode,
            entryUrl=entry_url,
        )

        def allocate_sequence(base_dir: Path) -> int:
            nonlocal root_sequence
            base_dir.mkdir(parents=True, exist_ok=True)
            key = str(base_dir.resolve()).lower()
            if key == output_key:
                root_sequence += 1
                return root_sequence
            if key not in folder_sequences:
                folder_sequences[key] = scan_max_export_sequence(base_dir)
            folder_sequences[key] += 1
            return folder_sequences[key]

        def unique_path(path: Path) -> Path:
            if not path.exists():
                return path
            for i in range(2, 1000):
                candidate = path.with_name(f"{path.stem}-{i}{path.suffix}")
                if not candidate.exists():
                    return candidate
            raise ExportError(f"无法生成不冲突的文件名：{path}")

        def reusable_checkpoint_path(item_key: str) -> Path | None:
            """Reuse a previously reserved/written Markdown path for an interrupted item."""
            if not checkpoint or not item_key:
                return None
            row = checkpoint.conn.execute(
                "SELECT local_path FROM items WHERE task_id = ? AND item_key = ?",
                (checkpoint.task_id, item_key),
            ).fetchone()
            local_path = str(row["local_path"] or "").strip() if row else ""
            if not local_path:
                return None
            candidate = Path(local_path).expanduser().resolve()
            try:
                candidate.relative_to(output)
            except ValueError:
                return None
            if candidate.suffix.lower() != ".md" or not candidate.exists():
                return None
            return candidate

        def next_markdown_path(base_dir: Path, title: str) -> Path:
            index = allocate_sequence(base_dir)
            return unique_path(base_dir / f"{pad(index)}-{sanitize_filename(title)}.md")

        def next_folder_path(base_dir: Path, title: str) -> Path:
            index = allocate_sequence(base_dir)
            folder_path = base_dir / f"{pad(index)}-{sanitize_filename(title)}"
            if folder_path.exists() and folder_path.is_file():
                folder_path = unique_path(folder_path.with_suffix(".folder"))
            folder_path.mkdir(parents=True, exist_ok=True)
            return folder_path

        def report_local_path(path: Path) -> str:
            try:
                return path.resolve().relative_to(output).as_posix()
            except ValueError:
                return str(path.resolve())

        def record_exported_item(
            title: str,
            path: Path,
            source: dict[str, Any] | str,
            item_key: str = "",
            status: str = "completed",
        ) -> None:
            source_data = source if isinstance(source, dict) else {"href": str(source or "")}
            sequence = export_sequence_from_name(path.name)
            if sequence is None and path.name.startswith("00-"):
                sequence = export_sequence_from_name(path.parent.name)
            run_exported_items.append(
                {
                    "itemKey": item_key,
                    "sourceId": str(source_data.get("topicId") or source_data.get("topicUid") or ""),
                    "sourceUrl": str(
                        source_data.get("topicUrl")
                        or source_data.get("articleUrl")
                        or source_data.get("href")
                        or ""
                    ),
                    "title": title,
                    "localPath": report_local_path(path),
                    "sequence": sequence,
                    "status": status,
                }
            )

        def record_skipped_item(
            title: str,
            source: dict[str, Any] | str,
            reason: str,
            path: Path | None = None,
            item_key: str = "",
        ) -> None:
            source_data = source if isinstance(source, dict) else {"href": str(source or "")}
            run_skipped_items.append(
                {
                    "itemKey": item_key,
                    "sourceId": str(source_data.get("topicId") or source_data.get("topicUid") or ""),
                    "sourceUrl": str(
                        source_data.get("topicUrl")
                        or source_data.get("articleUrl")
                        or source_data.get("href")
                        or ""
                    ),
                    "title": title,
                    "localPath": report_local_path(path) if path else "",
                    "reason": reason,
                }
            )

        def maybe_long_sleep_after_export() -> None:
            nonlocal long_sleep_count, long_sleep_seconds
            has_more_work = bool(queue_links) or (use_group_topics and not group_exhausted)
            if not has_more_work or not should_long_sleep_after_export(args, exported):
                return
            pause = random.uniform(args.long_sleep_min, args.long_sleep_max)
            long_sleep_count += 1
            long_sleep_seconds += pause
            emit(
                args,
                f"大批量保护：已导出 {exported} 篇，长休眠 {format_duration(pause)} 后继续。",
                event="task.paused",
                level="info",
                stats={"exportedDocs": exported, "longSleepSeconds": round(pause, 1), "longSleepCount": long_sleep_count},
            )
            wait_with_stop(args, pause)
            emit(
                args,
                f"长休眠结束，继续导出：已完成 {exported} 篇。",
                event="task.resumed",
                level="info",
                stats={"exportedDocs": exported, "longSleepCount": long_sleep_count},
            )

        if include_overview:
            root_sequence = max(root_sequence, 1)
            overview_path = output / "01-专栏正文.md"
            overview_key = str(entry.get("url") or entry_url)
            overview_item_key = zsxq_item_key_from_source({"href": overview_key, "key": "overview"})
            overview_existing = next((existing[key] for key in canonical_url_keys(overview_key) if key in existing), None)
            overview_needs_comment_update = bool(
                args.incremental
                and overview_existing
                and not args.update_existing
                and should_update_existing_for_comments(args, overview_existing)
            )
            if args.incremental and overview_existing and not args.update_existing and not overview_needs_comment_update:
                if checkpoint and overview_item_key:
                    checkpoint.complete_item(overview_item_key, local_path=str(overview_existing), metadata={"source": overview_key, "skippedExisting": True})
                exported_rows.append({"title": entry.get("title") or "专栏正文", "path": str(overview_existing)})
                record_skipped_item(
                    str(entry.get("title") or "专栏正文"),
                    overview_key,
                    "existing",
                    overview_existing,
                    overview_item_key,
                )
            else:
                if checkpoint and overview_item_key:
                    checkpoint.upsert_item(
                        overview_item_key,
                        title=str(entry.get("title") or "专栏正文"),
                        source_url=overview_key,
                        source_id="",
                        metadata={"source": {"href": overview_key, "kind": "overview"}},
                    )
                    checkpoint.start_item(overview_item_key, "content")
                if overview_needs_comment_update and overview_existing:
                    overview_path = overview_existing
                    comments_updated_existing += 1
                emit(
                    args,
                    f"开始导出知识星球专栏正文：{entry.get('title') or '专栏正文'}",
                    event="document.export.started",
                    doc={"title": entry.get("title") or "专栏正文", "index": 0, "path": str(overview_path)},
                )
                markdown = entry.get("markdown") or "# 专栏正文\n"
                if args.include_comments:
                    total_comments += int(entry.get("commentCount") or 0)
                markdown += (
                    f"\n---\n\n知识星球入口: {entry.get('url') or entry_url}\n"
                    "知识星球页面类型: column\n"
                    + (
                        f"知识星球评论区导出: {'true' if entry.get('commentsIncluded') else 'false'}\n"
                        if "commentsIncluded" in entry
                        else ""
                    )
                    + (f"知识星球评论数: {entry.get('commentCount')}\n" if "commentCount" in entry else "")
                )
                markdown = strip_code_toolbar_lines(markdown)
                markdown, count, img_errors = localize_images(
                    markdown,
                    entry.get("images") or [],
                    overview_path,
                    args.download_timeout,
                    args.keep_remote_images,
                    args,
                    checkpoint,
                    overview_item_key,
                )
                image_success += count
                if img_errors:
                    image_failures.append({"document": "专栏正文", "path": str(overview_path), "failures": img_errors})
                    for failure in img_errors:
                        emit(
                            args,
                            f"知识星球图片下载失败：专栏正文：{failure.get('error') or failure.get('url') or ''}",
                            event="resource.download.failed",
                            level="error",
                            doc={"title": "专栏正文", "path": str(overview_path)},
                            resource={"type": "image", "url": failure.get("url", "")},
                            error={"message": failure.get("error", "")},
                        )
                overview_path.write_text(markdown, encoding="utf-8")
                if checkpoint and overview_item_key:
                    if img_errors:
                        checkpoint.complete_item(overview_item_key, local_path=str(overview_path), metadata={"source": overview_key})
                        checkpoint.fail_item(overview_item_key, f"{len(img_errors)} 个图片下载失败")
                    else:
                        checkpoint.complete_item(overview_item_key, local_path=str(overview_path), metadata={"source": overview_key})
                exported_rows.append({"title": entry.get("title") or "专栏正文", "path": str(overview_path)})
                record_exported_item(
                    str(entry.get("title") or "专栏正文"),
                    overview_path,
                    overview_key,
                    overview_item_key,
                    "completed_with_resource_errors" if img_errors else "completed",
                )
                emit(
                    args,
                    f"知识星球专栏正文导出完成：{entry.get('title') or '专栏正文'}",
                    event="document.export.completed",
                    doc={"title": entry.get("title") or "专栏正文", "index": 0, "path": str(overview_path)},
                    stats={"imageSuccessInDoc": count, "imageFailuresInDoc": len(img_errors)},
                )

        queue_links: list[tuple[dict[str, Any], int]] = []
        seen_urls: set[str] = set()
        queued_urls: set[str] = set()
        seen_toc_keys: set[str] = set()
        group_seen_topic_ids: set[str] = set()
        group_end_time = ""
        group_history_page_total = 0
        group_history_fetched_total = 0
        group_page_count = 0
        group_fetched_count = 0
        group_selected_count = 0
        group_exhausted = not use_group_topics
        group_paused_for_limit = False
        group_latest_create_time = ""
        group_latest_topic_id = ""
        group_previous_latest_create_time = ""
        group_previous_latest_topic_id = ""
        newest_discovered_count = 0
        if checkpoint and use_group_topics and getattr(args, "resume", False):
            cursor, cursor_task_id = load_compatible_group_cursor(
                checkpoint,
                group_id,
                group_scope,
                entry_url,
                output,
            )
            if (
                str(cursor.get("group_id") or "") == str(group_id)
                and str(cursor.get("scope") or "") == str(group_scope)
            ):
                inherited_items = inherit_unresolved_group_items(checkpoint, cursor_task_id)
                group_end_time = str(cursor.get("end_time") or "")
                group_history_page_total = int(cursor.get("page") or 0)
                group_history_fetched_total = int(cursor.get("fetched_count") or 0)
                group_exhausted = bool(cursor.get("exhausted"))
                group_previous_latest_create_time = str(cursor.get("latest_create_time") or "")
                group_previous_latest_topic_id = str(cursor.get("latest_topic_id") or "")
                group_latest_create_time = group_previous_latest_create_time
                group_latest_topic_id = group_previous_latest_topic_id
                emit(
                    args,
                    "已从 checkpoint 恢复知识星球 Group 历史游标："
                    f"page={group_history_page_total} fetched={group_history_fetched_total}；"
                    "本次仍会先检查最新帖子。",
                    event="task.resumed",
                    level="info",
                    checkpointFile=str(checkpoint.path),
                    cursor=cursor,
                    inheritedFromTask=cursor_task_id,
                    inheritedPendingItems=inherited_items,
                )

        def save_group_checkpoint_cursor(exhausted: bool = False) -> None:
            if not checkpoint or not use_group_topics:
                return
            checkpoint.save_cursor(
                GROUP_CURSOR_NAME,
                {
                    "group_id": group_id,
                    "scope": group_scope,
                    "end_time": group_end_time,
                    "page": group_history_page_total,
                    "fetched_count": group_history_fetched_total,
                    "latest_create_time": group_latest_create_time,
                    "latest_topic_id": group_latest_topic_id,
                    "limit": args.limit,
                    "page_size": group_page_size,
                    "exhausted": exhausted,
                    "paused_for_limit": group_paused_for_limit,
                },
            )

        def enqueue_checkpoint_pending_items() -> int:
            nonlocal group_selected_count, group_paused_for_limit
            if not checkpoint or not use_group_topics:
                return 0
            rows = checkpoint.failed_items() if getattr(args, "retry_failed", False) else checkpoint.pending_items()
            decoded_rows: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
            for row in rows:
                metadata = json.loads(row.get("metadata_json") or "{}")
                source = metadata.get("source") if isinstance(metadata, dict) else None
                if isinstance(source, dict):
                    decoded_rows.append((str(source.get("createTime") or ""), row, source))
            decoded_rows.sort(key=lambda value: value[0], reverse=True)
            added = 0
            for _, row, source in decoded_rows:
                if group_selected_count >= args.limit:
                    group_paused_for_limit = True
                    break
                item_key = str(row.get("item_key") or zsxq_item_key_from_source(source))
                topic_id = str(source.get("topicId") or source.get("topicUid") or "").strip()
                if topic_id:
                    group_seen_topic_ids.add(topic_id)
                if item_key and checkpoint.item_status(item_key) == "completed":
                    continue
                queue_links.append((dict(source, kind="toc", outputDir=str(output)), 1))
                added += 1
                group_selected_count += 1
            if added:
                emit(
                    args,
                    f"从 checkpoint 恢复 {added} 个未完成知识星球条目。",
                    event="task.resumed",
                    level="info",
                    checkpointFile=str(checkpoint.path),
                    stats={"pendingItems": added},
                )
            return added

        def discover_newest_group_topics() -> int:
            """Persist posts newer than the previous high-water mark before resuming history."""
            nonlocal group_latest_create_time, group_latest_topic_id
            if (
                not checkpoint
                or not use_group_topics
                or not getattr(args, "resume", False)
                or getattr(args, "retry_failed", False)
            ):
                return 0
            legacy_watermark = not group_previous_latest_create_time and not group_previous_latest_topic_id

            refresh_end_time = ""
            refresh_pages = 0
            discovered = 0
            reached_watermark = False
            newest_time = ""
            newest_id = ""
            while refresh_pages < MAX_GROUP_NEWEST_REFRESH_PAGES:
                check_stopped(args)
                if refresh_pages > 0:
                    pause_between_group_pages(args, refresh_pages, discovered)
                result = fetch_group_topics_page(cdp, group_id, group_scope, refresh_end_time, group_page_size, args)
                if not result.get("ok"):
                    raise ExportError(summarize_zsxq_api_failure(result, f"知识星球 group 最新主题检查失败：{group_id}"))
                batch = [topic for topic in result.get("topics") or [] if isinstance(topic, dict)]
                refresh_pages += 1
                if not batch:
                    reached_watermark = True
                    break
                if not newest_time:
                    newest_time = str(batch[0].get("create_time") or "").strip()
                    newest_id = str(batch[0].get("topic_id") or batch[0].get("topic_uid") or "").strip()

                for topic in batch:
                    topic_id = str(topic.get("topic_id") or topic.get("topic_uid") or "").strip()
                    create_time = str(topic.get("create_time") or "").strip()
                    preview_item = group_topic_to_item(topic, group_scope, discovered, include_raw=True)
                    preview_item["entryUrl"] = entry_url
                    preview_item_key = zsxq_item_key_from_source(preview_item)
                    previous_status = checkpoint.item_status(preview_item_key) if preview_item_key else ""
                    if legacy_watermark and previous_status == "completed":
                        reached_watermark = True
                        break
                    if group_topic_reaches_watermark(
                        topic,
                        group_previous_latest_create_time,
                        group_previous_latest_topic_id,
                    ):
                        reached_watermark = True
                        break
                    item = preview_item
                    item_key = preview_item_key
                    if item_key:
                        checkpoint.upsert_item(
                            item_key,
                            title=str(item.get("title") or item.get("text") or topic_id),
                            source_url=str(item.get("topicUrl") or item.get("articleUrl") or entry_url),
                            source_id=topic_id,
                            metadata={"source": item},
                        )
                    if not previous_status:
                        discovered += 1

                if reached_watermark or len(batch) < group_page_size:
                    reached_watermark = True
                    break
                last_time = str(batch[-1].get("create_time") or "").strip()
                if not last_time or last_time == refresh_end_time:
                    break
                refresh_end_time = previous_zsxq_end_time(last_time)

            if reached_watermark and newest_time:
                group_latest_create_time = newest_time
                group_latest_topic_id = newest_id
            elif not reached_watermark:
                emit(
                    args,
                    f"最新帖子检查达到安全上限 {MAX_GROUP_NEWEST_REFRESH_PAGES} 页；"
                    "已发现内容均已写入 checkpoint，下次仍会从最新页复核，避免越过未确认区间。",
                    event="log.message",
                    level="warn",
                )
            save_group_checkpoint_cursor(exhausted=group_exhausted)
            emit(
                args,
                f"最新帖子检查完成：扫描 {refresh_pages} 页，发现 {discovered} 个待导出帖子。",
                event="task.progress",
                stats={"newestRefreshPages": refresh_pages, "newestDiscoveredTopics": discovered},
            )
            return discovered

        def enqueue_next_group_batch() -> int:
            nonlocal group_end_time, group_page_count, group_fetched_count
            nonlocal group_history_page_total, group_history_fetched_total
            nonlocal group_selected_count, group_exhausted, group_paused_for_limit
            nonlocal group_latest_create_time, group_latest_topic_id, toc
            if not use_group_topics or group_exhausted or group_paused_for_limit:
                return 0
            if group_selected_count >= args.limit:
                group_paused_for_limit = True
                save_group_checkpoint_cursor(exhausted=False)
                return 0
            if group_page_count >= group_max_pages:
                group_paused_for_limit = True
                save_group_checkpoint_cursor(exhausted=False)
                emit(
                    args,
                    f"知识星球 Group 已达到本次批次页数上限：{group_max_pages} 页；"
                    "历史游标已保存，下次会继续且仍会先检查最新帖子。",
                    event="log.message",
                    level="warn",
                )
                return 0
            if group_page_count > 0:
                pause_between_group_pages(args, group_page_count, group_fetched_count)

            result = fetch_group_topics_page(cdp, group_id, group_scope, group_end_time, group_page_size, args)
            if not result.get("ok"):
                raise ExportError(summarize_zsxq_api_failure(result, f"知识星球 group 主题列表读取失败：{group_id}"))
            batch = [topic for topic in result.get("topics") or [] if isinstance(topic, dict)]
            group_page_count += 1
            group_history_page_total += 1
            group_fetched_count += len(batch)
            group_history_fetched_total += len(batch)
            if batch and not group_latest_create_time:
                group_latest_create_time = str(batch[0].get("create_time") or "").strip()
                group_latest_topic_id = str(batch[0].get("topic_id") or batch[0].get("topic_uid") or "").strip()
            added = 0
            for topic in batch:
                topic_id = str(topic.get("topic_id") or topic.get("topic_uid") or "").strip()
                if topic_id and topic_id in group_seen_topic_ids:
                    continue
                if topic_id:
                    group_seen_topic_ids.add(topic_id)
                item = group_topic_to_item(topic, group_scope, group_fetched_count, include_raw=True)
                item["entryUrl"] = entry_url
                item_key = zsxq_item_key_from_source(item)
                if checkpoint and item_key:
                    checkpoint.upsert_item(
                        item_key,
                        title=str(item.get("title") or item.get("text") or topic_id),
                        source_url=str(item.get("topicUrl") or item.get("articleUrl") or entry_url),
                        source_id=topic_id,
                        metadata={"source": item},
                    )
                item_existing = next(
                    (
                        existing[key]
                        for key in canonical_url_keys(item.get("topicUrl"), item.get("articleUrl"))
                        if key in existing
                    ),
                    None,
                )
                already_exported = bool(
                    (checkpoint and item_key and checkpoint.item_status(item_key) == "completed")
                    or (args.incremental and item_existing and not args.update_existing)
                )
                if not already_exported and group_selected_count < args.limit:
                    queue_links.append((dict(item, kind="toc", outputDir=str(output)), 1))
                    added += 1
                    group_selected_count += 1

            toc["pageCount"] = group_page_count
            toc["totalTopics"] = group_fetched_count
            toc["groups"] = [
                {
                    "key": f"group:{group_scope}",
                    "groupIndex": 0,
                    "groupTitle": group_scope_title(group_scope),
                    "expectedCount": args.limit,
                    "topicCount": group_selected_count,
                    "topics": [],
                }
            ]
            emit(
                args,
                f"知识星球 Group 批次读取：page={group_page_count} batch={len(batch)} "
                f"queued={added} selected={group_selected_count}/{args.limit} scope={group_scope}",
                event="task.progress",
                progress={"current": group_selected_count, "total": args.limit},
                stats={"groupPage": group_page_count, "groupBatchTopics": len(batch), "groupQueuedTopics": added},
            )

            if not batch:
                group_exhausted = True
                save_group_checkpoint_cursor(exhausted=True)
                return added
            last_time = str(batch[-1].get("create_time") or "").strip()
            if not last_time or last_time == group_end_time:
                group_exhausted = True
                save_group_checkpoint_cursor(exhausted=True)
                return added
            group_end_time = previous_zsxq_end_time(last_time)
            if len(batch) < group_page_size:
                group_exhausted = True
                save_group_checkpoint_cursor(exhausted=True)
                return added
            if group_selected_count >= args.limit:
                group_paused_for_limit = True
                save_group_checkpoint_cursor(exhausted=False)
                return added
            save_group_checkpoint_cursor(exhausted=False)
            return added

        def child_output_dir_for_existing(md_path: Path, child_count: int) -> Path:
            if md_path.name.startswith("00-"):
                return md_path.parent
            if args.folder_link_threshold > 0 and child_count >= args.folder_link_threshold:
                folder_path = md_path.with_suffix("")
                folder_path.mkdir(parents=True, exist_ok=True)
                return folder_path
            return md_path.parent

        def enqueue_children_from_existing(md_path: Path, source: dict[str, Any], depth: int) -> int:
            if depth >= args.max_depth:
                return 0
            children = filter_follow_zsxq_links(extract_remote_zsxq_links_from_markdown(md_path), args)
            if not children:
                return 0
            source_keys = canonical_url_keys(source.get("href"), source.get("key")) | source_keys_from_markdown(md_path)
            seen_urls.update(source_keys)
            child_output = child_output_dir_for_existing(md_path, len(children))
            added = 0
            for child in children:
                child_href = child.get("href") or ""
                child_keys = canonical_url_keys(child_href)
                if not child_href or child_keys & source_keys:
                    continue
                child_link = {"text": child.get("text") or child_href, "href": child_href}
                if not link_seen(child_link, seen_urls) and not link_seen(child_link, queued_urls):
                    mark_link_seen(child_link, queued_urls)
                    queue_links.append((dict(child_link, kind="link", outputDir=str(child_output)), depth + 1))
                    added += 1
            if added:
                emit(args, f"增量补深入：{md_path.name} 发现 {added} 个下一层链接")
            return added

        if use_group_topics:
            try:
                newest_discovered_count = discover_newest_group_topics()
                restored_items = enqueue_checkpoint_pending_items()
                if getattr(args, "retry_failed", False):
                    group_exhausted = True
                if not restored_items and not group_paused_for_limit:
                    enqueue_next_group_batch()
            except ExportStopped:
                stopped = True
                group_paused_for_limit = True
                emit(args, "收到停止请求，正在生成本次批次报告。", event="task.stopped", level="warn")
        elif use_toc:
            for toc_item in toc_items:
                toc_key = str(toc_item.get("key") or "")
                if not toc_key or toc_key in seen_toc_keys:
                    continue
                seen_toc_keys.add(toc_key)
                queue_links.append((dict(toc_item, kind="toc", outputDir=str(output)), 1))
        else:
            for link in links:
                if link_seen(link, seen_urls) or link_seen(link, queued_urls):
                    continue
                mark_link_seen(link, queued_urls)
                queue_links.append((dict(link, kind="link", outputDir=str(output)), 1))
        while queue_links or (use_group_topics and not group_exhausted and not group_paused_for_limit):
            if not queue_links and use_group_topics and not group_exhausted and not group_paused_for_limit:
                try:
                    enqueue_next_group_batch()
                except ExportStopped:
                    stopped = True
                    group_paused_for_limit = True
                    emit(args, "收到停止请求，正在生成本次批次报告。", event="task.stopped", level="warn")
                    break
                if not queue_links:
                    continue
            if stop_requested(args):
                stopped = True
                emit(args, "收到停止请求，正在结束并写入已完成的导出结果。", event="task.stopped", level="warn")
                break
            link, depth = queue_links.pop(0)
            href = link.get("href") or link.get("key") or ""
            checkpoint_item_key = zsxq_item_key_from_source(link)
            if checkpoint and checkpoint_item_key and checkpoint.item_status(checkpoint_item_key) == "completed" and not args.update_existing:
                skipped += 1
                record_skipped_item(
                    str(link.get("title") or link.get("text") or href),
                    link,
                    "checkpoint-completed",
                    reusable_checkpoint_path(checkpoint_item_key),
                    checkpoint_item_key,
                )
                continue
            if checkpoint and checkpoint_item_key:
                checkpoint.upsert_item(
                    checkpoint_item_key,
                    title=str(link.get("title") or link.get("text") or href),
                    source_url=str(link.get("topicUrl") or link.get("articleUrl") or link.get("href") or ""),
                    source_id=str(link.get("topicId") or link.get("topicUid") or ""),
                    metadata={"source": {k: v for k, v in link.items() if k not in {"kind", "outputDir"}}},
                )
            href_existing = next((existing[key] for key in canonical_url_keys(href) if key in existing), None)
            existing_update_path: Path | None = reusable_checkpoint_path(checkpoint_item_key)
            if args.incremental and href_existing and not args.update_existing:
                if existing_update_path is not None:
                    pass
                elif should_update_existing_for_comments(args, href_existing):
                    existing_update_path = href_existing
                    emit(args, f"补写评论区：{href_existing.name}")
                else:
                    added = enqueue_children_from_existing(href_existing, link, depth)
                    if added:
                        deepened_existing += 1
                        queued_from_existing += added
                    if checkpoint and checkpoint_item_key:
                        checkpoint.complete_item(checkpoint_item_key, local_path=str(href_existing), metadata={"source": link, "skippedExisting": True})
                    skipped += 1
                    record_skipped_item(
                        str(link.get("title") or link.get("text") or href),
                        link,
                        "existing",
                        href_existing,
                        checkpoint_item_key,
                    )
                    if depth == 1:
                        exported_rows.append({"title": link.get("title") or link.get("text") or href, "path": str(href_existing)})
                    continue
            try:
                if checkpoint and checkpoint_item_key:
                    checkpoint.start_item(checkpoint_item_key, "content")
                if link.get("kind") == "toc":
                    item = resolve_toc_item(cdp, link, args)
                    already_seen = bool(canonical_url_keys(item.get("tocKey")) & seen_urls)
                else:
                    item = resolve_link(cdp, link, args)
                    already_seen = item_seen(item, seen_urls)
                resolved_item_key = zsxq_item_key_from_source(item) or checkpoint_item_key
                if checkpoint and resolved_item_key and resolved_item_key != checkpoint_item_key:
                    previous_item_key = checkpoint_item_key
                    checkpoint.upsert_item(
                        resolved_item_key,
                        title=str(item.get("title") or link.get("title") or link.get("text") or href),
                        source_url=str(item.get("topicUrl") or item.get("articleUrl") or href),
                        source_id=str(item.get("topicId") or item.get("topicUid") or ""),
                        metadata={"source": item},
                    )
                    checkpoint.start_item(resolved_item_key, "content")
                    if previous_item_key:
                        checkpoint.skip_item(previous_item_key, f"resolved-to:{resolved_item_key}")
                    checkpoint_item_key = resolved_item_key
                mark_item_seen(item, seen_urls)
                seen_urls.update(canonical_url_keys(item.get("tocKey")))
                if already_seen and not args.update_existing:
                    if checkpoint and checkpoint_item_key:
                        checkpoint.skip_item(checkpoint_item_key, "duplicate")
                    skipped += 1
                    record_skipped_item(
                        str(item.get("title") or link.get("title") or link.get("text") or href),
                        item,
                        "duplicate",
                        item_key=checkpoint_item_key,
                    )
                    continue
                if link.get("kind") == "toc":
                    key_existing = next((existing[key] for key in canonical_url_keys(item.get("tocKey")) if key in existing), None)
                else:
                    key_existing = next(
                        (
                            existing[key]
                            for key in canonical_url_keys(
                                item.get("articleUrl"),
                                item.get("topicUrl"),
                                item.get("shortUrl"),
                                href,
                            )
                            if key in existing
                        ),
                        None,
                    )
                if args.incremental and key_existing and not args.update_existing:
                    if existing_update_path is not None:
                        pass
                    elif should_update_existing_for_comments(args, key_existing):
                        existing_update_path = key_existing
                        emit(args, f"补写评论区：{key_existing.name}")
                    else:
                        added = enqueue_children_from_existing(key_existing, dict(link, **item), depth)
                        if added:
                            deepened_existing += 1
                            queued_from_existing += added
                        if checkpoint and checkpoint_item_key:
                            checkpoint.complete_item(checkpoint_item_key, local_path=str(key_existing), metadata={"source": item, "skippedExisting": True})
                        skipped += 1
                        record_skipped_item(
                            str(item.get("title") or link.get("title") or link.get("text") or href),
                            item,
                            "existing",
                            key_existing,
                            checkpoint_item_key,
                        )
                        if depth == 1:
                            exported_rows.append({"title": item.get("title") or link.get("title") or link.get("text") or href, "path": str(key_existing)})
                        continue
                title = item.get("title") or link.get("text") or "知识星球文档"
                current_output = Path(link.get("outputDir") or output)
                raw_markdown = item.get("markdown") or f"# {title}\n"
                children = filter_follow_zsxq_links((item.get("zsxqLinks") or []) + markdown_links(raw_markdown), args)
                should_folderize = (
                    depth < args.max_depth
                    and args.folder_link_threshold > 0
                    and len(children) >= args.folder_link_threshold
                )
                checkpoint_resume_path = reusable_checkpoint_path(checkpoint_item_key)
                if existing_update_path:
                    md_path = existing_update_path
                    child_output = child_output_dir_for_existing(md_path, len(children))
                    comments_updated_existing += 1
                elif checkpoint_resume_path:
                    md_path = checkpoint_resume_path
                    child_output = child_output_dir_for_existing(md_path, len(children))
                elif should_folderize:
                    folder_path = next_folder_path(current_output, title)
                    md_path = unique_path(folder_path / f"00-{sanitize_filename(title)}.md")
                    child_output = folder_path
                    folderized += 1
                else:
                    md_path = next_markdown_path(current_output, title)
                    child_output = current_output
                emit(
                    args,
                    f"开始导出知识星球文档：{title}",
                    event="document.export.started",
                    doc={"title": title, "index": exported + skipped + len(failures) + 1, "path": str(md_path), "source": href},
                )
                total_comments += int(item.get("commentCount") or 0)
                markdown = append_source_meta(raw_markdown, item)
                markdown, count, img_errors = localize_images(
                    markdown,
                    item.get("images") or [],
                    md_path,
                    args.download_timeout,
                    args.keep_remote_images,
                    args,
                    checkpoint,
                    checkpoint_item_key,
                )
                image_success += count
                if img_errors:
                    image_failures.append({"document": title, "path": str(md_path), "failures": img_errors})
                    for failure in img_errors:
                        emit(
                            args,
                            f"知识星球图片下载失败：{title}：{failure.get('error') or failure.get('url') or ''}",
                            event="resource.download.failed",
                            level="error",
                            doc={"title": title, "path": str(md_path), "source": href},
                            resource={"type": "image", "url": failure.get("url", "")},
                            error={"message": failure.get("error", "")},
                        )
                file_count = 0
                file_errors: list[dict[str, str]] = []
                if args.download_files:
                    markdown, file_count, file_errors = localize_files(
                        markdown,
                        item.get("files") or [],
                        md_path,
                        args.download_timeout,
                        args,
                        checkpoint,
                        checkpoint_item_key,
                    )
                    file_success += file_count
                    if file_errors:
                        file_failures.append({"document": title, "path": str(md_path), "failures": file_errors})
                        for failure in file_errors:
                            emit(
                                args,
                                f"知识星球附件下载失败：{title}：{failure.get('error') or failure.get('url') or ''}",
                                event="resource.download.failed",
                                level="error",
                                doc={"title": title, "path": str(md_path), "source": href},
                                resource={"type": "attachment", "url": failure.get("url", ""), "name": failure.get("name", "")},
                                error={"message": failure.get("error", "")},
                            )
                md_path.write_text(markdown, encoding="utf-8")
                if checkpoint and checkpoint_item_key:
                    if img_errors or file_errors:
                        checkpoint.complete_item(checkpoint_item_key, local_path=str(md_path), metadata={"source": item})
                        checkpoint.fail_item(
                            checkpoint_item_key,
                            f"{len(img_errors)} 个图片、{len(file_errors)} 个附件下载失败",
                        )
                    else:
                        checkpoint.complete_item(checkpoint_item_key, local_path=str(md_path), metadata={"source": item})
                if depth == 1:
                    exported_rows.append({"title": title, "path": str(md_path)})
                record_exported_item(
                    str(title),
                    md_path,
                    item,
                    checkpoint_item_key,
                    "completed_with_resource_errors" if img_errors or file_errors else "completed",
                )
                exported += 1
                emit(
                    args,
                    f"知识星球文档导出完成：{title}",
                    event="document.export.completed",
                    doc={"title": title, "path": str(md_path), "source": href},
                    stats={
                        "imageSuccessInDoc": count,
                        "imageFailuresInDoc": len(img_errors),
                        "attachmentSuccessInDoc": file_count,
                        "attachmentFailuresInDoc": len(file_errors),
                        "children": len(children),
                    },
                )

                if depth < args.max_depth:
                    for child in children:
                        child_href = child.get("href") or ""
                        child_link = {"text": child.get("text") or child_href, "href": child_href}
                        if child_href and not link_seen(child_link, seen_urls) and not link_seen(child_link, queued_urls):
                            mark_link_seen(child_link, queued_urls)
                            queue_links.append((dict(child_link, kind="link", outputDir=str(child_output)), depth + 1))
                maybe_long_sleep_after_export()
            except ExportStopped:
                stopped = True
                emit(args, "收到停止请求，正在结束并写入已完成的导出结果。", event="task.stopped", level="warn")
                break
            except SkipDocument as exc:
                skipped += 1
                if exc.reason == "video-topic":
                    skipped_video += 1
                if checkpoint and checkpoint_item_key:
                    checkpoint.skip_item(checkpoint_item_key, exc.reason)
                record_skipped_item(
                    str(exc.title or link.get("title") or link.get("text") or href),
                    link,
                    exc.reason,
                    item_key=checkpoint_item_key,
                )
                emit(
                    args,
                    f"跳过：{exc.title or href} ({exc.reason})",
                    event="document.export.failed",
                    level="warn",
                    doc={"title": exc.title or href, "source": href},
                    error={"message": exc.reason},
                )
            except Exception as exc:
                if checkpoint and checkpoint_item_key:
                    checkpoint.fail_item(checkpoint_item_key, str(exc))
                failures.append({"title": link.get("text") or "", "href": href, "error": str(exc)})
                emit(
                    args,
                    f"知识星球文档导出失败：{link.get('text') or href}：{exc}",
                    event="document.export.failed",
                    level="error",
                    doc={"title": link.get("text") or "", "source": href},
                    error={"type": type(exc).__name__, "message": str(exc)},
                )

            done = exported + skipped + len(failures)
            total_hint = args.limit if use_group_topics else (len(toc_items) if use_toc else len(links))
            if args.progress_every and (done % args.progress_every == 0 or done == total_hint):
                elapsed = max(0.1, time.time() - started_at)
                rate = done / elapsed
                queued = len(queue_links)
                eta = queued / rate if rate > 0 and queued else 0
                emit(
                    args,
                    "progress "
                    f"done={done} queued={queued} source_links={total_hint} "
                    f"exported={exported} skipped={skipped} failures={len(failures)} "
                    f"elapsed={format_duration(elapsed)} eta={format_duration(eta)}",
                    event="task.progress",
                    progress={"current": done, "total": max(total_hint, done + queued)},
                    stats={
                        "exportedDocs": exported,
                        "skippedDocs": skipped,
                        "failureCount": len(failures),
                        "imageSuccess": image_success,
                        "imageFailureCount": sum(len(item["failures"]) for item in image_failures),
                        "attachmentSuccess": file_success,
                        "attachmentFailureCount": sum(len(item["failures"]) for item in file_failures),
                    },
                )

        write_index(output, entry.get("title") or "知识星球导出", exported_rows)
        local_link_rewrite_count = rewrite_local_zsxq_links(output)
        pending_report_items: list[dict[str, Any]] = []
        failed_report_items: list[dict[str, Any]] = []
        if checkpoint:
            for row in checkpoint.pending_items():
                metadata = json.loads(row.get("metadata_json") or "{}")
                source = metadata.get("source") if isinstance(metadata, dict) else {}
                source = source if isinstance(source, dict) else {}
                report_item = {
                    "itemKey": str(row.get("item_key") or ""),
                    "sourceId": str(row.get("source_id") or source.get("topicId") or source.get("topicUid") or ""),
                    "sourceUrl": str(row.get("source_url") or source.get("topicUrl") or source.get("articleUrl") or ""),
                    "title": str(row.get("title") or source.get("title") or ""),
                    "localPath": str(row.get("local_path") or ""),
                    "status": str(row.get("status") or "pending"),
                    "lastError": str(row.get("last_error") or ""),
                }
                if report_item["status"] == "failed":
                    failed_report_items.append(report_item)
                else:
                    pending_report_items.append(report_item)
        report = {
            "provider": "zsxq",
            "runId": run_id,
            "exportMode": "incremental" if args.incremental else "full",
            "entryUrl": entry_url,
            "output": str(output),
            "sourceMode": source_mode,
            "groupId": toc.get("groupId", ""),
            "groupScope": toc.get("scope", ""),
            "groupPageCount": group_page_count if use_group_topics else toc.get("pageCount", 0),
            "groupHistoryPageTotal": group_history_page_total if use_group_topics else 0,
            "groupHistoryFetchedTotal": group_history_fetched_total if use_group_topics else 0,
            "groupPausedForLimit": group_paused_for_limit if use_group_topics else False,
            "newestDiscoveredDocs": newest_discovered_count if use_group_topics else 0,
            "latestCreateTime": group_latest_create_time if use_group_topics else "",
            "tocGroupCount": len(toc.get("groups") or []),
            "tocTopicCount": group_fetched_count if use_group_topics else toc.get("totalTopics", 0),
            "selectedTocCount": group_fetched_count if use_group_topics else (len(toc_items) if use_toc else 0),
            "sourceLinkCount": group_fetched_count if use_group_topics else (len(toc_items) if use_toc else len(entry.get("zsxqLinks") or [])),
            "selectedLinkCount": group_selected_count if use_group_topics else (len(toc_items) if use_toc else len(links)),
            "totalDocs": exported + skipped + len(failures),
            "exportedDocs": exported,
            "skippedDocs": skipped,
            "skippedVideoDocs": skipped_video,
            "includeComments": args.include_comments,
            "fetchFullComments": args.fetch_full_comments,
            "downloadFiles": args.download_files,
            "exportedComments": total_comments,
            "commentsUpdatedExistingDocs": comments_updated_existing,
            "deepenedExistingDocs": deepened_existing,
            "queuedFromExistingDocs": queued_from_existing,
            "folderizedDocs": folderized,
            "folderLinkThreshold": args.folder_link_threshold,
            "maxDepth": args.max_depth,
            "followLinkScope": args.follow_link_scope,
            "longSleepAfter": args.long_sleep_after,
            "longSleepEvery": args.long_sleep_every,
            "longSleepMinSeconds": args.long_sleep_min,
            "longSleepMaxSeconds": args.long_sleep_max,
            "longSleepCount": long_sleep_count,
            "longSleepSeconds": round(long_sleep_seconds, 1),
            "stopped": stopped,
            "imageSuccess": image_success,
            "imageFailureCount": sum(len(item["failures"]) for item in image_failures),
            "attachmentSuccess": file_success,
            "attachmentFailureCount": sum(len(item["failures"]) for item in file_failures),
            "localLinkRewriteFiles": local_link_rewrite_count,
            "requestCount": int(getattr(args, "_request_count", 0) or 0),
            "rateLimitEvents": int(getattr(args, "_rate_limit_events", 0) or 0),
            "requestDelaySeconds": float(getattr(args, "request_delay", 0) or 0),
            "requestJitterSeconds": float(getattr(args, "request_jitter", 0) or 0),
            "commentRequestDelaySeconds": float(getattr(args, "comment_request_delay", 0) or 0),
            "commentRequestJitterSeconds": float(getattr(args, "comment_request_jitter", 0) or 0),
            "groupPageDelaySeconds": float(getattr(args, "group_page_delay", 0) or 0),
            "groupPageJitterSeconds": float(getattr(args, "group_page_jitter", 0) or 0),
            "rateLimitPauseSeconds": float(getattr(args, "rate_limit_pause", 0) or 0),
            "elapsedSeconds": round(time.time() - started_at, 1),
            "exportedItems": run_exported_items,
            "skippedItems": run_skipped_items,
            "pendingItems": pending_report_items,
            "failedItems": failed_report_items,
            "failures": failures,
            "imageFailures": image_failures,
            "attachmentFailures": file_failures,
            "exportedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        if checkpoint:
            report["checkpoint"] = checkpoint.stats()
        report_path = output / "00-导出报告.json"
        report = finalize_report(report, provider="zsxq", mode="export", report_file=report_path, output=output)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report_history_dir = output / ".wandao" / "reports"
        report_history_dir.mkdir(parents=True, exist_ok=True)
        report_history_path = report_history_dir / f"zsxq-{run_id}.json"
        report_history_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        emit(
            args,
            "知识星球导出完成" if not stopped else "知识星球导出已停止",
            event="task.completed" if not stopped else "task.stopped",
            level="success" if not stopped and not failures else "warn",
            reportFile=str(report_path),
            stats={
                "exportedDocs": exported,
                "skippedDocs": skipped,
                "failureCount": len(failures),
                "imageSuccess": image_success,
                "imageFailureCount": report.get("imageFailureCount", 0),
                "rateLimitEvents": report.get("rateLimitEvents", 0),
            },
        )
        if checkpoint:
            if stopped:
                checkpoint.fail_task("stopped", status="stopped")
            elif failures or image_failures or file_failures:
                checkpoint.fail_task(
                    f"{len(failures)} 个文档失败，"
                    f"{sum(len(item['failures']) for item in image_failures)} 个图片失败，"
                    f"{sum(len(item['failures']) for item in file_failures)} 个附件失败",
                    status="failed",
                )
            else:
                checkpoint.complete_task(report)
        return report
    except Exception as exc:
        if checkpoint:
            checkpoint.fail_task(str(exc))
        raise
    finally:
        if checkpoint:
            checkpoint.close()
        cdp.close()
        if chrome_proc and args.close_started_chrome:
            chrome_proc.terminate()


def scan_toc_entry(args: argparse.Namespace) -> dict[str, Any]:
    entry_url = normalize_entry_url(args.entry_url)
    cdp, chrome_proc = connect_browser(args, entry_url)
    try:
        auth_file = auth_path_from_args(args)
        if auth_file.exists() and not args.skip_auth_load:
            cookie_count = load_auth_state(cdp, auth_file)
            emit(args, f"Loaded {cookie_count} auth cookies from {auth_file}")
            navigate_with_retry(cdp, entry_url, args)
            time.sleep(2)
        emit(args, "开始读取知识星球目录。")
        if args.wait_login:
            input("Press Enter after the ZSXQ page is logged in and visible...")
        toc = collect_group_toc(cdp, entry_url, args) if is_group_entry_url(entry_url) else collect_toc(cdp, entry_url, args)
        selected = select_toc_items(toc, args)
        toc["selectedTopics"] = len(selected)
        return toc
    finally:
        cdp.close()
        if chrome_proc and args.close_started_chrome:
            chrome_proc.terminate()


def login_and_save_auth(args: argparse.Namespace, wait_callback: Callable[[], None] | None = None) -> dict[str, Any]:
    entry_url = normalize_entry_url(args.entry_url)
    auth_file = auth_path_from_args(args)
    cdp, chrome_proc = connect_browser(args, entry_url)
    try:
        cdp.navigate(entry_url)
        emit(args, "Chrome opened. Log in to ZSXQ in the browser.")
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
            input("After login is complete and the ZSXQ page is visible, press Enter...")
        check_stopped(args)
        result = save_auth_state(cdp, auth_file, entry_url)
        emit(args, f"Saved {result['cookieCount']} auth cookies to {auth_file}")
        auth_check = validate_zsxq_auth(cdp, args)
        result["authCheck"] = auth_check
        if auth_check.get("ok"):
            account = auth_check.get("account") or {}
            result["account"] = account
            annotate_auth_state(auth_file, account)
            display_name = str(account.get("name") or account.get("userId") or "当前账号")
            emit(
                args,
                f"知识星球账号验证成功：{display_name}",
                event="auth.verified",
                level="success",
                account=account,
            )
        else:
            emit(
                args,
                summarize_zsxq_api_failure(auth_check, "知识星球账号验证未通过"),
                event="auth.warning",
                level="warn",
                result=auth_check,
            )
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
    root.title("知识星球导出工具")
    root.geometry("1080x840")
    body = create_scrollable_body(root)

    entry_var = tk.StringVar(value=DEFAULT_ENTRY_URL)
    output_var = tk.StringVar(value=str((PROJECT_DIR / "exports" / "zsxq").resolve()))
    auth_var = tk.StringVar(value=str(default_auth_path()))
    profile_var = tk.StringVar(value=str(default_profile_path()))
    browser_path_var = tk.StringVar(value="")
    port_var = tk.StringVar(value=str(DEFAULT_PORT))
    pattern_var = tk.StringVar(value="")
    limit_var = tk.StringVar(value="0")
    depth_var = tk.StringVar(value="2")
    folder_threshold_var = tk.StringVar(value="9")
    request_delay_var = tk.StringVar(value="1.5")
    request_jitter_var = tk.StringVar(value="0.6")
    rate_pause_var = tk.StringVar(value="90")
    rate_retries_var = tk.StringVar(value="5")
    close_chrome_var = tk.BooleanVar(value=False)
    overview_var = tk.BooleanVar(value=False)
    skip_video_var = tk.BooleanVar(value=True)
    include_comments_var = tk.BooleanVar(value=False)
    toc_status_var = tk.StringVar(value="目录：未读取")
    log_queue: queue.Queue[str] = queue.Queue()
    current_stop_event: dict[str, threading.Event | None] = {"event": None}
    toc_state: dict[str, Any] = {"toc": None, "selected": set()}
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
        selected_toc_keys: list[str] | None = None,
        toc_mode: str = "auto",
    ) -> argparse.Namespace:
        if not entry_var.get().strip():
            raise ExportError("请填写知识星球 URL")
        if not output_var.get().strip():
            raise ExportError("请填写输出目录")
        return argparse.Namespace(
            entry_url=entry_var.get().strip(),
            output=output_var.get().strip(),
            port=int(port_var.get().strip() or DEFAULT_PORT),
            profile_dir=profile_var.get().strip() or None,
            browser_path=browser_path_var.get().strip() or None,
            auth_file=auth_var.get().strip() or str(default_auth_path()),
            skip_auth_load=False,
            wait_login=False,
            incremental=incremental,
            update_existing=update_existing,
            include_overview=overview_var.get(),
            include_comments=include_comments_var.get(),
            link_pattern=pattern_var.get().strip() or None,
            toc_mode=toc_mode,
            toc_group_pattern=None,
            toc_title_pattern=None,
            selected_toc_keys=selected_toc_keys,
            limit=int(limit_var.get().strip() or "0"),
            max_depth=max(1, int(depth_var.get().strip() or "2")),
            folder_link_threshold=max(0, int(folder_threshold_var.get().strip() or "9")),
            skip_video_topics=skip_video_var.get(),
            request_delay=max(0.0, float(request_delay_var.get().strip() or "1.5")),
            request_jitter=max(0.0, float(request_jitter_var.get().strip() or "0.6")),
            rate_limit_pause=max(5.0, float(rate_pause_var.get().strip() or "90")),
            rate_limit_retries=max(0, int(rate_retries_var.get().strip() or "5")),
            download_timeout=45,
            progress_every=10,
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
                "浏览器已经打开。\n\n请在浏览器里完成知识星球登录，并确认入口页面能正常打开。\n完成后回到这里点击“确定”，工具会保存登录凭证。",
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

    def all_toc_keys() -> list[str]:
        toc = toc_state.get("toc") or {}
        return [item.get("key") for item in flatten_toc(toc) if item.get("key")]

    def refresh_toc_status() -> None:
        keys = all_toc_keys()
        selected = toc_state.get("selected") or set()
        if not keys:
            toc_status_var.set("目录：未读取")
        else:
            toc_status_var.set(f"目录：共 {len(keys)} 篇，已选择 {len(selected)} 篇")

    def render_toc_tree() -> None:
        toc_tree.delete(*toc_tree.get_children(""))
        toc = toc_state.get("toc") or {}
        selected: set[str] = toc_state.get("selected") or set()
        for group in toc.get("groups") or []:
            topics = group.get("topics") or []
            topic_keys = [item.get("key") for item in topics if item.get("key")]
            selected_count = sum(1 for key in topic_keys if key in selected)
            mark = "☑" if topic_keys and selected_count == len(topic_keys) else ("◩" if selected_count else "☐")
            group_key = group.get("key") or f"group:{group.get('groupIndex', 0)}"
            parent = toc_tree.insert(
                "",
                "end",
                iid=group_key,
                text=f"{mark} {group.get('groupTitle') or '未命名分组'}  ({selected_count}/{len(topic_keys)})",
                open=True,
            )
            for item in topics:
                key = item.get("key")
                item_mark = "☑" if key in selected else "☐"
                toc_tree.insert(parent, "end", iid=key, text=f"{item_mark} {item.get('title') or key}")
        refresh_toc_status()

    def set_all_toc_selected(selected: bool) -> None:
        keys = all_toc_keys()
        if not keys:
            messagebox.showinfo("还没有目录", "请先点击“读取目录”。")
            return
        toc_state["selected"] = set(keys) if selected else set()
        render_toc_tree()

    def invert_toc_selected() -> None:
        keys = set(all_toc_keys())
        if not keys:
            messagebox.showinfo("还没有目录", "请先点击“读取目录”。")
            return
        toc_state["selected"] = keys - set(toc_state.get("selected") or set())
        render_toc_tree()

    def selected_keys_for_export() -> list[str] | None:
        if not toc_state.get("toc"):
            return None
        selected = sorted(toc_state.get("selected") or set())
        if not selected:
            raise ExportError("目录已读取，但没有选择任何文章。")
        return selected

    def toggle_toc_selection(event: Any | None = None) -> str:
        node = toc_tree.focus()
        if not node:
            return "break"
        selected: set[str] = toc_state.get("selected") or set()
        if node.startswith("group:"):
            toc = toc_state.get("toc") or {}
            group = next((item for item in toc.get("groups") or [] if (item.get("key") or f"group:{item.get('groupIndex', 0)}") == node), None)
            topic_keys = [item.get("key") for item in (group or {}).get("topics") or [] if item.get("key")]
            if topic_keys and all(key in selected for key in topic_keys):
                selected.difference_update(topic_keys)
            else:
                selected.update(topic_keys)
        elif node.startswith("toc:"):
            if node in selected:
                selected.remove(node)
            else:
                selected.add(node)
        toc_state["selected"] = selected
        render_toc_tree()
        if node in toc_tree.get_children("") or toc_tree.exists(node):
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
                    for key in (
                        "sourceLinkCount",
                        "selectedLinkCount",
                        "exportedDocs",
                        "skippedDocs",
                        "skippedVideoDocs",
                        "exportedComments",
                        "commentsUpdatedExistingDocs",
                        "deepenedExistingDocs",
                        "queuedFromExistingDocs",
                        "folderizedDocs",
                        "requestCount",
                        "rateLimitEvents",
                        "elapsedSeconds",
                        "stopped",
                        "imageSuccess",
                        "imageFailureCount",
                    )
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
        toc_state["toc"] = result
        toc_state["selected"] = set(item.get("key") for item in flatten_toc(result) if item.get("key"))
        render_toc_tree()

    def do_scan_toc() -> None:
        try:
            args = build_args(incremental=True, update_existing=False, toc_mode="toc")
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        run_worker("读取目录", args, lambda: scan_toc_entry(args), on_toc_loaded)

    def do_incremental() -> None:
        try:
            args = build_args(incremental=True, update_existing=False, selected_toc_keys=selected_keys_for_export())
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        run_worker("增量更新缺失文档", args, lambda: export_entry(args))

    def do_full_export() -> None:
        try:
            args = build_args(incremental=False, update_existing=True, selected_toc_keys=selected_keys_for_export())
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        run_worker("全量覆盖导出", args, lambda: export_entry(args))

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

    row("知识星球入口 URL", entry_var, 0)
    row("输出目录", output_var, 1, browse_output)
    row("凭证文件", auth_var, 2, browse_auth)
    row("浏览器配置目录", profile_var, 3, browse_profile)
    browser_row(4)
    row("链接标题过滤", pattern_var, 5)
    row("最多导出数量", limit_var, 6)
    row("最多进入几层URL", depth_var, 7)
    row("成文件夹链接数", folder_threshold_var, 8)
    row("请求延迟秒", request_delay_var, 9)
    row("请求随机浮动秒", request_jitter_var, 10)
    row("429暂停秒", rate_pause_var, 11)
    row("429重试次数", rate_retries_var, 12)
    row("调试端口", port_var, 13)
    tk.Checkbutton(form, text="保存入口正文", variable=overview_var).grid(row=14, column=1, sticky="w", pady=5)
    tk.Checkbutton(form, text="跳过视频页/视频贴链接", variable=skip_video_var).grid(row=15, column=1, sticky="w", pady=5)
    tk.Checkbutton(form, text="同时导出评论区", variable=include_comments_var).grid(row=16, column=1, sticky="w", pady=5)
    tk.Checkbutton(form, text="导出后关闭本工具启动的浏览器", variable=close_chrome_var).grid(row=17, column=1, sticky="w", pady=5)

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
        text="说明：先读取目录可选择导出范围；未读取目录时默认导出全部可识别内容。勾选评论区会额外滚动并展开页面可见评论；遇到 429 会暂停重试。",
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
    parser = argparse.ArgumentParser(description="Export ZSXQ column/topic/article pages to Markdown.")
    parser.add_argument("--gui", action="store_true", help="Open the graphical interface")
    parser.add_argument("--login", action="store_true", help="Open browser, let you log in, then save auth cookies")
    parser.add_argument("--login-wait-seconds", type=float, default=0.0, help="For non-interactive GUI wrappers, wait this many seconds before saving login cookies")
    parser.add_argument("--scan-toc", action="store_true", help="Read the left ZSXQ directory and print it as JSON, without exporting")
    parser.add_argument("--entry-url", help="ZSXQ column/topic/article URL")
    parser.add_argument("--output", help="Output directory")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Chrome remote debugging port")
    parser.add_argument("--profile-dir", help=f"Chrome profile dir. Omit to auto-use {default_profile_path()}")
    parser.add_argument("--browser-path", help="Optional Chrome/Edge/Chromium executable path")
    parser.add_argument("--auth-file", help=f"Auth cookie file. Omit to auto-use {default_auth_path()}")
    parser.add_argument("--skip-auth-load", action="store_true", help="Do not load saved auth cookies before export")
    parser.add_argument("--wait-login", action="store_true", help="Pause for manual login before exporting")
    parser.add_argument("--incremental", action="store_true", help="Only export documents missing from local Markdown")
    parser.add_argument("--update-existing", action="store_true", help="With --incremental, update existing documents too")
    parser.add_argument("--checkpoint-file", help="SQLite checkpoint file for precise resume")
    parser.add_argument("--checkpoint-task-id", default=os.environ.get("WANDAO_JOB_ID") or "default", help="Stable job id inside the checkpoint database")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint when available")
    parser.add_argument("--retry-failed", action="store_true", help="Only retry failed checkpoint items")
    parser.add_argument("--reset-checkpoint", action="store_true", help="Delete the checkpoint before starting")
    parser.add_argument("--no-overview", dest="include_overview", action="store_false", help="Do not export the entry/column body itself")
    parser.set_defaults(include_overview=True)
    parser.add_argument("--link-pattern", help="Regex filter for link text or href, for example AI大模型Ragent项目")
    parser.add_argument("--toc-mode", choices=("auto", "toc", "off"), default="auto", help="Use left directory for column pages: auto, toc, or off")
    parser.add_argument("--toc-group-pattern", help="Regex filter for ZSXQ directory group names")
    parser.add_argument("--toc-title-pattern", help="Regex filter for ZSXQ directory article titles")
    parser.add_argument("--toc-key", action="append", dest="selected_toc_keys", help="Export one specific directory key, repeatable, for example toc:1:0")
    parser.add_argument("--group-scope", choices=("auto", "all", "digests", "by_owner"), default="auto", help="For /group/ URLs: export all topics, digests, or owner topics")
    parser.add_argument("--group-page-size", type=int, default=DEFAULT_GROUP_BATCH_SIZE, help="Topics fetched per group API batch")
    parser.add_argument("--group-max-pages", type=int, default=200, help="Safety limit for group topic pagination")
    parser.add_argument("--group-page-delay", type=float, default=4.0, help="Extra seconds to wait between ZSXQ group topic list pages")
    parser.add_argument("--group-page-jitter", type=float, default=4.0, help="Extra random seconds added to --group-page-delay")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of source links to export. 0 means no limit")
    parser.add_argument("--max-depth", type=int, default=2, help="Recursion depth for ZSXQ links inside exported pages")
    parser.add_argument(
        "--follow-link-scope",
        choices=("all", "articles", "none"),
        default="all",
        help="Which ZSXQ links to follow recursively: all links, articles.zsxq.com only, or none",
    )
    parser.add_argument(
        "--folder-link-threshold",
        type=int,
        default=9,
        help="If an exported page has at least this many ZSXQ links, place linked pages in a same-name folder. 0 disables this.",
    )
    parser.add_argument("--request-delay", type=float, default=DEFAULT_REQUEST_DELAY, help="Seconds to wait before each ZSXQ navigation/API request")
    parser.add_argument("--request-jitter", type=float, default=DEFAULT_REQUEST_JITTER, help="Extra random seconds added to request delay")
    parser.add_argument("--rate-limit-pause", type=float, default=90, help="Seconds to pause after Too Many Requests before retrying")
    parser.add_argument("--rate-limit-retries", type=int, default=5, help="Retries for the same URL/API call after Too Many Requests")
    parser.add_argument("--long-sleep-after", type=int, default=25, help="Enable long sleep only after this many successfully exported docs. 0 disables it")
    parser.add_argument("--long-sleep-every", type=int, default=12, help="After --long-sleep-after, sleep whenever exported docs is a multiple of this value. 0 disables it")
    parser.add_argument("--long-sleep-min", type=float, default=120, help="Minimum long sleep seconds for large ZSXQ exports")
    parser.add_argument("--long-sleep-max", type=float, default=300, help="Maximum long sleep seconds for large ZSXQ exports")
    parser.add_argument("--include-video-topics", dest="skip_video_topics", action="store_false", help="Export video-only ZSXQ topic pages instead of skipping them")
    parser.add_argument("--skip-video-topics", dest="skip_video_topics", action="store_true", help="Skip video-only ZSXQ topic pages")
    parser.set_defaults(skip_video_topics=True)
    parser.add_argument("--include-comments", action="store_true", help="Append visible ZSXQ comments to exported Markdown")
    parser.add_argument("--no-comments", dest="include_comments", action="store_false", help="Do not export ZSXQ comments")
    parser.set_defaults(include_comments=False)
    parser.add_argument("--fetch-full-comments", action="store_true", help="Fetch the full comments API for each topic. Slower and easier to hit ZSXQ rate limits")
    parser.add_argument("--comment-request-delay", type=float, default=DEFAULT_COMMENT_REQUEST_DELAY, help="Minimum seconds to wait before each full comments API request")
    parser.add_argument("--comment-request-jitter", type=float, default=DEFAULT_COMMENT_REQUEST_JITTER, help="Extra random seconds added to full comments API request delay")
    parser.add_argument("--download-files", action="store_true", help="Download ZSXQ post attachments and rewrite Markdown links to local files")
    parser.add_argument("--download-timeout", type=int, default=45, help="Seconds to wait for each image download")
    parser.add_argument("--progress-every", type=int, default=10, help="Print progress after N documents")
    parser.add_argument("--keep-remote-images", action="store_true", default=True, help="Keep remote image URLs when download fails")
    parser.add_argument("--drop-failed-images", dest="keep_remote_images", action="store_false", help="Remove image URL when download fails")
    parser.add_argument("--close-started-chrome", action="store_true", help="Close Chrome started by this script after export")
    return apply_zsxq_safety_defaults(parser.parse_args(argv))


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.gui:
        print("旧版 Python GUI 已废弃，请使用 Electron 桌面端：start-wandao.cmd 或 ./start-wandao.sh", file=sys.stderr)
        return 2
    if not args.entry_url:
        raise ExportError("--entry-url is required")
    if not args.output:
        data_dir = os.environ.get("WANDAO_DATA_DIR")
        root = Path(data_dir).expanduser().resolve() if data_dir else PROJECT_DIR
        args.output = str((root / "exports" / "zsxq").resolve())
    try:
        if args.login:
            result = login_and_save_auth(args)
        elif args.scan_toc:
            result = scan_toc_entry(args)
        else:
            result = export_entry(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ExportStopped as exc:
        emit(args, f"知识星球导出已停止：{exc}", event="task.stopped", level="warn")
        print(f"Stopped: {exc}", file=sys.stderr)
        return 130
    except Exception as exc:
        emit(
            args,
            f"知识星球导出任务失败：{exc}",
            event="task.failed",
            level="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
