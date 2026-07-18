#!/usr/bin/env python3
"""Export local desktop OneNote notebooks to Markdown.

The OneNote desktop COM API is not a regular web API.  In practice, Python COM
wrappers are less reliable for OneNote on some Office installations, so this
script compiles a tiny C# bridge on demand and keeps all export/conversion logic
in Python.
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
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

from wandao_core.checkpoint import add_checkpoint_args, open_checkpoint_from_args
from wandao_cli import extend_arg_list_from_file
from wandao_core.logging import emit_legacy
from wandao_core.report import finalize_report


PROJECT_DIR = Path(__file__).resolve().parent
FORBIDDEN_FILENAME_CHARS = r'<>:"/\|?*'


CSHARP_BRIDGE_SOURCE = r'''
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Office.Interop.OneNote;

public static class WandaoOneNoteBridge
{
    private const int RpcCallFailed = unchecked((int)0x800706BE);

    private static bool HasPublishedFile(string outputPath)
    {
        return File.Exists(outputPath) && new FileInfo(outputPath).Length > 0;
    }

    private static void ReleaseApplication(Application app)
    {
        if (app != null && Marshal.IsComObject(app))
        {
            Marshal.FinalReleaseComObject(app);
        }
    }

    private static string EncodeMessage(Exception ex)
    {
        return Convert.ToBase64String(Encoding.UTF8.GetBytes(ex.Message ?? ex.ToString()));
    }

    private static void WritePublishResult(string pageId, string status, Exception error = null)
    {
        string message = error == null ? "" : EncodeMessage(error);
        Console.WriteLine("publish-result\t" + pageId + "\t" + status + "\t" + message);
    }

    public static int Main(string[] args)
    {
        try
        {
            if (args.Length < 2)
            {
                Console.Error.WriteLine("usage: WandaoOneNoteBridge hierarchy <xmlPath> | publish-list <listPath>");
                return 2;
            }

            if (args[0] == "hierarchy")
            {
                var app = new Application();
                try
                {
                    string xml;
                    app.GetHierarchy(null, HierarchyScope.hsPages, out xml, XMLSchema.xs2013);
                    File.WriteAllText(args[1], xml, new UTF8Encoding(false));
                }
                finally
                {
                    ReleaseApplication(app);
                }
                return 0;
            }

            if (args[0] == "publish-list")
            {
                int index = 0;
                Application app = null;
                foreach (string rawLine in File.ReadLines(args[1], Encoding.UTF8))
                {
                    if (String.IsNullOrWhiteSpace(rawLine)) continue;
                    int tab = rawLine.IndexOf('\t');
                    if (tab <= 0) throw new Exception("Invalid publish list line: " + rawLine);
                    string pageId = rawLine.Substring(0, tab);
                    string outputPath = rawLine.Substring(tab + 1);
                    Directory.CreateDirectory(Path.GetDirectoryName(outputPath));
                    index++;
                    Console.WriteLine("publish\t" + index + "\t" + outputPath);
                    try
                    {
                        if (File.Exists(outputPath)) File.Delete(outputPath);
                        if (app == null) app = new Application();
                        app.Publish(pageId, outputPath, PublishFormat.pfMHTML, "");
                        if (!HasPublishedFile(outputPath))
                        {
                            throw new Exception("OneNote Publish returned without creating an MHT file.");
                        }
                        WritePublishResult(pageId, "ok");
                    }
                    catch (COMException ex)
                    {
                        if (ex.ErrorCode != RpcCallFailed)
                        {
                            ReleaseApplication(app);
                            app = null;
                            WritePublishResult(pageId, "failed", ex);
                            continue;
                        }
                        if (HasPublishedFile(outputPath))
                        {
                            ReleaseApplication(app);
                            app = null;
                            WritePublishResult(pageId, "recovered-output", ex);
                            continue;
                        }

                        try
                        {
                            if (File.Exists(outputPath)) File.Delete(outputPath);
                            ReleaseApplication(app);
                            app = null;
                            Console.WriteLine("publish-retry\t" + index + "\t" + pageId);
                            Thread.Sleep(3000);
                            app = new Application();
                            app.Publish(pageId, outputPath, PublishFormat.pfMHTML, "");
                            if (!HasPublishedFile(outputPath))
                            {
                                throw new Exception("OneNote Publish retry returned without creating an MHT file.");
                            }
                            WritePublishResult(pageId, "retried", ex);
                        }
                        catch (Exception retryEx)
                        {
                            ReleaseApplication(app);
                            app = null;
                            WritePublishResult(pageId, "failed", retryEx);
                        }
                    }
                    catch (Exception ex)
                    {
                        ReleaseApplication(app);
                        app = null;
                        WritePublishResult(pageId, "failed", ex);
                    }
                }
                ReleaseApplication(app);
                return 0;
            }

            Console.Error.WriteLine("unknown command: " + args[0]);
            return 2;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex.ToString());
            return 1;
        }
    }
}
'''.strip()


class ExportError(RuntimeError):
    """User-facing export error."""


@dataclass
class TocNode:
    id: str
    node_id: str
    type: str
    title: str
    parent_node_id: str
    selectable: bool
    order: int
    path_parts: list[str]
    page_level: int = 0
    parent_page_id: str = ""
    ancestor_page_ids: list[str] | None = None
    section_id: str = ""


def emit(message: str, *, event: str = "log.message", level: str = "info", **fields: Any) -> None:
    emit_legacy("onenote-export", message, event=event, level=level, **fields)


def emit_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)


def default_state_dir() -> Path:
    data_dir = os.environ.get("WANDAO_DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser().resolve() / "onenote"
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "wandao" / "onenote"


def default_output_dir() -> Path:
    return PROJECT_DIR / "exports" / "onenote"


def safe_name(value: str, fallback: str = "untitled", max_len: int = 120) -> str:
    text = (value or "").strip() or fallback
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        text = fallback
    while len(text.encode("utf-8")) > max_len:
        text = text[:-1]
    return text or fallback


def short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:10]


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def node_name(element: ET.Element) -> str:
    return element.attrib.get("name") or element.attrib.get("nickname") or "未命名"


def node_id(prefix: str, raw_id: str) -> str:
    return f"onenote-{prefix}:{raw_id}"


def parse_int(value: str | None, default: int = 1) -> int:
    try:
        return int(value or "")
    except (TypeError, ValueError):
        return default


def find_interop_dll() -> Path:
    override = os.environ.get("ONENOTE_INTEROP_DLL")
    if override:
        path = Path(override).expanduser().resolve()
        if path.exists():
            return path

    windir = Path(os.environ.get("WINDIR") or r"C:\Windows")
    base = windir / "assembly" / "GAC_MSIL" / "Microsoft.Office.Interop.OneNote"
    candidates = list(base.glob("*/Microsoft.Office.Interop.OneNote.dll"))

    def version_key(path: Path) -> tuple[int, ...]:
        version = path.parent.name.split("__", 1)[0]
        return tuple(parse_int(part, 0) for part in version.split("."))

    candidates.sort(key=version_key, reverse=True)
    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise ExportError(
        "未找到 Microsoft.Office.Interop.OneNote.dll。请确认已安装 Windows 桌面版 OneNote/Office，"
        "不是仅安装 Microsoft Store 版 OneNote。"
    )


def find_csc() -> Path:
    override = os.environ.get("CSC")
    if override and Path(override).exists():
        return Path(override)

    windir = Path(os.environ.get("WINDIR") or r"C:\Windows")
    candidates = [
        windir / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe",
        windir / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe",
    ]
    which = shutil.which("csc")
    if which:
        candidates.append(Path(which))

    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise ExportError("未找到 .NET Framework C# 编译器 csc.exe，无法调用 OneNote COM。")


def ensure_bridge(helper_dir: Path | None = None) -> Path:
    if sys.platform != "win32":
        raise ExportError("OneNote 导出当前只支持 Windows 桌面版 OneNote。")

    helper_dir = helper_dir or default_state_dir() / "bridge"
    helper_dir.mkdir(parents=True, exist_ok=True)
    source_path = helper_dir / "WandaoOneNoteBridge.cs"
    exe_path = helper_dir / "WandaoOneNoteBridge.exe"
    stamp_path = helper_dir / "WandaoOneNoteBridge.sha1"
    source_hash = hashlib.sha1(CSHARP_BRIDGE_SOURCE.encode("utf-8")).hexdigest()

    if not source_path.exists() or source_path.read_text(encoding="utf-8", errors="ignore") != CSHARP_BRIDGE_SOURCE:
        source_path.write_text(CSHARP_BRIDGE_SOURCE, encoding="utf-8")

    if exe_path.exists() and stamp_path.exists() and stamp_path.read_text(encoding="utf-8").strip() == source_hash:
        return exe_path

    interop = find_interop_dll()
    csc = find_csc()
    emit("正在准备 OneNote 本地桥接组件...")
    proc = subprocess.run(
        [
            str(csc),
            "/nologo",
            "/target:exe",
            f"/out:{exe_path}",
            f"/reference:{interop}",
            str(source_path),
        ],
        cwd=str(helper_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise ExportError(
            "编译 OneNote 桥接组件失败：\n"
            + (proc.stderr or proc.stdout or f"csc exited with {proc.returncode}")
        )
    stamp_path.write_text(source_hash, encoding="utf-8")
    return exe_path


def run_bridge(args: list[str], helper_dir: Path | None = None, stream: bool = False) -> str:
    exe = ensure_bridge(helper_dir)
    if not stream:
        proc = subprocess.run(
            [str(exe), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            raise ExportError(proc.stderr or proc.stdout or f"OneNote bridge exited with {proc.returncode}")
        return proc.stdout

    proc = subprocess.Popen(
        [str(exe), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout_lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\r\n")
        stdout_lines.append(line)
        if line.startswith("publish\t"):
            parts = line.split("\t", 2)
            if len(parts) >= 2:
                emit(f"OneNote 正在导出页面 MHT：{parts[1]}")
        elif line:
            emit(line)
    stderr = proc.stderr.read() if proc.stderr else ""
    code = proc.wait()
    if code != 0:
        raise ExportError(stderr or "\n".join(stdout_lines) or f"OneNote bridge exited with {code}")
    return "\n".join(stdout_lines)


def load_hierarchy_xml(args: argparse.Namespace) -> str:
    helper_dir = Path(args.helper_dir).expanduser().resolve() if args.helper_dir else None
    with tempfile.TemporaryDirectory(prefix="wandao-onenote-hierarchy-") as tmp:
        xml_path = Path(tmp) / "hierarchy.xml"
        run_bridge(["hierarchy", str(xml_path)], helper_dir=helper_dir)
        return xml_path.read_text(encoding="utf-8", errors="replace")


def parse_hierarchy(xml_text: str) -> tuple[list[TocNode], list[TocNode]]:
    root = ET.fromstring(xml_text)
    nodes: list[TocNode] = []
    pages: list[TocNode] = []
    order = 0

    def next_order() -> int:
        nonlocal order
        order += 1
        return order

    def add_container(element: ET.Element, type_name: str, parent_node: str, path_parts: list[str]) -> TocNode:
        raw_id = element.attrib.get("ID") or f"{type_name}-{next_order()}"
        title = node_name(element)
        current_path = [*path_parts, title]
        toc = TocNode(
            id=raw_id,
            node_id=node_id(type_name, raw_id),
            type=type_name,
            title=title,
            parent_node_id=parent_node,
            selectable=False,
            order=next_order(),
            path_parts=current_path,
        )
        nodes.append(toc)
        return toc

    def parse_section(section: ET.Element, parent_node: str, path_parts: list[str]) -> None:
        section_node = add_container(section, "section", parent_node, path_parts)
        page_stack: dict[int, TocNode] = {}
        for child in list(section):
            if local_name(child.tag) != "Page":
                continue
            raw_id = child.attrib.get("ID") or f"page-{next_order()}"
            title = node_name(child)
            level = max(1, parse_int(child.attrib.get("pageLevel"), 1))
            parent_page = page_stack.get(level - 1)
            parent_node_id = parent_page.node_id if parent_page else section_node.node_id
            ancestor_ids = [*(parent_page.ancestor_page_ids or []), parent_page.id] if parent_page else []
            page = TocNode(
                id=raw_id,
                node_id=node_id("page", raw_id),
                type="page",
                title=title,
                parent_node_id=parent_node_id,
                selectable=True,
                order=next_order(),
                path_parts=section_node.path_parts,
                page_level=level,
                parent_page_id=parent_page.id if parent_page else "",
                ancestor_page_ids=ancestor_ids,
                section_id=section_node.id,
            )
            nodes.append(page)
            pages.append(page)
            page_stack[level] = page
            for key in list(page_stack):
                if key > level:
                    page_stack.pop(key, None)

    def walk(element: ET.Element, parent_node: str, path_parts: list[str]) -> None:
        for child in list(element):
            tag = local_name(child.tag)
            if tag == "Notebook":
                notebook = add_container(child, "notebook", parent_node, path_parts)
                walk(child, notebook.node_id, notebook.path_parts)
            elif tag == "SectionGroup":
                group = add_container(child, "section-group", parent_node, path_parts)
                walk(child, group.node_id, group.path_parts)
            elif tag == "Section":
                parse_section(child, parent_node, path_parts)

    walk(root, "", [])
    return nodes, pages


def toc_json(nodes: list[TocNode], pages: list[TocNode]) -> dict[str, Any]:
    return {
        "platform": "onenote",
        "nodes": [
            {
                "nodeId": node.node_id,
                "exportId": node.id if node.selectable else "",
                "title": node.title,
                "parentNodeId": node.parent_node_id,
                "selectable": node.selectable,
                "type": node.type,
                "pageLevel": node.page_level,
                "order": node.order,
            }
            for node in nodes
        ],
        "pageCount": len(pages),
    }


class PathPlanner:
    def __init__(self, output: Path, pages_by_id: dict[str, TocNode], child_page_ids: set[str]) -> None:
        self.output = output
        self.pages_by_id = pages_by_id
        self.child_page_ids = child_page_ids
        self.folder_cache: dict[tuple[tuple[str, ...], str], str] = {}
        self.used_folders: dict[tuple[str, ...], set[str]] = {}
        self.used_files: dict[tuple[str, ...], set[str]] = {}

    def unique_folder(self, parent_key: tuple[str, ...], raw_id: str, title: str) -> str:
        cache_key = (parent_key, raw_id)
        if cache_key in self.folder_cache:
            return self.folder_cache[cache_key]
        used = self.used_folders.setdefault(parent_key, set())
        base = safe_name(title, fallback="未命名")
        candidate = base
        index = 2
        while candidate.lower() in used:
            candidate = f"{base} ({index})"
            index += 1
        used.add(candidate.lower())
        self.folder_cache[cache_key] = candidate
        return candidate

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

    def parent_dir_for_page(self, page: TocNode) -> tuple[Path, tuple[str, ...]]:
        parts = [safe_name(part, fallback="未命名") for part in page.path_parts]
        logical_key: tuple[str, ...] = ("section", page.section_id or "::".join(page.path_parts))
        for ancestor_id in page.ancestor_page_ids or []:
            ancestor = self.pages_by_id.get(ancestor_id)
            if not ancestor:
                continue
            component = self.unique_folder(logical_key, ancestor.id, ancestor.title)
            parts.append(component)
            logical_key = (*logical_key, ancestor.id)
        return self.output.joinpath(*parts), logical_key

    def markdown_path(self, page: TocNode) -> Path:
        parent_dir, parent_key = self.parent_dir_for_page(page)
        if page.id in self.child_page_ids:
            folder = self.unique_folder(parent_key, page.id, page.title)
            return parent_dir / folder / "README.md"
        return parent_dir / self.unique_file(parent_key, page.title)


def normalize_location(value: str) -> list[str]:
    text = html.unescape((value or "").strip()).replace("\\", "/")
    if not text:
        return []
    decoded = urllib.parse.unquote(text)
    names = {text, decoded}
    parsed = urllib.parse.urlparse(decoded)
    if parsed.path:
        names.add(parsed.path.lstrip("/"))
        names.add(PurePosixPath(parsed.path).name)
    names.add(PurePosixPath(decoded).name)
    return [item for item in names if item]


def extension_for_part(content_type: str, location: str) -> str:
    suffix = Path(PurePosixPath(urllib.parse.unquote(location or "")).name).suffix
    if suffix:
        return suffix
    guessed = mimetypes.guess_extension(content_type or "")
    return guessed or ".bin"


@dataclass
class MhtPayload:
    html_doc: str
    assets: dict[str, tuple[bytes, str]]
    attachments: list[tuple[str, bytes, str]]


def parse_mht(path: Path) -> MhtPayload:
    message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    html_doc = ""
    assets: dict[str, tuple[bytes, str]] = {}
    attachments: list[tuple[str, bytes, str]] = []
    image_index = 0
    attachment_index = 0

    for part in message.walk():
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        if content_type == "text/html":
            try:
                html_doc = part.get_content()
            except Exception:
                payload = part.get_payload(decode=True) or b""
                html_doc = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            continue

        payload = part.get_payload(decode=True) or b""
        if not payload:
            continue

        location = part.get("Content-Location") or part.get_filename() or ""
        if PurePosixPath(urllib.parse.unquote(location).replace("\\", "/")).name.lower() == "filelist.xml":
            continue
        ext = extension_for_part(content_type, location)
        if content_type.startswith("image/"):
            image_index += 1
            fallback = f"image{image_index:03d}{ext}"
            for key in normalize_location(location) or [fallback]:
                assets[key] = (payload, ext)
            cid = (part.get("Content-ID") or "").strip("<>")
            if cid:
                assets[f"cid:{cid}"] = (payload, ext)
        else:
            attachment_index += 1
            fallback = f"attachment{attachment_index:03d}{ext}"
            name = safe_name(Path(PurePosixPath(urllib.parse.unquote(location)).name).name or fallback)
            attachments.append((name, payload, ext))

    return MhtPayload(html_doc=html_doc, assets=assets, attachments=attachments)


def markdown_link_path(value: str) -> str:
    return value.replace("\\", "/").replace(" ", "%20")


class OneNoteHtmlToMarkdown(HTMLParser):
    def __init__(self, save_image: Any) -> None:
        super().__init__(convert_charrefs=True)
        self.save_image = save_image
        self.blocks: list[str] = []
        self.current: list[str] = []
        self.current_tag = ""
        self.current_style = ""
        self.skip_depth = 0
        self.table_rows: list[list[str]] | None = None
        self.current_row: list[str] | None = None
        self.current_cell: list[str] | None = None

    def flush_current(self) -> None:
        text = "".join(self.current)
        text = html.unescape(text).replace("\xa0", " ")
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text).strip()
        self.current = []
        if not text or text == "已使用 OneNote 创建。":
            return
        prefix = ""
        if self.current_tag == "li":
            prefix = "- "
        elif self.current_tag in {"h1", "h2", "h3"}:
            prefix = "#" * int(self.current_tag[1]) + " "
        else:
            font_size = self.font_size()
            if font_size >= 18:
                prefix = "## "
            elif font_size >= 14:
                prefix = "### "
        self.blocks.append(prefix + text)

    def font_size(self) -> float:
        match = re.search(r"font-size\s*:\s*([0-9.]+)\s*pt", self.current_style, re.I)
        if not match:
            return 0.0
        try:
            return float(match.group(1))
        except ValueError:
            return 0.0

    def attrs_dict(self, attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key.lower(): value or "" for key, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr = self.attrs_dict(attrs)
        if tag in {"head", "script", "style", "title"}:
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
        if tag in {"p", "li", "h1", "h2", "h3", "h4"}:
            self.flush_current()
            self.current_tag = "h3" if tag == "h4" else tag
            self.current_style = attr.get("style", "")
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
            self.current.append("")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"head", "script", "style", "title"} and self.skip_depth:
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
        if tag in {"p", "li", "h1", "h2", "h3", "h4"}:
            self.flush_current()
            self.current_tag = ""
            self.current_style = ""

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.table_rows is not None and self.current_cell is not None:
            self.current_cell.append(data)
            return
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


def convert_mht_to_markdown(mht_path: Path, md_path: Path) -> dict[str, int]:
    payload = parse_mht(mht_path)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    asset_dir = md_path.parent / f"{md_path.stem}_assets"
    saved_assets: dict[str, str] = {}
    image_count = 0

    def save_image(src: str, alt: str = "") -> str:
        nonlocal image_count
        keys = normalize_location(src)
        found: tuple[bytes, str] | None = None
        found_key = ""
        for key in keys:
            if key in payload.assets:
                found = payload.assets[key]
                found_key = key
                break
        if not found:
            return ""
        if found_key in saved_assets:
            return saved_assets[found_key]
        image_count += 1
        data, ext = found
        asset_dir.mkdir(parents=True, exist_ok=True)
        filename = f"image{image_count:03d}{ext}"
        (asset_dir / filename).write_bytes(data)
        rel = f"{asset_dir.name}/{filename}"
        saved_assets[found_key] = rel
        return rel

    parser = OneNoteHtmlToMarkdown(save_image)
    parser.feed(payload.html_doc or "")
    markdown = parser.result()

    attachment_count = 0
    if payload.attachments:
        asset_dir.mkdir(parents=True, exist_ok=True)
        markdown += "\n## 附件\n\n"
        used: set[str] = set()
        for raw_name, data, ext in payload.attachments:
            attachment_count += 1
            name = raw_name if Path(raw_name).suffix else f"{raw_name}{ext}"
            base = safe_name(name, fallback=f"attachment{attachment_count:03d}{ext}")
            candidate = base
            index = 2
            while candidate.lower() in used:
                stem = Path(base).stem
                suffix = Path(base).suffix
                candidate = f"{stem} ({index}){suffix}"
                index += 1
            used.add(candidate.lower())
            (asset_dir / candidate).write_bytes(data)
            markdown += f"- [{candidate}]({markdown_link_path(asset_dir.name + '/' + candidate)})\n"

    if not markdown.strip():
        markdown = md_path.stem + "\n"
    md_path.write_text(markdown, encoding="utf-8")
    return {
        "images": len(saved_assets),
        "attachments": attachment_count,
        "chars": len(markdown),
    }


def child_page_ids(pages: list[TocNode]) -> set[str]:
    return {page.parent_page_id for page in pages if page.parent_page_id}


def selected_pages(args: argparse.Namespace, pages: list[TocNode]) -> list[TocNode]:
    if not args.selected_doc_ids:
        return pages
    by_id = {page.id: page for page in pages}
    missing = [doc_id for doc_id in args.selected_doc_ids if doc_id not in by_id]
    if missing:
        raise ExportError("选择的 OneNote 页面不存在或目录已变化，请重新读取目录：" + ", ".join(missing[:5]))
    selected_set = set(args.selected_doc_ids)
    return [page for page in pages if page.id in selected_set]


def load_doc_id_file(args: argparse.Namespace) -> None:
    try:
        extend_arg_list_from_file(args, "selected_doc_ids")
    except (FileNotFoundError, ValueError) as exc:
        raise ExportError(str(exc)) from exc


def write_publish_list(items: list[tuple[TocNode, Path]], path: Path) -> None:
    lines = [f"{page.id}\t{mht_path}" for page, mht_path in items]
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_publish_results(output: str) -> dict[str, dict[str, str]]:
    results: dict[str, dict[str, str]] = {}
    for line in output.splitlines():
        parts = line.split("\t", 3)
        if len(parts) < 3 or parts[0] != "publish-result":
            continue
        page_id, status = parts[1], parts[2]
        encoded_message = parts[3] if len(parts) > 3 else ""
        try:
            message = base64.b64decode(encoded_message, validate=True).decode("utf-8", errors="replace") if encoded_message else ""
        except (ValueError, UnicodeError):
            message = "OneNote bridge returned an invalid diagnostic payload."
        results[page_id] = {"status": status, "message": message}
    return results


def publish_mht_items(
    items: list[tuple[TocNode, Path]],
    list_path: Path,
    *,
    helper_dir: Path | None,
) -> dict[str, dict[str, str]]:
    try:
        for _page, mht_path in items:
            if mht_path.exists():
                mht_path.unlink()
        write_publish_list(items, list_path)
        output = run_bridge(["publish-list", str(list_path)], helper_dir=helper_dir, stream=True)
    except (ExportError, OSError) as exc:
        return {page.id: {"status": "failed", "message": str(exc)} for page, _mht in items}

    results = parse_publish_results(output)
    for page, _mht in items:
        results.setdefault(
            page.id,
            {
                "status": "failed",
                "message": "OneNote bridge ended before reporting the page publish result.",
            },
        )
    return results


def export_onenote(args: argparse.Namespace, nodes: list[TocNode], pages: list[TocNode]) -> dict[str, Any]:
    output = Path(args.output).expanduser().resolve() if args.output else default_output_dir()
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = open_checkpoint_from_args(args, "onenote", "export")
    pages_by_id = {page.id: page for page in pages}
    planner = PathPlanner(output, pages_by_id, child_page_ids(pages))
    selected = selected_pages(args, pages)
    helper_dir = Path(args.helper_dir).expanduser().resolve() if args.helper_dir else None

    if checkpoint:
        checkpoint.start_task(
            {
                "source": "local-onenote",
                "outputDir": str(output),
                "totalDocs": len(selected),
                "resume": bool(getattr(args, "resume", False)),
                "retryFailed": bool(getattr(args, "retry_failed", False)),
            }
        )
        for page in selected:
            checkpoint.upsert_item(
                f"onenote:page:{page.id}",
                title=page.title,
                source_url="/".join(page.path_parts),
                source_id=page.id,
                parent_key=page.parent_node_id,
                metadata={
                    "id": page.id,
                    "path": page.path_parts,
                    "level": page.page_level,
                    "parentNodeId": page.parent_node_id,
                    "sectionId": page.section_id,
                },
            )
        if getattr(args, "retry_failed", False):
            selected = [page for page in selected if checkpoint.item_status(f"onenote:page:{page.id}") == "failed"]

    targets: list[tuple[TocNode, Path]] = []
    skipped = 0
    for page in selected:
        md_path = planner.markdown_path(page)
        if checkpoint and getattr(args, "resume", False) and checkpoint.item_status(f"onenote:page:{page.id}") == "completed":
            skipped += 1
            continue
        if args.incremental and md_path.exists():
            if checkpoint:
                checkpoint.complete_item(
                    f"onenote:page:{page.id}",
                    local_path=str(md_path),
                    metadata={"id": page.id, "skippedExisting": True},
                )
            skipped += 1
            continue
        targets.append((page, md_path))

    started = time.time()
    failures: list[dict[str, str]] = []
    image_success = 0
    attachment_success = 0
    exported = 0
    emit(
        f"开始导出 OneNote 页面：共 {len(targets)} 篇。",
        event="task.started",
        totals={"documents": len(targets), "selected": len(selected), "skippedExisting": skipped},
        output=str(output),
    )

    mht_root: Path
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if args.keep_mht:
        mht_root = output / "_onenote_mht"
        if mht_root.exists() and not args.incremental:
            shutil.rmtree(mht_root)
        mht_root.mkdir(parents=True, exist_ok=True)
    else:
        temp_dir = tempfile.TemporaryDirectory(prefix="wandao-onenote-mht-")
        mht_root = Path(temp_dir.name)

    try:
        publish_items: list[tuple[TocNode, Path]] = []
        for index, (page, _md_path) in enumerate(targets, start=1):
            mht_path = mht_root / f"{index:04d}-{short_hash(page.id)}.mht"
            publish_items.append((page, mht_path))

        if publish_items:
            list_path = mht_root / "publish-list.tsv"
            emit(f"开始调用 OneNote 导出 MHT：{len(publish_items)} 篇")
            for page, _mht_path in publish_items:
                if checkpoint:
                    checkpoint.start_item(f"onenote:page:{page.id}", "publish")
            publish_results = publish_mht_items(publish_items, list_path, helper_dir=helper_dir)
        else:
            publish_results = {}

        mht_by_id = {page.id: mht for page, mht in publish_items}
        total = len(targets)
        for index, (page, md_path) in enumerate(targets, start=1):
            item_key = f"onenote:page:{page.id}"
            publish_result = publish_results.get(page.id, {"status": "failed", "message": "Missing OneNote publish result."})
            if publish_result["status"] == "failed":
                error_message = publish_result["message"] or "OneNote did not create an MHT file for this page."
                if checkpoint:
                    checkpoint.fail_item(item_key, error_message)
                failures.append({
                    "id": page.id,
                    "title": page.title,
                    "path": str(md_path),
                    "stage": "publish",
                    "error": error_message,
                })
                emit(
                    f"OneNote 页面 MHT 导出失败，已跳过并继续后续页面：{page.title}：{error_message}",
                    event="document.export.failed",
                    level="error",
                    doc={"id": page.id, "title": page.title, "index": index, "path": str(md_path)},
                    error={"type": "OneNotePublishError", "message": error_message},
                )
            else:
                if publish_result["status"] in {"recovered-output", "retried"}:
                    emit(
                        f"OneNote 页面发布已恢复：{page.title}（{publish_result['status']}）",
                        event="document.export.recovered",
                        level="warn",
                        doc={"id": page.id, "title": page.title, "index": index, "path": str(md_path)},
                    )
            try:
                if publish_result["status"] != "failed":
                    if checkpoint:
                        checkpoint.start_item(item_key, "convert")
                    emit(
                        f"开始转换 OneNote 页面：{page.title}",
                        event="document.export.started",
                        doc={"id": page.id, "title": page.title, "index": index, "path": str(md_path)},
                    )
                    stats = convert_mht_to_markdown(mht_by_id[page.id], md_path)
                    image_success += stats["images"]
                    attachment_success += stats["attachments"]
                    exported += 1
                    if checkpoint:
                        checkpoint.complete_item(item_key, local_path=str(md_path), metadata={"id": page.id})
                    emit(
                        f"OneNote 页面导出完成：{page.title}",
                        event="document.export.completed",
                        doc={"id": page.id, "title": page.title, "index": index, "path": str(md_path)},
                        stats={
                            "imageSuccessInDoc": stats["images"],
                            "attachmentSuccessInDoc": stats["attachments"],
                            "chars": stats["chars"],
                        },
                    )
            except Exception as exc:  # noqa: BLE001 - keep exporting other pages.
                if checkpoint:
                    checkpoint.fail_item(item_key, str(exc))
                failures.append({
                    "id": page.id,
                    "title": page.title,
                    "path": str(md_path),
                    "error": str(exc),
                })
                emit(
                    f"OneNote 页面导出失败：{page.title}：{exc}",
                    event="document.export.failed",
                    level="error",
                    doc={"id": page.id, "title": page.title, "index": index, "path": str(md_path)},
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
            if index % max(1, args.progress_every) == 0 or index == total:
                emit(
                    "progress "
                    f"{index}/{total} exported={exported} skipped={skipped} "
                    f"image_success={image_success} failures={len(failures)}",
                    event="task.progress",
                    progress={"current": index, "total": total},
                    stats={
                        "exportedDocs": exported,
                        "skippedDocs": skipped,
                        "imageSuccess": image_success,
                        "attachmentSuccess": attachment_success,
                        "failureCount": len(failures),
                    },
                )
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()

    report = {
        "platform": "onenote",
        "output": str(output),
        "total": len(selected),
        "exported": exported,
        "skipped": skipped,
        "failures": failures,
        "imageSuccess": image_success,
        "attachmentSuccess": attachment_success,
        "elapsedSeconds": round(time.time() - started, 2),
        "keptMht": str(mht_root) if args.keep_mht else "",
    }
    if checkpoint:
        report["checkpoint"] = checkpoint.stats()
    report_path = output / "00-导出报告.json"
    report = finalize_report(report, provider="onenote", mode="export", report_file=report_path, output=output)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if checkpoint:
        if failures:
            checkpoint.fail_task(f"{len(failures)} 个文档失败", status="failed")
        else:
            checkpoint.complete_task(report)
        checkpoint.close()
    emit(
        "OneNote 导出完成" if not failures else f"OneNote 导出完成，但有 {len(failures)} 个失败项",
        event="task.completed",
        level="success" if not failures else "warn",
        reportFile=str(report_path),
        stats={
            "exportedDocs": exported,
            "skippedDocs": skipped,
            "imageSuccess": image_success,
            "attachmentSuccess": attachment_success,
            "failureCount": len(failures),
        },
    )
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出 Windows 桌面版 OneNote 为 Markdown")
    parser.add_argument("--gui", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--scan-toc", action="store_true", help="读取 OneNote 本地目录并输出 JSON")
    parser.add_argument("--output", default=str(default_output_dir()), help="输出目录")
    parser.add_argument("--doc-id", action="append", dest="selected_doc_ids", default=[], help="只导出指定页面 ID，可重复")
    parser.add_argument("--doc-id-file", default="", help="从文件读取要导出的页面 ID，JSON 数组或逐行文本均可")
    parser.add_argument("--incremental", action="store_true", help="目标 Markdown 已存在时跳过")
    add_checkpoint_args(parser)
    parser.add_argument("--progress-every", type=int, default=1, help="每处理多少篇输出一次进度")
    parser.add_argument("--request-delay", default="0", help=argparse.SUPPRESS)
    parser.add_argument("--request-jitter", default="0", help=argparse.SUPPRESS)
    parser.add_argument("--keep-mht", action="store_true", help="保留 OneNote 中间 MHT 文件，便于排障")
    parser.add_argument("--helper-dir", default="", help="OneNote 桥接组件缓存目录")
    args = parser.parse_args(argv)
    load_doc_id_file(args)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        xml_text = load_hierarchy_xml(args)
        nodes, pages = parse_hierarchy(xml_text)
        if args.scan_toc:
            emit_json(toc_json(nodes, pages))
            return 0
        result = export_onenote(args, nodes, pages)
        emit_json(result)
        return 0 if not result.get("failures") else 1
    except ExportError as exc:
        emit(
            f"OneNote 导出任务失败：{exc}",
            event="task.failed",
            level="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    except Exception as exc:  # noqa: BLE001
        emit(
            f"OneNote 导出任务失败：{exc}",
            event="task.failed",
            level="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        print(f"OneNote 导出失败：{exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
