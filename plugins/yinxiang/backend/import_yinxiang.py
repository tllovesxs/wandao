#!/usr/bin/env python3
"""
Import local Markdown files into Yinxiang Note / Evernote.

Author: tllovesxs
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import mimetypes
import os
import re
import sqlite3
import struct
import sys
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wandao_core.logging import emit_legacy
from wandao_core.report import finalize_report
from wandao_core.source_paths import iter_regular_files_under_root, resolve_local_reference, source_root_for_file


class ImportErrorForUser(RuntimeError):
    """User-facing import error."""


def emit(message: str, *, event: str = "log.message", level: str = "info", **fields: Any) -> None:
    emit_legacy("yinxiang-import", message, event=event, level=level, **fields)


def emit_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)


def default_state_dir() -> Path:
    data_dir = os.environ.get("WANDAO_DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser().resolve() / "yinxiang"
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "wandao" / "yinxiang"


def default_database() -> Path:
    return default_state_dir() / "yinxiang_china.db"


def safe_title(value: str, fallback: str = "未命名笔记", max_len: int = 120) -> str:
    text = (value or "").strip() or fallback
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        text = fallback
    while len(text.encode("utf-8")) > max_len:
        text = text[:-1]
    return text or fallback


def safe_notebook_name(value: str, fallback: str = "万能导导入", max_len: int = 100) -> str:
    text = safe_title(value, fallback=fallback, max_len=max_len)
    return text.replace("/", "／").replace("\\", "＼")


def image_dimensions(body: bytes, mime: str) -> tuple[int, int] | None:
    """Read basic image dimensions without adding a heavyweight image dependency."""
    try:
        if mime == "image/png" and body.startswith(b"\x89PNG\r\n\x1a\n") and len(body) >= 24:
            return struct.unpack(">II", body[16:24])
        if mime == "image/gif" and body[:6] in {b"GIF87a", b"GIF89a"} and len(body) >= 10:
            return struct.unpack("<HH", body[6:10])
        if mime in {"image/jpeg", "image/jpg"} and body.startswith(b"\xff\xd8"):
            index = 2
            while index + 9 < len(body):
                if body[index] != 0xFF:
                    index += 1
                    continue
                marker = body[index + 1]
                index += 2
                if marker in {0xD8, 0xD9}:
                    continue
                if index + 2 > len(body):
                    break
                segment_length = int.from_bytes(body[index : index + 2], "big")
                if segment_length < 2:
                    break
                if marker in {
                    0xC0,
                    0xC1,
                    0xC2,
                    0xC3,
                    0xC5,
                    0xC6,
                    0xC7,
                    0xC9,
                    0xCA,
                    0xCB,
                    0xCD,
                    0xCE,
                    0xCF,
                }:
                    height = int.from_bytes(body[index + 3 : index + 5], "big")
                    width = int.from_bytes(body[index + 5 : index + 7], "big")
                    return width, height
                index += segment_length
    except Exception:
        return None
    return None


def require_evernote_api() -> None:
    try:
        import evernote_backup  # noqa: F401
        import evernote  # noqa: F401
    except ModuleNotFoundError as exc:
        raise ImportErrorForUser(
            "缺少依赖 evernote-backup。请先执行：python -m pip install -r requirements.txt"
        ) from exc


def load_auth(database: Path) -> dict[str, str]:
    if not database.exists():
        raise ImportErrorForUser("未找到印象笔记本地同步库，请先在“印象笔记导出”里登录并同步一次。")

    con = sqlite3.connect(database)
    try:
        rows = dict(con.execute("select name,value from config"))
    finally:
        con.close()

    token = rows.get("auth_token") or ""
    if not token:
        raise ImportErrorForUser("本地同步库里没有找到印象笔记登录凭证，请重新登录并同步。")
    return {
        "backend": rows.get("backend") or "china",
        "token": token,
        "user": rows.get("user") or "",
    }


def note_store(database: Path, retry: int) -> Any:
    require_evernote_api()
    from evernote_backup.evernote_client import EvernoteClient  # type: ignore

    auth = load_auth(database)
    client = EvernoteClient(
        backend=auth["backend"],
        token=auth["token"],
        network_error_retry_count=retry,
    )
    return client.note_store


@dataclass
class ResourceRef:
    hash_hex: str
    mime: str
    file_name: str
    resource: Any


class MarkdownToEnml:
    def __init__(self, md_path: Path, source_root: Path | None = None) -> None:
        self.md_path = md_path.resolve()
        self.source_root = source_root or source_root_for_file(None, self.md_path)
        self.resources: dict[Path, ResourceRef] = {}
        self.first_heading = ""

    def convert(self) -> tuple[str, list[Any], str]:
        text = self.md_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        body = self._render_lines(lines)
        content = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">\n'
            f"<en-note>{body}</en-note>"
        )
        return content, [item.resource for item in self.resources.values()], self.first_heading

    def _render_lines(self, lines: list[str]) -> str:
        parts: list[str] = []
        paragraph: list[str] = []
        list_type: str | None = None
        code_lines: list[str] = []
        in_code = False

        def close_paragraph() -> None:
            if paragraph:
                parts.append(f"<p>{self._inline(' '.join(paragraph))}</p>")
                paragraph.clear()

        def close_list() -> None:
            nonlocal list_type
            if list_type:
                parts.append(f"</{list_type}>")
                list_type = None

        for raw_line in lines:
            line = raw_line.rstrip("\n")
            stripped = line.strip()

            if stripped.startswith("```"):
                if in_code:
                    parts.append(f"<pre>{html.escape(chr(10).join(code_lines))}</pre>")
                    code_lines.clear()
                    in_code = False
                else:
                    close_paragraph()
                    close_list()
                    in_code = True
                continue

            if in_code:
                code_lines.append(line)
                continue

            if not stripped:
                close_paragraph()
                close_list()
                continue

            heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
            if heading:
                close_paragraph()
                close_list()
                level = min(6, len(heading.group(1)))
                title = heading.group(2).strip()
                if not self.first_heading:
                    self.first_heading = re.sub(r"[*_`]+", "", title).strip()
                parts.append(f"<h{level}>{self._inline(title)}</h{level}>")
                continue

            unordered = re.match(r"^[-*+]\s+(.+)$", stripped)
            ordered = re.match(r"^\d+[.)]\s+(.+)$", stripped)
            if unordered or ordered:
                close_paragraph()
                wanted = "ul" if unordered else "ol"
                if list_type != wanted:
                    close_list()
                    list_type = wanted
                    parts.append(f"<{wanted}>")
                item = (unordered or ordered).group(1)
                parts.append(f"<li>{self._inline(item)}</li>")
                continue

            if stripped.startswith(">"):
                close_paragraph()
                close_list()
                quote = stripped.lstrip(">").strip()
                parts.append(f"<blockquote>{self._inline(quote)}</blockquote>")
                continue

            paragraph.append(stripped)

        if in_code:
            parts.append(f"<pre>{html.escape(chr(10).join(code_lines))}</pre>")
        close_paragraph()
        close_list()
        return "\n".join(parts)

    def _inline(self, value: str) -> str:
        placeholders: dict[str, str] = {}

        def keep(raw: str) -> str:
            key = f"@@WANDAO_PLACEHOLDER_{len(placeholders)}@@"
            placeholders[key] = raw
            return key

        def image_replacer(match: re.Match[str]) -> str:
            alt = match.group(1).strip()
            target = match.group(2).strip()
            resource = self._resource_from_link(target)
            if not resource:
                return html.escape(alt or target)
            return keep(f'<en-media type="{html.escape(resource.mime)}" hash="{resource.hash_hex}"/>')

        def link_replacer(match: re.Match[str]) -> str:
            label = match.group(1).strip()
            target = match.group(2).strip()
            resource = self._resource_from_link(target)
            if resource:
                return keep(f'<en-media type="{html.escape(resource.mime)}" hash="{resource.hash_hex}"/>')
            href = html.escape(target, quote=True)
            return keep(f'<a href="{href}">{html.escape(label)}</a>')

        value = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", image_replacer, value)
        value = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link_replacer, value)
        value = html.escape(value)
        value = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", value)
        value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
        value = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", value)
        for key, raw in placeholders.items():
            value = value.replace(key, raw)
        return value

    def _resource_from_link(self, raw_target: str) -> ResourceRef | None:
        target = raw_target.strip().strip("<>")
        parsed = urllib.parse.urlparse(target)
        if parsed.scheme in {"http", "https", "mailto"}:
            return None
        path = resolve_local_reference(self.source_root, self.md_path, target)
        if not path:
            return None
        return self._build_resource(path)

    def _build_resource(self, path: Path) -> ResourceRef:
        cached = self.resources.get(path)
        if cached:
            return cached

        require_evernote_api()
        from evernote.edam.type import ttypes as Types  # type: ignore

        body = path.read_bytes()
        digest = hashlib.md5(body)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

        data = Types.Data()
        data.size = len(body)
        data.bodyHash = digest.digest()
        data.body = body

        attrs = Types.ResourceAttributes()
        attrs.fileName = path.name
        attrs.attachment = not mime.startswith("image/")

        resource = Types.Resource()
        resource.mime = mime
        resource.data = data
        resource.attributes = attrs
        if mime.startswith("image/"):
            dimensions = image_dimensions(body, mime)
            if dimensions:
                resource.width, resource.height = dimensions

        ref = ResourceRef(
            hash_hex=digest.hexdigest(),
            mime=mime,
            file_name=path.name,
            resource=resource,
        )
        self.resources[path] = ref
        return ref


def markdown_files(source_dir: Path, limit: int = 0) -> list[Path]:
    source_dir = source_dir.resolve()
    files = sorted(
        iter_regular_files_under_root(source_dir, suffixes={".md"}),
        key=lambda path: path.relative_to(source_dir).as_posix(),
    )
    if limit > 0:
        files = files[:limit]
    return files


def scan_source(args: argparse.Namespace) -> dict[str, Any]:
    source = args.source_dir.resolve()
    files = markdown_files(source, args.max_import)
    return {
        "provider": "yinxiang-import",
        "sourceDir": str(source),
        "docCount": len(files),
        "sampleDocs": [
            {
                "relativePath": path.relative_to(source).as_posix(),
                "title": detect_title(path),
                "size": path.stat().st_size,
            }
            for path in files[:20]
        ],
    }


def detect_title(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[:80]:
            match = re.match(r"^#\s+(.+)$", line.strip())
            if match:
                return safe_title(match.group(1), path.stem)
    except OSError:
        pass
    return safe_title(path.stem)


def get_or_create_notebook(store: Any, name: str, stack: str = "") -> Any:
    require_evernote_api()
    from evernote.edam.type import ttypes as Types  # type: ignore

    name = safe_notebook_name(name)
    stack = safe_notebook_name(stack, fallback="", max_len=100) if stack else ""
    for notebook in store.listNotebooks():
        if (notebook.name or "") == name and (notebook.stack or "") == stack:
            return notebook

    notebook = Types.Notebook()
    notebook.name = name
    if stack:
        notebook.stack = stack
    return store.createNotebook(notebook)


def target_notebook_for(args: argparse.Namespace, md_path: Path) -> tuple[str, str]:
    if not args.preserve_folders:
        return args.notebook, args.stack

    try:
        relative_parent = md_path.parent.relative_to(args.source_dir.resolve())
    except ValueError:
        return args.notebook, args.stack
    parts = [part for part in relative_parent.parts if part and part != "."]
    if not parts:
        return args.notebook, args.stack
    if len(parts) == 1:
        return parts[0], args.stack
    return " / ".join(parts[1:]), parts[0]


def import_markdown_file(args: argparse.Namespace, store: Any, md_path: Path) -> dict[str, Any]:
    require_evernote_api()
    from evernote.edam.type import ttypes as Types  # type: ignore

    converter = MarkdownToEnml(md_path, source_root_for_file(getattr(args, "source_dir", None), md_path))
    content, resources, first_heading = converter.convert()
    title = safe_title(first_heading or md_path.stem)
    notebook_name, stack_name = target_notebook_for(args, md_path)
    notebook = get_or_create_notebook(store, notebook_name, stack_name)

    note = Types.Note()
    note.title = title
    note.content = content
    note.notebookGuid = notebook.guid
    note.resources = resources

    created = store.createNote(note)
    return {
        "path": str(md_path),
        "title": created.title,
        "guid": created.guid,
        "notebook": notebook.name,
        "stack": notebook.stack or "",
        "resourceCount": len(resources),
    }


def import_one(args: argparse.Namespace) -> dict[str, Any]:
    if not args.yes:
        raise ImportErrorForUser("这是写入操作，请添加 --yes 确认导入。")
    md_path = args.source_file or next(iter(markdown_files(args.source_dir.resolve(), 1)), None)
    if not md_path:
        raise ImportErrorForUser("未找到可导入的 Markdown 文件。")
    md_path = md_path.resolve()
    emit(
        f"开始导入印象笔记 Markdown：{md_path.name}",
        event="task.started",
        totals={"documents": 1},
        source=str(md_path),
    )
    store = note_store(args.database, args.retry)
    emit(
        f"开始导入文档：{md_path.name}",
        event="document.import.started",
        doc={"path": str(md_path), "index": 1},
    )
    created = import_markdown_file(args, store, md_path)
    report = {
        "provider": "yinxiang-import",
        "importMode": "one",
        "totalDocs": 1,
        "importedDocs": 1,
        "importedCount": 1,
        "imported": [created],
    }
    report = finalize_report(report, provider="yinxiang-import", mode="import", output=md_path)
    emit(
        f"印象笔记文档导入完成：{created.get('title') or md_path.name}",
        event="document.import.completed",
        doc={"path": str(md_path), "id": created.get("guid", ""), "title": created.get("title", ""), "index": 1},
        stats={"attachmentSuccess": created.get("resourceCount", 0)},
    )
    emit(
        "印象笔记 Markdown 导入完成",
        event="task.completed",
        level="success",
        stats={"importedDocs": 1, "failureCount": 0},
    )
    return report


def import_all(args: argparse.Namespace) -> dict[str, Any]:
    if not args.yes:
        raise ImportErrorForUser("这是写入操作，请添加 --yes 确认导入。")
    files = markdown_files(args.source_dir.resolve(), args.max_import)
    store = note_store(args.database, args.retry)
    imported: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    total = len(files)
    emit(
        f"开始批量导入印象笔记 Markdown：共 {total} 篇。",
        event="task.started",
        totals={"documents": total},
        sourceDir=str(args.source_dir.resolve()),
    )

    for index, md_path in enumerate(files, start=1):
        try:
            emit(
                f"开始导入文档：{md_path.relative_to(args.source_dir.resolve()).as_posix()}",
                event="document.import.started",
                doc={"path": str(md_path), "index": index},
            )
            result = import_markdown_file(args, store, md_path)
            imported.append(result)
            emit(
                f"印象笔记文档导入完成：{result.get('title') or md_path.name}",
                event="document.import.completed",
                doc={"path": str(md_path), "id": result.get("guid", ""), "title": result.get("title", ""), "index": index},
                stats={"attachmentSuccess": result.get("resourceCount", 0)},
            )
        except Exception as exc:
            failures.append({"path": str(md_path), "error": str(exc)})
            emit(
                f"印象笔记文档导入失败：{md_path.name}：{exc}",
                event="document.import.failed",
                level="error",
                doc={"path": str(md_path), "index": index},
                error={"type": type(exc).__name__, "message": str(exc)},
            )
        if args.request_delay > 0 and index < total:
            time.sleep(args.request_delay)
        if index % max(1, args.progress_every) == 0 or index == total:
            emit(
                "progress "
                f"done={index} queued={max(0, total - index)} "
                f"imported={len(imported)} failures={len(failures)}",
                event="task.progress",
                progress={"current": index, "total": total},
                stats={"importedDocs": len(imported), "failureCount": len(failures)},
            )

    report = {
        "provider": "yinxiang-import",
        "importMode": "all",
        "sourceDir": str(args.source_dir.resolve()),
        "sourceDocCount": total,
        "totalDocs": total,
        "importedDocs": len(imported),
        "importedCount": len(imported),
        "failureCount": len(failures),
        "imported": imported,
        "failures": failures,
    }
    report = finalize_report(report, provider="yinxiang-import", mode="import", output=args.source_dir.resolve())
    emit(
        "印象笔记 Markdown 导入完成" if not failures else f"印象笔记 Markdown 导入完成，但有 {len(failures)} 个失败项",
        event="task.completed",
        level="success" if not failures else "warn",
        stats={"importedDocs": len(imported), "failureCount": len(failures)},
    )
    return report


def run_gui() -> int:
    import subprocess
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext

    root = tk.Tk()
    root.title("万能导 - 印象笔记 Markdown 导入")
    root.geometry("820x640")

    username_var = tk.StringVar()
    password_var = tk.StringVar()
    source_var = tk.StringVar()
    file_var = tk.StringVar()
    notebook_var = tk.StringVar(value="万能导导入")
    stack_var = tk.StringVar()
    preserve_var = tk.BooleanVar(value=True)

    frame = tk.Frame(root)
    frame.pack(fill="both", expand=True, padx=16, pady=16)

    tk.Label(frame, text="印象笔记账号").pack(anchor="w")
    tk.Entry(frame, textvariable=username_var).pack(fill="x", pady=(0, 8))
    tk.Label(frame, text="印象笔记密码").pack(anchor="w")
    tk.Entry(frame, textvariable=password_var, show="*").pack(fill="x", pady=(0, 8))

    tk.Label(frame, text="Markdown 目录").pack(anchor="w")
    source_row = tk.Frame(frame)
    source_row.pack(fill="x", pady=(0, 8))
    tk.Entry(source_row, textvariable=source_var).pack(side="left", fill="x", expand=True)
    tk.Button(
        source_row,
        text="浏览",
        command=lambda: source_var.set(filedialog.askdirectory() or source_var.get()),
    ).pack(side="left", padx=(8, 0))

    tk.Label(frame, text="单篇测试文件（可选）").pack(anchor="w")
    file_row = tk.Frame(frame)
    file_row.pack(fill="x", pady=(0, 8))
    tk.Entry(file_row, textvariable=file_var).pack(side="left", fill="x", expand=True)
    tk.Button(
        file_row,
        text="浏览",
        command=lambda: file_var.set(
            filedialog.askopenfilename(filetypes=[("Markdown 文件", "*.md"), ("所有文件", "*.*")])
            or file_var.get()
        ),
    ).pack(side="left", padx=(8, 0))

    tk.Label(frame, text="默认目标笔记本").pack(anchor="w")
    tk.Entry(frame, textvariable=notebook_var).pack(fill="x", pady=(0, 8))
    tk.Label(frame, text="默认笔记本组").pack(anchor="w")
    tk.Entry(frame, textvariable=stack_var).pack(fill="x", pady=(0, 8))
    tk.Checkbutton(frame, text="按本地目录创建笔记本组/笔记本", variable=preserve_var).pack(anchor="w")

    log_box = scrolledtext.ScrolledText(frame, height=16)
    log_box.pack(fill="both", expand=True, pady=12)

    def append_log(text: str) -> None:
        log_box.insert("end", text)
        log_box.see("end")
        root.update_idletasks()

    def run_action(args: list[str], stdin_text: str | None = None) -> None:
        command = [sys.executable, str(Path(__file__).resolve()), *args]
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        if stdin_text is not None and proc.stdin:
            proc.stdin.write(stdin_text)
            proc.stdin.close()
        assert proc.stdout
        for line in proc.stdout:
            append_log(line)
        if proc.wait() != 0:
            messagebox.showerror("执行失败", "请查看日志。")

    def common_args() -> list[str]:
        if not source_var.get().strip():
            raise ImportErrorForUser("请选择 Markdown 目录。")
        args = ["--source-dir", source_var.get().strip(), "--notebook", notebook_var.get().strip() or "万能导导入"]
        if file_var.get().strip():
            args.extend(["--source-file", file_var.get().strip()])
        if stack_var.get().strip():
            args.extend(["--stack", stack_var.get().strip()])
        if preserve_var.get():
            args.append("--preserve-folders")
        return args

    def login_sync() -> None:
        if not username_var.get().strip() or not password_var.get():
            messagebox.showwarning("缺少信息", "请填写账号和密码。")
            return
        script = Path(__file__).resolve().parent / "export_yinxiang.py"
        proc = subprocess.Popen(
            [sys.executable, str(script), "--init-auth", "--username", username_var.get().strip(), "--password-stdin"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        if proc.stdin:
            proc.stdin.write(password_var.get() + "\n")
            proc.stdin.close()
        assert proc.stdout
        for line in proc.stdout:
            append_log(line)
        if proc.wait() != 0:
            messagebox.showerror("执行失败", "请查看日志。")

    actions = tk.Frame(frame)
    actions.pack(fill="x")
    tk.Button(actions, text="登录并同步凭证", command=login_sync).pack(side="left", padx=(0, 8))
    tk.Button(actions, text="扫描目录", command=lambda: run_action([*common_args(), "--scan-source"])).pack(side="left", padx=(0, 8))
    tk.Button(actions, text="单篇导入测试", command=lambda: run_action([*common_args(), "--import-one", "--yes"])).pack(side="left", padx=(0, 8))
    tk.Button(actions, text="批量导入", command=lambda: run_action([*common_args(), "--import-all", "--yes"])).pack(side="left", padx=(0, 8))
    tk.Button(actions, text="退出", command=root.destroy).pack(side="right")

    root.mainloop()
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导入本地 Markdown 到印象笔记")
    parser.add_argument("--gui", action="store_true", help="打开简易图形界面")
    parser.add_argument("--database", type=Path, default=default_database(), help="印象笔记本地同步库路径")
    parser.add_argument("--source-dir", type=Path, default=Path.cwd(), help="Markdown 源目录")
    parser.add_argument("--source-file", type=Path, help="单篇 Markdown 文件")
    parser.add_argument("--notebook", default="万能导导入", help="默认目标笔记本")
    parser.add_argument("--stack", default="", help="默认笔记本组")
    parser.add_argument("--preserve-folders", action="store_true", help="按本地目录映射笔记本组/笔记本")
    parser.add_argument("--scan-source", action="store_true", help="扫描 Markdown 导入计划")
    parser.add_argument("--import-one", action="store_true", help="导入一篇 Markdown")
    parser.add_argument("--import-all", action="store_true", help="批量导入 Markdown")
    parser.add_argument("--max-import", type=int, default=0, help="最多导入数量，0 表示全部")
    parser.add_argument("--request-delay", type=float, default=0.3, help="批量导入请求间隔秒")
    parser.add_argument("--progress-every", type=int, default=1, help="每处理多少篇输出一次进度")
    parser.add_argument("--retry", type=int, default=3, help="网络重试次数")
    parser.add_argument("--yes", action="store_true", help="确认执行写入操作")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        args.database = args.database.expanduser().resolve()
        args.source_dir = args.source_dir.expanduser().resolve()
        if args.source_file:
            args.source_file = args.source_file.expanduser().resolve()

        if args.gui:
            print("旧版 Python GUI 已废弃，请使用 Electron 桌面端：start-wandao.cmd 或 ./start-wandao.sh", file=sys.stderr)
            return 2
        if args.scan_source:
            emit_json(scan_source(args))
            return 0
        if args.import_one:
            emit_json(import_one(args))
            return 0
        if args.import_all:
            emit_json(import_all(args))
            return 0
        raise ImportErrorForUser("请指定 --scan-source、--import-one 或 --import-all。")
    except KeyboardInterrupt:
        emit("已停止。", event="task.stopped", level="warn")
        return 130
    except ImportErrorForUser as exc:
        emit(
            str(exc),
            event="task.failed",
            level="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        return 1
    except Exception as exc:
        emit(
            f"印象笔记导入失败：{exc}",
            event="task.failed",
            level="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
