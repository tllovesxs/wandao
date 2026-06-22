#!/usr/bin/env python3
# Author: tllovesxs
"""
Standalone Yuque knowledge-base exporter.

GUI:
  python export_yuque.py --gui

First login:
  python export_yuque.py --login \
    --book-url https://www.yuque.com/<namespace>/<book> \
    --auth-file .yuque_auth.json

Incremental export:
  python export_yuque.py \
    --book-url https://www.yuque.com/<namespace>/<book> \
    --output "./exports/yuque" \
    --auth-file .yuque_auth.json \
    --incremental

The tool controls Chrome/Edge through Chrome DevTools Protocol and stores session
cookies, not passwords. Keep auth files private.
"""

from __future__ import annotations

import argparse
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
DEFAULT_PROFILE = ".yuque-chrome-profile"
DEFAULT_AUTH_FILE = ".yuque_auth.json"
DEFAULT_BOOK_URL = ""


def default_auth_path() -> Path:
    return PROJECT_DIR / DEFAULT_AUTH_FILE


def default_profile_path() -> Path:
    env_profile = os.environ.get("YUQUE_PROFILE_DIR")
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


def normalize_book_url(book_url: str) -> str:
    parsed = urllib.parse.urlparse(book_url.strip())
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise ExportError("语雀知识库 URL 至少需要包含用户/团队路径和知识库 slug")
    return f"https://www.yuque.com/{parts[0]}/{parts[1]}"


def parse_book_url(book_url: str) -> tuple[str, str, str]:
    normalized = normalize_book_url(book_url)
    parts = [part for part in urllib.parse.urlparse(normalized).path.split("/") if part]
    return parts[0], parts[1], normalized


def page_for_book(port: int, namespace: str, book_slug: str) -> dict[str, Any] | None:
    pages = http_json(f"http://127.0.0.1:{port}/json/list", timeout=5)
    needle = f"yuque.com/{namespace}/{book_slug}"
    for page in pages:
        if page.get("type") == "page" and needle in page.get("url", ""):
            return page
    return None


def connect_book_browser(
    args: argparse.Namespace,
    book_url: str,
    namespace: str,
    book_slug: str,
) -> tuple[CDPClient, subprocess.Popen[Any] | None]:
    chrome_proc: subprocess.Popen[Any] | None = None
    if not chrome_debug_available(args.port):
        profile = Path(args.profile_dir).resolve() if args.profile_dir else default_profile_path()
        chrome_proc = start_chrome(args.port, profile, book_url)
        wait_for_debug_port(args.port, timeout=30)

    page = page_for_book(args.port, namespace, book_slug)
    if not page:
        open_tab(args.port, book_url)
        time.sleep(3)
        page = page_for_book(args.port, namespace, book_slug)
    if not page:
        raise ExportError("Could not find or create a Yuque book page in Chrome.")

    cdp = CDPClient(page["webSocketDebuggerUrl"])
    cdp.connect()
    cdp.send("Runtime.enable")
    cdp.send("Page.enable")
    return cdp, chrome_proc


def is_yuque_cookie(cookie: dict[str, Any]) -> bool:
    domain = (cookie.get("domain") or "").lower()
    return any(
        token in domain
        for token in ("yuque.com", "nlark.com", "alipay.com", "alipayobjects.com", "alicdn.com", "aliyuncs.com")
    )


def save_auth_state(cdp: CDPClient, auth_file: Path, book_url: str) -> dict[str, Any]:
    cdp.send("Network.enable")
    cookies = cdp.send("Network.getAllCookies", timeout=20).get("result", {}).get("cookies", [])
    cookies = [cookie for cookie in cookies if is_yuque_cookie(cookie)]
    if not cookies:
        raise ExportError("No Yuque cookies found. Make sure login is complete in Chrome.")
    payload = {
        "version": 1,
        "bookUrl": book_url,
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


def wait_for_app_data(cdp: CDPClient, timeout: int, args: argparse.Namespace | None = None) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        check_stopped(args)
        ready = cdp.evaluate("!!(window.appData && window.appData.book && window.appData.book.toc)", timeout=10)
        if ready:
            return
        time.sleep(0.5)
    raise ExportError("语雀页面没有加载出 appData.book.toc，可能未登录或页面结构变化")


def load_book(cdp: CDPClient, book_url: str, args: argparse.Namespace | None = None) -> dict[str, Any]:
    cdp.navigate(book_url)
    wait_for_app_data(cdp, timeout=30, args=args)
    value = cdp.evaluate(
        "(() => ({book: {id: window.appData.book.id, slug: window.appData.book.slug, "
        "name: window.appData.book.name}, toc: window.appData.book.toc}))()",
        timeout=30,
    )
    if not isinstance(value, dict) or not value.get("book") or not value.get("toc"):
        raise ExportError("Failed to load Yuque book data")
    return value


YUQUE_CONVERTER_JS = r"""
(content, title) => {
  const images = [];
  function text(s) {
    return (s || "").replace(/\u00a0/g, " ").replace(/\n{3,}/g, "\n\n").trim();
  }
  function decodeCard(value) {
    try {
      if (value && value.startsWith("data:")) value = decodeURIComponent(value.slice(5));
      return value ? JSON.parse(value) : {};
    } catch (_) {
      return {};
    }
  }
  function inline(node) {
    if (node.nodeType === 3) return node.nodeValue.replace(/\s+/g, " ");
    if (node.nodeType !== 1) return "";
    const tag = node.tagName.toLowerCase();
    if (tag === "br") return "\n";
    if (tag === "img") {
      const src = node.getAttribute("src") || node.getAttribute("data-src") || "";
      const alt = node.getAttribute("alt") || "";
      if (src) images.push(src);
      return src ? "![" + alt + "](" + src + ")" : "";
    }
    if (tag === "card" || tag === "lake-card") {
      const name = node.getAttribute("name") || node.getAttribute("type") || "card";
      const data = decodeCard(node.getAttribute("value") || node.getAttribute("data-card-value") || "");
      if (name === "image" && data.src) {
        images.push(data.src);
        return "![" + (data.title || data.name || "image") + "](" + data.src + ")";
      }
      if (name === "math" && data.code) return "$" + String(data.code).replace(/\n/g, " ") + "$";
      if (name === "codeblock" && data.code) {
        return "\n```" + (data.mode || "") + "\n" + data.code + "\n```\n";
      }
      if (name === "diagram" && data.code) {
        const lang = data.type === "mermaid" ? "mermaid" : (data.type === "puml" ? "plantuml" : (data.type || ""));
        return "\n```" + lang + "\n" + data.code + "\n```\n";
      }
      if (data.url) {
        images.push(data.url);
        return "[" + name + "](" + data.url + ")";
      }
      return "[" + name + "]";
    }
    const inner = [...node.childNodes].map(inline).join("");
    if (tag === "strong" || tag === "b") return inner.trim() ? "**" + inner + "**" : inner;
    if (tag === "em" || tag === "i") return inner.trim() ? "*" + inner + "*" : inner;
    if (tag === "code") return "`" + inner.replace(/`/g, "\\`") + "`";
    if (tag === "a") {
      const href = node.getAttribute("href") || "";
      return href && inner.trim() ? "[" + inner.trim() + "](" + href + ")" : inner;
    }
    return inner;
  }
  function block(el) {
    if (!el || el.nodeType !== 1) return "";
    const tag = el.tagName.toLowerCase();
    if (tag === "meta" || tag === "style") return "";
    if (/^h[1-6]$/.test(tag)) return "#".repeat(+tag[1]) + " " + text(inline(el)) + "\n\n";
    if (tag === "p") return text(inline(el)) + "\n\n";
    if (tag === "pre") return "```\n" + (el.innerText || "").replace(/\n$/, "") + "\n```\n\n";
    if (tag === "blockquote") return text(el.innerText).split("\n").map(x => "> " + x).join("\n") + "\n\n";
    if (tag === "ul" || tag === "ol") {
      return [...el.children]
        .filter(x => x.tagName && x.tagName.toLowerCase() === "li")
        .map((li, i) => (tag === "ol" ? (i + 1) + ". " : "- ") + text(inline(li)))
        .join("\n") + "\n\n";
    }
    if (tag === "table") {
      const rows = [...el.querySelectorAll("tr")]
        .map(tr => [...tr.children].map(td => text(td.innerText).replace(/\n+/g, "<br>")))
        .filter(r => r.length);
      if (!rows.length) return "";
      const max = Math.max(...rows.map(r => r.length));
      rows.forEach(r => { while (r.length < max) r.push(""); });
      return "| " + rows[0].join(" | ") + " |\n| " + Array(max).fill("---").join(" | ") + " |\n"
        + rows.slice(1).map(r => "| " + r.join(" | ") + " |").join("\n") + "\n\n";
    }
    if (tag === "img" || tag === "card" || tag === "lake-card") return inline(el) + "\n\n";
    const own = [...el.children].map(block).join("");
    if (own.trim()) return own;
    const t = text(el.innerText);
    return t ? t + "\n\n" : "";
  }
  const doc = document.implementation.createHTMLDocument("yuque");
  doc.body.innerHTML = content || "";
  const md = [...doc.body.children].map(block).join("").replace(/\n{3,}/g, "\n\n").trim();
  return { markdown: "# " + title + "\n\n" + md + "\n", images };
}
"""


def fetch_doc_markdown(
    cdp: CDPClient,
    book_id: int,
    doc: dict[str, Any],
    args: argparse.Namespace | None = None,
) -> dict[str, Any]:
    check_stopped(args)
    throttle_request(args)
    slug = doc["url"]
    title = doc.get("title") or "未命名"
    expression = (
        "(async () => {"
        f"const j = await fetch('/api/docs/{slug}?include_contributors=true&include_like=true&include_hits=true"
        f"&merge_dynamic_data=false&book_id={book_id}').then(r => r.json());"
        f"const conv = ({YUQUE_CONVERTER_JS})(j.data.content || '', {js_string(title)});"
        "return { data: { id: j.data.id, title: j.data.title, slug: j.data.slug, "
        "content_updated_at: j.data.content_updated_at, word_count: j.data.word_count }, ...conv };"
        "})()"
    )
    value = cdp.evaluate(expression, timeout=120)
    if not isinstance(value, dict):
        raise ExportError(f"Unexpected Yuque doc response: {title}")
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


def download_image(url: str, dest_dir: Path, timeout: int) -> Path:
    import hashlib

    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
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


def node_has_children(toc: list[dict[str, Any]], uuid: str) -> bool:
    return any(item.get("parent_uuid") == uuid for item in toc)


def build_doc_paths(toc: list[dict[str, Any]], output: Path) -> tuple[dict[str, Path], dict[str, Path]]:
    children: dict[str, list[dict[str, Any]]] = {}
    by_uuid: dict[str, dict[str, Any]] = {}
    index_in_parent: dict[str, int] = {}
    for item in toc:
        by_uuid[item["uuid"]] = item
        children.setdefault(item.get("parent_uuid") or "ROOT", []).append(item)
    for siblings in children.values():
        for index, item in enumerate(siblings, start=1):
            index_in_parent[item["uuid"]] = index

    containers: dict[str, Path] = {"ROOT": output}

    def ensure_container(uuid: str) -> Path:
        if uuid in containers:
            return containers[uuid]
        item = by_uuid[uuid]
        parent = ensure_container(item.get("parent_uuid") or "ROOT")
        directory = parent / f"{pad(index_in_parent.get(uuid, 1))}-{sanitize_filename(item.get('title') or '未命名')}"
        directory.mkdir(parents=True, exist_ok=True)
        containers[uuid] = directory
        return directory

    for item in toc:
        if item.get("type") == "TITLE" or node_has_children(toc, item["uuid"]):
            ensure_container(item["uuid"])

    doc_paths: dict[str, Path] = {}
    for fallback_index, item in enumerate([x for x in toc if x.get("type") == "DOC"], start=1):
        parent_dir = ensure_container(item.get("parent_uuid") or "ROOT")
        index = index_in_parent.get(item["uuid"], fallback_index)
        doc_paths[str(item.get("doc_id") or item["uuid"])] = parent_dir / f"{pad(index)}-{sanitize_filename(item.get('title') or '未命名')}.md"
    return doc_paths, containers


def scan_exported_docs(output: Path) -> dict[str, Path]:
    exported: dict[str, Path] = {}
    if not output.exists():
        return exported
    pattern = re.compile(r"语雀文档ID:\s*(\d+)")
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
    book: dict[str, Any],
    toc: list[dict[str, Any]],
    doc_paths: dict[str, Path],
    selected_doc_ids: set[str] | None = None,
) -> None:
    index_path = output / "00-知识库入口.md"
    lines = [f"# {book.get('name') or '语雀知识库'}", "", "> 从语雀知识库导出。", ""]
    for item in toc:
        indent = "  " * max(0, int(item.get("level") or 0))
        if item.get("type") == "TITLE":
            lines.append(f"{indent}- **{item.get('title') or '未命名'}**")
        elif item.get("type") == "DOC":
            key = str(item.get("doc_id") or item["uuid"])
            if selected_doc_ids and key not in selected_doc_ids:
                continue
            doc_path = doc_paths.get(key)
            rel_path = os.path.relpath(doc_path, index_path.parent).replace("\\", "/") if doc_path else ""
            lines.append(f"{indent}- [{item.get('title') or '未命名'}]({rel_path})")
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def scan_book_toc(args: argparse.Namespace) -> dict[str, Any]:
    namespace, book_slug, book_url = parse_book_url(args.book_url)
    cdp, chrome_proc = connect_book_browser(args, book_url, namespace, book_slug)
    try:
        auth_file = auth_path_from_args(args)
        if auth_file.exists() and not args.skip_auth_load:
            cookie_count = load_auth_state(cdp, auth_file)
            emit(args, f"Loaded {cookie_count} auth cookies from {auth_file}")
            cdp.navigate(book_url)
            time.sleep(2)
        emit(args, "开始读取语雀目录。")
        data = load_book(cdp, book_url, args)
        toc = data.get("toc") or []
        data["totalDocs"] = sum(1 for item in toc if item.get("type") == "DOC")
        return data
    finally:
        cdp.close()
        if chrome_proc and args.close_started_chrome:
            chrome_proc.terminate()


def export_book(args: argparse.Namespace) -> dict[str, Any]:
    namespace, book_slug, book_url = parse_book_url(args.book_url)
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    cdp, chrome_proc = connect_book_browser(args, book_url, namespace, book_slug)
    try:
        auth_file = auth_path_from_args(args)
        if auth_file.exists() and not args.skip_auth_load:
            cookie_count = load_auth_state(cdp, auth_file)
            emit(args, f"Loaded {cookie_count} auth cookies from {auth_file}")
            cdp.navigate(book_url)
            time.sleep(2)

        emit(args, "Chrome page is ready. If login is required, finish login in Chrome.")
        if args.wait_login:
            input("Press Enter after the Yuque book page is logged in and visible...")

        data = load_book(cdp, book_url, args)
        book = data["book"]
        toc = data["toc"]
        selected_doc_ids = set(getattr(args, "selected_doc_ids", None) or [])
        docs = [
            item
            for item in toc
            if item.get("type") == "DOC" and (not selected_doc_ids or str(item.get("doc_id") or item["uuid"]) in selected_doc_ids)
        ]
        doc_paths, _ = build_doc_paths(toc, output)
        existing = scan_exported_docs(output)
        for doc_id, old_path in existing.items():
            doc_paths[doc_id] = old_path

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
            key = str(doc.get("doc_id") or doc["uuid"])
            md_path = doc_paths[key]
            if args.incremental and key in existing and not args.update_existing:
                skipped += 1
                continue
            try:
                result = fetch_doc_markdown(cdp, int(book["id"]), doc, args)
                markdown = result.get("markdown") or f"# {doc.get('title') or '未命名'}\n"
                markdown += (
                    f"\n---\n\n来源: https://www.yuque.com/{namespace}/{book_slug}/{doc.get('url')}\n"
                    f"语雀文档ID: {key}\n"
                )
                markdown, count, img_errors = localize_images(
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
                failures.append({"title": doc.get("title") or "", "slug": doc.get("url") or "", "error": str(exc)})

            if index % args.progress_every == 0 or index == len(docs):
                emit(
                    args,
                    f"progress {index}/{len(docs)} exported={exported} skipped={skipped} "
                    f"image_success={image_success} failures={len(failures)}",
                )

        write_index(output, book, toc, doc_paths, selected_doc_ids or None)
        report = {
            "provider": "yuque",
            "book": book,
            "bookUrl": book_url,
            "output": str(output),
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
    namespace, book_slug, book_url = parse_book_url(args.book_url)
    auth_file = auth_path_from_args(args)
    cdp, chrome_proc = connect_book_browser(args, book_url, namespace, book_slug)
    try:
        emit(args, "Chrome opened. Log in to Yuque in the browser.")
        if wait_callback:
            wait_callback()
        else:
            input("After login is complete and the Yuque book page is visible, press Enter...")
        check_stopped(args)
        cdp.navigate(book_url)
        time.sleep(2)
        check_stopped(args)
        result = save_auth_state(cdp, auth_file, book_url)
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
    root.title("语雀知识库导出工具")
    root.geometry("980x780")

    book_var = tk.StringVar(value=DEFAULT_BOOK_URL)
    output_var = tk.StringVar(value=str((PROJECT_DIR / "exports" / "yuque").resolve()))
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
    toc_state: dict[str, Any] = {"toc": [], "selected": set()}

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
        if not book_var.get().strip():
            raise ExportError("请填写语雀知识库 URL")
        if not output_var.get().strip():
            raise ExportError("请填写输出目录")
        return argparse.Namespace(
            book_url=book_var.get().strip(),
            output=output_var.get().strip(),
            port=int(port_var.get().strip() or DEFAULT_PORT),
            profile_dir=profile_var.get().strip() or None,
            auth_file=auth_var.get().strip() or str(default_auth_path()),
            skip_auth_load=False,
            wait_login=False,
            incremental=incremental,
            update_existing=update_existing,
            selected_doc_ids=selected_doc_ids,
            download_timeout=30,
            progress_every=20,
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
                "浏览器已经打开。\n\n请在浏览器里完成语雀登录，并确认知识库页面已经能正常打开。\n完成后回到这里点击“确定”，工具会保存登录凭证。",
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

    def doc_key(item: dict[str, Any]) -> str:
        return str(item.get("doc_id") or item.get("uuid") or "")

    def all_doc_ids() -> list[str]:
        return [doc_key(item) for item in toc_state.get("toc") or [] if item.get("type") == "DOC" and doc_key(item)]

    def refresh_toc_status() -> None:
        keys = all_doc_ids()
        selected = toc_state.get("selected") or set()
        if not keys:
            toc_status_var.set("目录：未读取")
        else:
            toc_status_var.set(f"目录：共 {len(keys)} 篇，已选择 {len(selected)} 篇")

    def render_toc_tree() -> None:
        toc_tree.delete(*toc_tree.get_children(""))
        toc = toc_state.get("toc") or []
        selected: set[str] = toc_state.get("selected") or set()
        children: dict[str, list[dict[str, Any]]] = {}
        for item in toc:
            children.setdefault(str(item.get("parent_uuid") or "ROOT"), []).append(item)

        def docs_under(uuid: str) -> list[str]:
            result: list[str] = []
            for child in children.get(uuid, []):
                if child.get("type") == "DOC":
                    key = doc_key(child)
                    if key:
                        result.append(key)
                result.extend(docs_under(str(child.get("uuid"))))
            return result

        def add_item(parent_iid: str, item: dict[str, Any]) -> None:
            uuid = str(item.get("uuid"))
            title = item.get("title") or "未命名"
            if item.get("type") == "DOC":
                key = doc_key(item)
                mark = "☑" if key in selected else "☐"
                toc_tree.insert(parent_iid, "end", iid=uuid, text=f"{mark} {title}")
            else:
                docs = docs_under(uuid)
                selected_count = sum(1 for key in docs if key in selected)
                mark = "☑" if docs and selected_count == len(docs) else ("◩" if selected_count else "☐")
                toc_tree.insert(parent_iid, "end", iid=uuid, text=f"{mark} {title}  ({selected_count}/{len(docs)})", open=True)
            for child in children.get(uuid, []):
                add_item(uuid, child)

        for item in children.get("ROOT", []):
            add_item("", item)
        refresh_toc_status()

    def set_all_toc_selected(selected: bool) -> None:
        keys = all_doc_ids()
        if not keys:
            messagebox.showinfo("还没有目录", "请先点击“读取目录”。")
            return
        toc_state["selected"] = set(keys) if selected else set()
        render_toc_tree()

    def invert_toc_selected() -> None:
        keys = set(all_doc_ids())
        if not keys:
            messagebox.showinfo("还没有目录", "请先点击“读取目录”。")
            return
        toc_state["selected"] = keys - set(toc_state.get("selected") or set())
        render_toc_tree()

    def selected_doc_ids_for_export() -> list[str] | None:
        if not toc_state.get("toc"):
            return None
        selected = sorted(toc_state.get("selected") or set())
        if not selected:
            raise ExportError("目录已读取，但没有选择任何文档。")
        return selected

    def toggle_toc_selection(event: Any | None = None) -> str:
        node = toc_tree.focus()
        if not node:
            return "break"
        toc = toc_state.get("toc") or []
        by_uuid = {str(item.get("uuid")): item for item in toc}
        children: dict[str, list[dict[str, Any]]] = {}
        for item in toc:
            children.setdefault(str(item.get("parent_uuid") or "ROOT"), []).append(item)
        selected: set[str] = toc_state.get("selected") or set()

        def docs_under(uuid: str) -> list[str]:
            item = by_uuid.get(uuid)
            if item and item.get("type") == "DOC":
                key = doc_key(item)
                return [key] if key else []
            result: list[str] = []
            for child in children.get(uuid, []):
                result.extend(docs_under(str(child.get("uuid"))))
            return result

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
                    k: result.get(k)
                    for k in ("totalDocs", "exportedDocs", "skippedDocs", "requestCount", "stopped", "imageSuccess", "imageFailureCount")
                    if k in result
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
        toc_state["toc"] = result.get("toc") or []
        toc_state["selected"] = set(all_doc_ids())
        render_toc_tree()

    def do_scan_toc() -> None:
        try:
            args = build_args(incremental=True, update_existing=False)
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        run_worker("读取目录", args, lambda: scan_book_toc(args), on_toc_loaded)

    def do_incremental() -> None:
        try:
            args = build_args(incremental=True, update_existing=False, selected_doc_ids=selected_doc_ids_for_export())
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        run_worker("增量更新缺失文档", args, lambda: export_book(args))

    def do_full_export() -> None:
        try:
            args = build_args(incremental=False, update_existing=True, selected_doc_ids=selected_doc_ids_for_export())
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        run_worker("全量覆盖导出", args, lambda: export_book(args))

    form = tk.Frame(root, padx=14, pady=12)
    form.pack(fill="x")
    form.columnconfigure(1, weight=1)

    def row(label: str, variable: tk.StringVar, row_index: int, browse: Callable[[], None] | None = None) -> None:
        tk.Label(form, text=label, anchor="w").grid(row=row_index, column=0, sticky="w", pady=5)
        tk.Entry(form, textvariable=variable).grid(row=row_index, column=1, sticky="ew", padx=8, pady=5)
        if browse:
            tk.Button(form, text="选择", command=browse).grid(row=row_index, column=2, pady=5)

    row("语雀知识库 URL", book_var, 0)
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
    parser = argparse.ArgumentParser(description="Export Yuque book to Markdown.")
    parser.add_argument("--gui", action="store_true", help="Open the graphical interface")
    parser.add_argument("--login", action="store_true", help="Open browser, let you log in, then save auth cookies")
    parser.add_argument("--book-url", help="Yuque book URL, for example https://www.yuque.com/user/book")
    parser.add_argument("--output", help="Output directory")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Chrome remote debugging port")
    parser.add_argument("--profile-dir", help=f"Chrome profile dir. Omit to auto-use {default_profile_path()}")
    parser.add_argument("--auth-file", help=f"Auth cookie file. Omit to auto-use {default_auth_path()}")
    parser.add_argument("--skip-auth-load", action="store_true", help="Do not load saved auth cookies before export")
    parser.add_argument("--wait-login", action="store_true", help="Pause for manual login before exporting")
    parser.add_argument("--incremental", action="store_true", help="Only export documents missing from local Markdown")
    parser.add_argument("--update-existing", action="store_true", help="With --incremental, update existing documents too")
    parser.add_argument("--download-timeout", type=int, default=30, help="Seconds to wait for each image download")
    parser.add_argument("--progress-every", type=int, default=20, help="Print progress after N documents")
    parser.add_argument("--request-delay", type=float, default=0.8, help="Fixed seconds to wait before each document/API request")
    parser.add_argument("--request-jitter", type=float, default=0.4, help="Extra random seconds added before each document/API request")
    parser.add_argument("--keep-remote-images", action="store_true", default=True, help="Keep remote image URLs when download fails")
    parser.add_argument("--drop-failed-images", dest="keep_remote_images", action="store_false", help="Remove image URL when download fails")
    parser.add_argument("--close-started-chrome", action="store_true", help="Close Chrome started by this script after export")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    if not argv or "--gui" in argv:
        return run_gui()

    args = parse_args(argv)
    try:
        if not args.book_url:
            raise ExportError("--book-url is required")
        if args.login:
            report = login_and_save_auth(args)
        else:
            if not args.output:
                raise ExportError("--output is required unless --login is used")
            report = export_book(args)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1
    keys = ("cookieCount", "authFile", "totalDocs", "exportedDocs", "skippedDocs", "requestCount", "stopped", "imageSuccess", "imageFailureCount")
    print(json.dumps({k: report[k] for k in keys if k in report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
