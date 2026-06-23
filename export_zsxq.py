#!/usr/bin/env python3
# Author: tllovesxs
"""
Standalone exporter for ZSXQ (知识星球) column/topic/article pages.

GUI:
  python export_zsxq.py --gui

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
    wait_for_debug_port,
)


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR
DEFAULT_PROFILE = ".zsxq-chrome-profile"
DEFAULT_AUTH_FILE = ".zsxq_auth.json"
DEFAULT_ENTRY_URL = ""


class SkipDocument(Exception):
    def __init__(self, reason: str, title: str = "", href: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.title = title
        self.href = href


def default_auth_path() -> Path:
    return PROJECT_DIR / DEFAULT_AUTH_FILE


def default_profile_path() -> Path:
    env_profile = os.environ.get("ZSXQ_PROFILE_DIR")
    if env_profile:
        return Path(env_profile).expanduser().resolve()
    return PROJECT_DIR / DEFAULT_PROFILE


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
    if "zsxq.com" not in parsed.netloc:
        raise ExportError("当前工具只支持 zsxq.com 知识星球页面")
    return entry_url.strip()


def page_for_zsxq(port: int) -> dict[str, Any] | None:
    pages = http_json(f"http://127.0.0.1:{port}/json/list", timeout=5)
    for page in pages:
        url = page.get("url", "")
        if page.get("type") == "page" and ("wx.zsxq.com" in url or "articles.zsxq.com" in url or "t.zsxq.com" in url):
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
    domain = (cookie.get("domain") or "").lower()
    return any(token in domain for token in ("zsxq.com", "zsxq.cn", "zsxq-img", "zsxqpic"))


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
    if (el.classList && (el.classList.contains("comment-container") || el.classList.contains("comment-item"))) return "";
    if (/^h[1-6]$/.test(tag)) return "#".repeat(+tag[1] + 1) + " " + clean(inline(el));
    if (tag === "p") return clean(inline(el));
    if (tag === "pre") return "```\n" + (el.innerText || "").replace(/\n$/, "") + "\n```";
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
  const activate = el => {
    if (!el) return;
    el.scrollIntoView({block: "center", inline: "nearest"});
    for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      el.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
    }
  };
  const expectedCount = title => {
    const match = clean(title).match(/[（(]\s*(\d+)\s*[）)]/);
    return match ? parseInt(match[1], 10) : 0;
  };
  const lists = () => [...document.querySelectorAll(".list-container .list")];
  const topicItems = list => [...list.querySelectorAll(".topic-item")];
  for (let gi = 0; gi < lists().length; gi += 1) {
    let list = lists()[gi];
    const name = clean((list.querySelector(".info .name") || list.querySelector(".name") || {}).innerText || "");
    const expected = expectedCount(name);
    if (expected && topicItems(list).length < expected) {
      const trigger = list.querySelector(".info .container") || list.querySelector(".info .name") || list.querySelector(".info") || list;
      try {
        activate(trigger);
      } catch (err) {}
      const deadline = Date.now() + 8000;
      while (Date.now() < deadline) {
        await wait(250);
        list = lists()[gi];
        const current = topicItems(list).length;
        if (current >= expected || current > 0) break;
      }
    }
  }
  const groups = lists().map((list, gi) => {
    const groupTitle = clean((list.querySelector(".info .name") || list.querySelector(".name") || {}).innerText || `目录 ${gi + 1}`);
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
    return {
      key: `group:${gi}`,
      groupIndex: gi,
      groupTitle,
      expectedCount: expectedCount(groupTitle),
      topicCount: topics.length,
      topics,
    };
  }).filter(group => group.groupTitle || group.topics.length);
  return {
    href: location.href,
    title: clean((document.querySelector(".group-name") || document.querySelector(".title") || {}).innerText || document.title || "知识星球目录"),
    groups,
    totalTopics: groups.reduce((sum, group) => sum + group.topics.length, 0),
  };
}
"""


ZSXQ_OPEN_TOC_ITEM_JS = r"""
async (target) => {
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const clean = s => (s || "").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
  const activate = el => {
    if (!el) return;
    el.scrollIntoView({block: "center", inline: "nearest"});
    for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      el.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
    }
  };
  const lists = () => [...document.querySelectorAll(".list-container .list")];
  const topicItems = list => [...list.querySelectorAll(".topic-item")];
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
    activate(trigger);
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
  activate(topic);
  const deadline = Date.now() + 12000;
  let detailText = "";
  while (Date.now() < deadline) {
    await wait(350);
          const root = document.querySelector(".column-topic-detail .talk-content-container, .column-topic-detail .answer-content-container, .talk-content-container, .answer-content-container");
    detailText = clean(root && root.innerText || "");
    const selected = clean((document.querySelector(".topic-item .content.selected") || {}).innerText || "");
    if (detailText.length > 30 && (!target.title || detailText.includes(target.title.slice(0, 16)) || selected === topicTitle)) {
      break;
    }
  }
  return {
    ok: detailText.length > 0,
    href: location.href,
    groupTitle,
    topicTitle,
    detailTextLen: detailText.length,
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
        items = [item for item in items if item.get("key") in selected_keys]
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
    content = cdp.evaluate(
        f"({ZSXQ_CONVERTER_JS})({js_string(source.get('title') or '知识星球文档')}, {js_string('.column-topic-detail .talk-content-container, .column-topic-detail .answer-content-container, .talk-content-container, .answer-content-container')})",
        timeout=90,
    )
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
        lambda value: bool(value.get("article")) or "articles.zsxq.com" in value.get("href", "") or bool(value.get("ready")),
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
        let data = {{}};
        try {{ data = JSON.parse(text); }} catch (err) {{}}
        if (rateLimited) {{
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


def content_is_rate_limited(content: dict[str, Any]) -> bool:
    title = str(content.get("title") or "")
    markdown = str(content.get("markdown") or "")
    plain = re.sub(r"\s+", " ", f"{title}\n{markdown}").strip()
    if re.fullmatch(r"(?:HTTP\s*)?429(?:\s+Too Many Requests)?|Too Many Requests", plain, re.I):
        return True
    if re.fullmatch(r"请求过于频繁|请求太频繁|访问过于频繁", plain):
        return True
    return len(plain) <= 300 and bool(re.search(r"Too Many Requests|请求过于频繁|请求太频繁|访问过于频繁", plain, re.I))


def resolve_link(cdp: CDPClient, link: dict[str, str], args: argparse.Namespace | None = None) -> dict[str, Any]:
    href = link["href"]
    retries = max(0, int(getattr(args, "rate_limit_retries", 5) if args else 5))
    for attempt in range(retries + 1):
        navigate_with_retry(cdp, href, args)
        info = find_article_url_on_topic(cdp, args)
        if detect_rate_limited_page(cdp).get("limited"):
            pause_for_rate_limit(args, href, attempt, retries)
            continue
        topic_url = info.get("href") or href
        article_url = info.get("article") or (topic_url if "articles.zsxq.com" in topic_url else "")
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
    import hashlib

    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://wx.zsxq.com/"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
        ext = guess_extension(url, response.headers.get("Content-Type"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / (hashlib.sha1(url.encode("utf-8")).hexdigest()[:12] + "." + ext)
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
) -> tuple[str, int, list[dict[str, str]]]:
    success = 0
    failures: list[dict[str, str]] = []
    for url in sorted(set(images)):
        check_stopped(args)
        if not url.startswith(("http://", "https://")):
            continue
        try:
            target = download_image(url, md_path.parent / "assets", timeout)
            markdown = markdown.replace(url, os.path.relpath(target, md_path.parent).replace("\\", "/"))
            success += 1
        except Exception as exc:
            failures.append({"url": url, "error": str(exc)})
            if not keep_remote:
                markdown = markdown.replace(url, "")
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


def item_seen(item: dict[str, Any], seen_urls: set[str]) -> bool:
    keys = canonical_url_keys(item.get("topicUrl"), item.get("articleUrl"))
    return bool(keys & seen_urls)


def mark_item_seen(item: dict[str, Any], seen_urls: set[str]) -> None:
    seen_urls.update(canonical_url_keys(item.get("shortUrl"), item.get("topicUrl"), item.get("articleUrl")))


def link_seen(link: dict[str, str], seen_urls: set[str]) -> bool:
    return bool(canonical_url_keys(link.get("href")) & seen_urls)


def mark_link_seen(link: dict[str, str], seen_urls: set[str]) -> None:
    seen_urls.update(canonical_url_keys(link.get("href")))


def append_source_meta(markdown: str, item: dict[str, Any]) -> str:
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
        if not href:
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
    args.folder_link_threshold = max(0, int(getattr(args, "folder_link_threshold", 9) or 0))
    args.skip_video_topics = bool(getattr(args, "skip_video_topics", True))
    args.include_comments = bool(getattr(args, "include_comments", False))
    args.request_delay = max(0.0, float(getattr(args, "request_delay", 1.5) or 0))
    args.request_jitter = max(0.0, float(getattr(args, "request_jitter", 0.6) or 0))
    args.rate_limit_pause = max(5.0, float(getattr(args, "rate_limit_pause", 90) or 90))
    args.rate_limit_retries = max(0, int(getattr(args, "rate_limit_retries", 5) or 0))
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
        if toc_mode != "off" and is_column_entry_url(entry_url):
            try:
                toc = collect_toc(cdp, entry_url, args)
                toc_items = select_toc_items(toc, args)
                use_toc = bool(toc_items)
                emit(
                    args,
                    f"目录读取完成：groups={len(toc.get('groups') or [])} total_topics={toc.get('totalTopics', 0)} selected={len(toc_items)}",
                )
                if toc_mode == "toc" and not toc_items:
                    raise ExportError("目录已读取，但没有匹配的导出条目。")
            except Exception as exc:
                if toc_mode == "toc":
                    raise
                emit(args, f"目录读取失败，改用正文链接导出：{exc}")
                toc = {}
                toc_items = []
                use_toc = False

        if use_toc and not args.include_overview:
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
            links = entry.get("zsxqLinks") or []
            if args.limit and args.limit > 0 and not use_toc:
                links = links[: args.limit]
        existing = scan_exported_docs(output)
        exported_rows: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        image_failures: list[dict[str, Any]] = []
        image_success = 0
        exported = 0
        skipped = 0
        skipped_video = 0
        total_comments = 0
        comments_updated_existing = 0
        stopped = False
        folderized = 0
        deepened_existing = 0
        queued_from_existing = 0
        started_at = time.time()
        root_sequence = 0
        folder_sequences: dict[str, int] = {}
        output_key = str(output.resolve()).lower()

        def allocate_sequence(base_dir: Path) -> int:
            nonlocal root_sequence
            base_dir.mkdir(parents=True, exist_ok=True)
            key = str(base_dir.resolve()).lower()
            if key == output_key:
                root_sequence += 1
                return root_sequence
            folder_sequences[key] = folder_sequences.get(key, 0) + 1
            return folder_sequences[key]

        def unique_path(path: Path) -> Path:
            if not path.exists():
                return path
            for i in range(2, 1000):
                candidate = path.with_name(f"{path.stem}-{i}{path.suffix}")
                if not candidate.exists():
                    return candidate
            raise ExportError(f"无法生成不冲突的文件名：{path}")

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

        if args.include_overview:
            root_sequence = max(root_sequence, 1)
            overview_path = output / "01-专栏正文.md"
            overview_key = str(entry.get("url") or entry_url)
            overview_existing = next((existing[key] for key in canonical_url_keys(overview_key) if key in existing), None)
            overview_needs_comment_update = bool(
                args.incremental
                and overview_existing
                and not args.update_existing
                and should_update_existing_for_comments(args, overview_existing)
            )
            if args.incremental and overview_existing and not args.update_existing and not overview_needs_comment_update:
                exported_rows.append({"title": entry.get("title") or "专栏正文", "path": str(overview_existing)})
            else:
                if overview_needs_comment_update and overview_existing:
                    overview_path = overview_existing
                    comments_updated_existing += 1
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
                markdown, count, img_errors = localize_images(
                    markdown,
                    entry.get("images") or [],
                    overview_path,
                    args.download_timeout,
                    args.keep_remote_images,
                    args,
                )
                image_success += count
                if img_errors:
                    image_failures.append({"document": "专栏正文", "path": str(overview_path), "failures": img_errors})
                overview_path.write_text(markdown, encoding="utf-8")
                exported_rows.append({"title": entry.get("title") or "专栏正文", "path": str(overview_path)})

        queue_links: list[tuple[dict[str, Any], int]] = []
        seen_urls: set[str] = set()
        queued_urls: set[str] = set()
        seen_toc_keys: set[str] = set()

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
            children = extract_remote_zsxq_links_from_markdown(md_path)
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

        if use_toc:
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
        while queue_links:
            if stop_requested(args):
                stopped = True
                emit(args, "收到停止请求，正在结束并写入已完成的导出结果。")
                break
            link, depth = queue_links.pop(0)
            href = link.get("href") or link.get("key") or ""
            href_existing = next((existing[key] for key in canonical_url_keys(href) if key in existing), None)
            existing_update_path: Path | None = None
            if args.incremental and href_existing and not args.update_existing:
                if should_update_existing_for_comments(args, href_existing):
                    existing_update_path = href_existing
                    emit(args, f"补写评论区：{href_existing.name}")
                else:
                    added = enqueue_children_from_existing(href_existing, link, depth)
                    if added:
                        deepened_existing += 1
                        queued_from_existing += added
                    skipped += 1
                    if depth == 1:
                        exported_rows.append({"title": link.get("title") or link.get("text") or href, "path": str(href_existing)})
                    continue
            try:
                if link.get("kind") == "toc":
                    item = resolve_toc_item(cdp, link, args)
                    already_seen = bool(canonical_url_keys(item.get("tocKey")) & seen_urls)
                else:
                    item = resolve_link(cdp, link, args)
                    already_seen = item_seen(item, seen_urls)
                mark_item_seen(item, seen_urls)
                seen_urls.update(canonical_url_keys(item.get("tocKey")))
                if already_seen and not args.update_existing:
                    skipped += 1
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
                        skipped += 1
                        if depth == 1:
                            exported_rows.append({"title": item.get("title") or link.get("title") or link.get("text") or href, "path": str(key_existing)})
                        continue
                title = item.get("title") or link.get("text") or "知识星球文档"
                current_output = Path(link.get("outputDir") or output)
                children = unique_zsxq_links(item.get("zsxqLinks") or [])
                should_folderize = (
                    depth < args.max_depth
                    and args.folder_link_threshold > 0
                    and len(children) >= args.folder_link_threshold
                )
                if existing_update_path:
                    md_path = existing_update_path
                    child_output = child_output_dir_for_existing(md_path, len(children))
                    comments_updated_existing += 1
                elif should_folderize:
                    folder_path = next_folder_path(current_output, title)
                    md_path = unique_path(folder_path / f"00-{sanitize_filename(title)}.md")
                    child_output = folder_path
                    folderized += 1
                else:
                    md_path = next_markdown_path(current_output, title)
                    child_output = current_output
                total_comments += int(item.get("commentCount") or 0)
                markdown = append_source_meta(item.get("markdown") or f"# {title}\n", item)
                markdown, count, img_errors = localize_images(
                    markdown,
                    item.get("images") or [],
                    md_path,
                    args.download_timeout,
                    args.keep_remote_images,
                    args,
                )
                image_success += count
                if img_errors:
                    image_failures.append({"document": title, "path": str(md_path), "failures": img_errors})
                md_path.write_text(markdown, encoding="utf-8")
                if depth == 1:
                    exported_rows.append({"title": title, "path": str(md_path)})
                exported += 1

                if depth < args.max_depth:
                    for child in children:
                        child_href = child.get("href") or ""
                        child_link = {"text": child.get("text") or child_href, "href": child_href}
                        if child_href and not link_seen(child_link, seen_urls) and not link_seen(child_link, queued_urls):
                            mark_link_seen(child_link, queued_urls)
                            queue_links.append((dict(child_link, kind="link", outputDir=str(child_output)), depth + 1))
            except ExportStopped:
                stopped = True
                emit(args, "收到停止请求，当前文档未写入，正在结束。")
                break
            except SkipDocument as exc:
                skipped += 1
                if exc.reason == "video-topic":
                    skipped_video += 1
                emit(args, f"跳过：{exc.title or href} ({exc.reason})")
            except Exception as exc:
                failures.append({"title": link.get("text") or "", "href": href, "error": str(exc)})

            done = exported + skipped + len(failures)
            total_hint = len(toc_items) if use_toc else len(links)
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
                )

        write_index(output, entry.get("title") or "知识星球导出", exported_rows)
        local_link_rewrite_count = rewrite_local_zsxq_links(output)
        report = {
            "provider": "zsxq",
            "mode": "incremental" if args.incremental else "full",
            "entryUrl": entry_url,
            "output": str(output),
            "sourceMode": "toc" if use_toc else "links",
            "tocGroupCount": len(toc.get("groups") or []),
            "tocTopicCount": toc.get("totalTopics", 0),
            "selectedTocCount": len(toc_items) if use_toc else 0,
            "sourceLinkCount": len(toc_items) if use_toc else len(entry.get("zsxqLinks") or []),
            "selectedLinkCount": len(toc_items) if use_toc else len(links),
            "exportedDocs": exported,
            "skippedDocs": skipped,
            "skippedVideoDocs": skipped_video,
            "includeComments": args.include_comments,
            "exportedComments": total_comments,
            "commentsUpdatedExistingDocs": comments_updated_existing,
            "deepenedExistingDocs": deepened_existing,
            "queuedFromExistingDocs": queued_from_existing,
            "folderizedDocs": folderized,
            "folderLinkThreshold": args.folder_link_threshold,
            "stopped": stopped,
            "imageSuccess": image_success,
            "imageFailureCount": sum(len(item["failures"]) for item in image_failures),
            "localLinkRewriteFiles": local_link_rewrite_count,
            "requestCount": int(getattr(args, "_request_count", 0) or 0),
            "rateLimitEvents": int(getattr(args, "_rate_limit_events", 0) or 0),
            "requestDelaySeconds": float(getattr(args, "request_delay", 0) or 0),
            "requestJitterSeconds": float(getattr(args, "request_jitter", 0) or 0),
            "rateLimitPauseSeconds": float(getattr(args, "rate_limit_pause", 0) or 0),
            "elapsedSeconds": round(time.time() - started_at, 1),
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
        toc = collect_toc(cdp, entry_url, args)
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
        else:
            input("After login is complete and the ZSXQ page is visible, press Enter...")
        check_stopped(args)
        result = save_auth_state(cdp, auth_file, entry_url)
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
    root.title("知识星球导出工具")
    root.geometry("1080x840")

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

    form = tk.Frame(root, padx=14, pady=12)
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
        text="说明：先读取目录可选择导出范围；未读取目录时默认导出全部可识别内容。勾选评论区会额外滚动并展开页面可见评论；遇到 429 会暂停重试。",
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
    parser = argparse.ArgumentParser(description="Export ZSXQ column/topic/article pages to Markdown.")
    parser.add_argument("--gui", action="store_true", help="Open the graphical interface")
    parser.add_argument("--login", action="store_true", help="Open browser, let you log in, then save auth cookies")
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
    parser.add_argument("--no-overview", dest="include_overview", action="store_false", help="Do not export the entry/column body itself")
    parser.set_defaults(include_overview=True)
    parser.add_argument("--link-pattern", help="Regex filter for link text or href, for example AI大模型Ragent项目")
    parser.add_argument("--toc-mode", choices=("auto", "toc", "off"), default="auto", help="Use left directory for column pages: auto, toc, or off")
    parser.add_argument("--toc-group-pattern", help="Regex filter for ZSXQ directory group names")
    parser.add_argument("--toc-title-pattern", help="Regex filter for ZSXQ directory article titles")
    parser.add_argument("--toc-key", action="append", dest="selected_toc_keys", help="Export one specific directory key, repeatable, for example toc:1:0")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of source links to export. 0 means no limit")
    parser.add_argument("--max-depth", type=int, default=2, help="Recursion depth for ZSXQ links inside exported pages")
    parser.add_argument(
        "--folder-link-threshold",
        type=int,
        default=9,
        help="If an exported page has at least this many ZSXQ links, place linked pages in a same-name folder. 0 disables this.",
    )
    parser.add_argument("--request-delay", type=float, default=1.5, help="Seconds to wait before each ZSXQ navigation/API request")
    parser.add_argument("--request-jitter", type=float, default=0.6, help="Extra random seconds added to request delay")
    parser.add_argument("--rate-limit-pause", type=float, default=90, help="Seconds to pause after Too Many Requests before retrying")
    parser.add_argument("--rate-limit-retries", type=int, default=5, help="Retries for the same URL/API call after Too Many Requests")
    parser.add_argument("--include-video-topics", dest="skip_video_topics", action="store_false", help="Export video-only ZSXQ topic pages instead of skipping them")
    parser.add_argument("--skip-video-topics", dest="skip_video_topics", action="store_true", help="Skip video-only ZSXQ topic pages")
    parser.set_defaults(skip_video_topics=True)
    parser.add_argument("--include-comments", action="store_true", help="Append visible ZSXQ comments to exported Markdown")
    parser.add_argument("--no-comments", dest="include_comments", action="store_false", help="Do not export ZSXQ comments")
    parser.set_defaults(include_comments=False)
    parser.add_argument("--download-timeout", type=int, default=45, help="Seconds to wait for each image download")
    parser.add_argument("--progress-every", type=int, default=10, help="Print progress after N documents")
    parser.add_argument("--keep-remote-images", action="store_true", default=True, help="Keep remote image URLs when download fails")
    parser.add_argument("--drop-failed-images", dest="keep_remote_images", action="store_false", help="Remove image URL when download fails")
    parser.add_argument("--close-started-chrome", action="store_true", help="Close Chrome started by this script after export")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.gui:
        return run_gui()
    if not args.entry_url:
        raise ExportError("--entry-url is required")
    if not args.output:
        args.output = str((PROJECT_DIR / "exports" / "zsxq").resolve())
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
        print(f"Stopped: {exc}", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
