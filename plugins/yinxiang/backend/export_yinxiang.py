#!/usr/bin/env python3
"""
Export Yinxiang Note notebooks to Markdown.

Author: tllovesxs
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import html
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from wandao_core.checkpoint import add_checkpoint_args, open_checkpoint_from_args
from wandao_cli import extend_arg_list_from_file
from wandao_core.logging import emit_legacy
from wandao_core.report import finalize_report


class ExportError(RuntimeError):
    """User-facing export error."""


def emit(message: str, *, event: str = "log.message", level: str = "info", **fields: Any) -> None:
    emit_legacy("yinxiang-export", message, event=event, level=level, **fields)


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


def default_output_dir() -> Path:
    return Path.cwd() / "exports" / "yinxiang"


def safe_name(value: str, fallback: str = "untitled", max_len: int = 120) -> str:
    text = (value or "").strip() or fallback
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        text = fallback
    while len(text.encode("utf-8")) > max_len:
        text = text[:-1]
    return text or fallback


def ensure_unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 1
    while True:
        candidate = parent / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def require_evernote_backup() -> Any:
    try:
        from evernote_backup import cli_app  # type: ignore

        return cli_app
    except ModuleNotFoundError as exc:
        raise ExportError(
            "缺少依赖 evernote-backup。请先在当前 Python 环境执行：\n"
            "python -m pip install evernote-backup==1.13.1\n"
            "如果你使用源码运行，也可以执行：python -m pip install -r requirements.txt"
        ) from exc


def patch_evernote_progress() -> None:
    """Avoid click progressbar requiring an active click context."""
    import io

    import evernote_backup.cli_app_util as cli_app_util  # type: ignore
    import evernote_backup.note_exporter as note_exporter  # type: ignore
    import evernote_backup.note_synchronizer as note_synchronizer  # type: ignore

    cli_app_util.get_progress_output = lambda: io.StringIO()
    note_exporter.get_progress_output = lambda: io.StringIO()
    note_synchronizer.get_progress_output = lambda: io.StringIO()


def read_password(args: argparse.Namespace) -> str | None:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    if args.password_env:
        return os.environ.get(args.password_env)
    if args.init_auth:
        return getpass.getpass("印象笔记密码：")
    return None


def init_auth(args: argparse.Namespace) -> None:
    cli_app = require_evernote_backup()
    patch_evernote_progress()

    if not args.username:
        raise ExportError("请填写印象笔记账号。")
    password = read_password(args)
    if not password:
        raise ExportError("请填写印象笔记密码。")

    database = args.database
    database.parent.mkdir(parents=True, exist_ok=True)

    emit("正在初始化印象笔记本地同步库...")
    if database.exists() and not args.force:
        cli_app.reauth(
            database=database,
            auth_user=args.username,
            auth_password=password,
            auth_oauth_port=0,
            auth_oauth_host="localhost",
            auth_token=None,
            network_retry_count=args.retry,
            use_system_ssl_ca=False,
            custom_api_data=None,
        )
    else:
        cli_app.init_db(
            database=database,
            auth_user=args.username,
            auth_password=password,
            auth_oauth_port=0,
            auth_oauth_host="localhost",
            auth_token=None,
            force=True,
            backend=args.backend,
            network_retry_count=args.retry,
            use_system_ssl_ca=False,
            custom_api_data=None,
        )

    emit("印象笔记凭证已保存到本地同步库，开始同步目录和笔记...")
    sync_notes(args)


def sync_notes(args: argparse.Namespace) -> None:
    cli_app = require_evernote_backup()
    patch_evernote_progress()

    if not args.database.exists():
        raise ExportError("未找到印象笔记本地同步库，请先点击“登录并同步”。")

    emit("正在同步印象笔记内容到本地...")
    started = time.time()
    cli_app.sync(
        database=args.database,
        max_chunk_results=args.max_chunk_results,
        max_download_workers=args.max_workers,
        download_cache_memory_limit=args.cache_memory_limit,
        network_retry_count=args.retry,
        use_system_ssl_ca=False,
        include_tasks=False,
        token=None,
    )
    emit(f"同步完成，用时 {time.time() - started:.1f}s")


def open_storage(database: Path) -> Any:
    require_evernote_backup()
    from evernote_backup.cli_app_storage import get_storage  # type: ignore

    if not database.exists():
        raise ExportError("未找到印象笔记本地同步库，请先点击“登录并同步”。")
    return get_storage(database)


def scan_toc(args: argparse.Namespace) -> dict[str, Any]:
    storage = open_storage(args.database)

    notebooks_data: list[dict[str, Any]] = []
    total = 0
    notebooks = sorted(
        list(storage.notebooks.iter_notebooks()),
        key=lambda nb: ((nb.stack or ""), nb.name or ""),
    )
    for notebook in notebooks:
        notes = []
        for note in storage.notes.iter_notes(notebook.guid):
            notes.append(
                {
                    "guid": note.guid,
                    "title": note.title or "未命名笔记",
                    "created": note.created,
                    "updated": note.updated,
                }
            )
        notes.sort(key=lambda item: item["title"])
        total += len(notes)
        notebooks_data.append(
            {
                "guid": notebook.guid,
                "name": notebook.name or "未命名笔记本",
                "stack": notebook.stack or "",
                "notes": notes,
            }
        )

    return {
        "provider": "yinxiang",
        "database": str(args.database),
        "notebookCount": len(notebooks_data),
        "totalNotes": total,
        "notebooks": notebooks_data,
    }


def export_enex(args: argparse.Namespace) -> Path:
    cli_app = require_evernote_backup()
    patch_evernote_progress()

    enex_dir = args.enex_dir or (args.output / ".enex-cache")
    if enex_dir.resolve() == args.output.resolve():
        raise ExportError("ENEX 中间目录不能和 Markdown 输出目录相同。")
    if enex_dir.exists():
        shutil.rmtree(enex_dir)
    enex_dir.mkdir(parents=True, exist_ok=True)

    emit("正在生成 ENEX 中间文件...")
    cli_app.export(
        database=args.database,
        single_notes=True,
        include_trash=args.include_trash,
        no_export_date=False,
        add_guid=True,
        add_metadata=True,
        overwrite=False,
        notebooks=tuple(),
        tags=tuple(),
        output_path=enex_dir,
    )
    return enex_dir


@dataclass
class ResourceInfo:
    file_name: str
    mime: str
    relative_link: str
    absolute_path: Path


class EnmlMarkdownParser(HTMLParser):
    def __init__(self, resources: dict[str, ResourceInfo]) -> None:
        super().__init__(convert_charrefs=True)
        self.resources = resources
        self.parts: list[str] = []
        self.link_stack: list[str] = []
        self.list_stack: list[str] = []
        self.pre_depth = 0
        self.heading_stack: list[str] = []

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t]+\n", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()

    def _append(self, value: str) -> None:
        self.parts.append(value)

    def _newline(self, count: int = 1) -> None:
        current = "".join(self.parts)
        if not current:
            return
        need = "\n" * count
        if not current.endswith(need):
            self._append(need)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        tag = tag.lower()

        if tag in {"div", "p", "section"}:
            self._newline(1)
        elif tag == "br":
            self._append("\n")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._newline(2)
            marks = "#" * int(tag[1])
            self.heading_stack.append(marks)
            self._append(f"{marks} ")
        elif tag in {"strong", "b"}:
            self._append("**")
        elif tag in {"em", "i"}:
            self._append("*")
        elif tag == "u":
            self._append("<u>")
        elif tag == "a":
            self.link_stack.append(attrs_dict.get("href", ""))
            self._append("[")
        elif tag in {"ul", "ol"}:
            self.list_stack.append(tag)
            self._newline(1)
        elif tag == "li":
            self._newline(1)
            indent = "  " * max(0, len(self.list_stack) - 1)
            marker = "1. " if self.list_stack and self.list_stack[-1] == "ol" else "- "
            self._append(f"{indent}{marker}")
        elif tag == "blockquote":
            self._newline(2)
            self._append("> ")
        elif tag == "pre":
            self._newline(2)
            self.pre_depth += 1
            self._append("```\n")
        elif tag == "code" and not self.pre_depth:
            self._append("`")
        elif tag == "en-todo":
            checked = attrs_dict.get("checked", "").lower() in {"true", "checked", "1"}
            self._append("[x] " if checked else "[ ] ")
        elif tag == "en-media":
            digest = attrs_dict.get("hash", "").lower()
            resource = self.resources.get(digest)
            if resource:
                if resource.mime.startswith("image/"):
                    self._append(f"![{resource.file_name}]({resource.relative_link})")
                else:
                    self._append(f"[{resource.file_name}]({resource.relative_link})")
            else:
                self._append("[附件]")
        elif tag in {"table", "tr"}:
            self._newline(1)
        elif tag in {"td", "th"}:
            self._append(" | ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"div", "p", "section"}:
            self._newline(1)
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            if self.heading_stack:
                self.heading_stack.pop()
            self._newline(2)
        elif tag in {"strong", "b"}:
            self._append("**")
        elif tag in {"em", "i"}:
            self._append("*")
        elif tag == "u":
            self._append("</u>")
        elif tag == "a":
            href = self.link_stack.pop() if self.link_stack else ""
            self._append(f"]({href})" if href else "]")
        elif tag in {"ul", "ol"}:
            if self.list_stack:
                self.list_stack.pop()
            self._newline(1)
        elif tag == "li":
            self._newline(1)
        elif tag == "blockquote":
            self._newline(2)
        elif tag == "pre":
            if self.pre_depth:
                self.pre_depth -= 1
            self._append("\n```")
            self._newline(2)
        elif tag == "code" and not self.pre_depth:
            self._append("`")
        elif tag in {"table", "tr"}:
            self._newline(1)

    def handle_data(self, data: str) -> None:
        if not data:
            return
        if self.pre_depth:
            self._append(data)
        else:
            self._append(re.sub(r"\s+", " ", data))


def parse_time(value: str | None) -> str:
    if not value:
        return ""
    match = re.match(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z", value)
    if not match:
        return value
    year, month, day, hour, minute, second = match.groups()
    return f"{year}-{month}-{day} {hour}:{minute}:{second} UTC"


def clean_enml(content: str) -> str:
    text = content or ""
    text = re.sub(r"<\?xml[^>]*\?>", "", text, flags=re.I)
    text = re.sub(r"<!DOCTYPE[^>]*>", "", text, flags=re.I | re.S)
    return text.strip()


def resource_extension(mime: str) -> str:
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "application/pdf": ".pdf",
        "audio/mpeg": ".mp3",
        "video/mp4": ".mp4",
    }
    return mapping.get(mime.lower(), ".bin")


def extract_resources(note_el: ET.Element, md_path: Path) -> dict[str, ResourceInfo]:
    resources: dict[str, ResourceInfo] = {}
    asset_dir = md_path.parent / f"{md_path.stem}_assets"

    for index, resource_el in enumerate(note_el.findall("resource"), start=1):
        data_el = resource_el.find("data")
        encoded = "".join((data_el.text or "").split()) if data_el is not None else ""
        if not encoded:
            continue
        try:
            raw = base64.b64decode(encoded)
        except Exception:
            continue

        mime = resource_el.findtext("mime") or "application/octet-stream"
        digest = hashlib.md5(raw).hexdigest()
        file_name = resource_el.findtext("resource-attributes/file-name")
        if not file_name:
            file_name = f"attachment-{index}{resource_extension(mime)}"
        file_name = safe_name(file_name, f"attachment-{index}{resource_extension(mime)}")

        asset_dir.mkdir(parents=True, exist_ok=True)
        asset_path = ensure_unique(asset_dir / file_name)
        asset_path.write_bytes(raw)
        relative_link = asset_path.relative_to(md_path.parent).as_posix()
        resources[digest] = ResourceInfo(
            file_name=asset_path.name,
            mime=mime,
            relative_link=relative_link,
            absolute_path=asset_path,
        )

    return resources


def note_markdown(note_el: ET.Element, md_path: Path) -> tuple[str, str]:
    title = note_el.findtext("title") or md_path.stem
    guid = note_el.findtext("guid") or ""
    notebook = note_el.findtext("note-custom-metadata/notebook-name") or ""
    source_url = note_el.findtext("note-attributes/source-url") or ""
    created = parse_time(note_el.findtext("created"))
    updated = parse_time(note_el.findtext("updated"))

    resources = extract_resources(note_el, md_path)
    parser = EnmlMarkdownParser(resources)
    parser.feed(clean_enml(note_el.findtext("content") or ""))
    body = parser.text()

    lines = [f"# {title}", ""]
    meta_lines = []
    if notebook:
        meta_lines.append(f"- 笔记本：{notebook}")
    if created:
        meta_lines.append(f"- 创建时间：{created}")
    if updated:
        meta_lines.append(f"- 更新时间：{updated}")
    if source_url:
        meta_lines.append(f"- 原始链接：{source_url}")
    if guid:
        meta_lines.append(f"- 印象笔记 GUID：{guid}")
    if meta_lines:
        lines.extend(meta_lines)
        lines.append("")
    if body:
        lines.append(body)
        lines.append("")
    return "\n".join(lines), guid


def select_enex_notes(notes: list[ET.Element], selected_doc_ids: set[str]) -> list[ET.Element]:
    if not selected_doc_ids:
        return notes
    return [note_el for note_el in notes if (note_el.findtext("guid") or "") in selected_doc_ids]


def convert_enex(args: argparse.Namespace, enex_dir: Path) -> dict[str, Any]:
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = open_checkpoint_from_args(args, "yinxiang", "export")
    selected = set(args.doc_id or [])
    enex_files = sorted(enex_dir.rglob("*.enex"))
    if checkpoint:
        checkpoint.start_task(
            {
                "source": str(enex_dir),
                "outputDir": str(output),
                "totalDocs": len(enex_files),
                "resume": bool(getattr(args, "resume", False)),
                "retryFailed": bool(getattr(args, "retry_failed", False)),
            }
        )
        for enex_file in enex_files:
            relative = enex_file.relative_to(enex_dir).as_posix()
            item_key = f"yinxiang:enex:{hashlib.sha1(relative.encode('utf-8')).hexdigest()}"
            checkpoint.upsert_item(item_key, title=Path(relative).stem, source_url=relative, metadata={"relative": relative})
        if getattr(args, "retry_failed", False):
            enex_files = [
                enex_file
                for enex_file in enex_files
                if checkpoint.item_status(f"yinxiang:enex:{hashlib.sha1(enex_file.relative_to(enex_dir).as_posix().encode('utf-8')).hexdigest()}") == "failed"
            ]

    exported = 0
    skipped = 0
    failures: list[dict[str, str]] = []
    md_files: list[Path] = []
    total = len(enex_files)
    emit(
        f"开始转换印象笔记 ENEX：共 {total} 篇。",
        event="task.started",
        totals={"documents": total},
        output=str(output),
        source=str(enex_dir),
    )

    for index, enex_file in enumerate(enex_files, start=1):
        relative = enex_file.relative_to(enex_dir)
        item_key = f"yinxiang:enex:{hashlib.sha1(relative.as_posix().encode('utf-8')).hexdigest()}"
        md_path = output / relative.with_suffix(".md")
        md_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            if checkpoint and getattr(args, "resume", False) and checkpoint.item_status(item_key) == "completed":
                skipped += 1
                if md_path.exists():
                    md_files.append(md_path)
                continue
            if checkpoint:
                checkpoint.start_item(item_key, "convert")
            emit(
                f"开始转换印象笔记：{relative.as_posix()}",
                event="document.export.started",
                doc={"path": relative.as_posix(), "index": index, "output": str(md_path)},
            )
            root = ET.parse(enex_file).getroot()
            notes = list(root.findall("note"))
            if not notes:
                if checkpoint:
                    checkpoint.skip_item(item_key, "empty-enex")
                skipped += 1
                continue
            selected_notes = select_enex_notes(notes, selected)
            if not selected_notes:
                if checkpoint:
                    checkpoint.skip_item(item_key, "not-selected")
                skipped += 1
                continue
            note_el = selected_notes[0]
            guid = note_el.findtext("guid") or ""
            if args.incremental and md_path.exists():
                if checkpoint:
                    checkpoint.complete_item(item_key, local_path=str(md_path), metadata={"guid": guid, "skippedExisting": True})
                skipped += 1
                continue
            md_text, guid = note_markdown(note_el, md_path)
            md_path.write_text(md_text, encoding="utf-8")
            md_files.append(md_path)
            exported += 1
            if checkpoint:
                checkpoint.complete_item(item_key, local_path=str(md_path), metadata={"guid": guid, "relative": relative.as_posix()})
            emit(
                f"印象笔记转换完成：{relative.as_posix()}",
                event="document.export.completed",
                doc={"id": guid, "path": relative.as_posix(), "index": index, "output": str(md_path)},
            )
        except Exception as exc:
            if checkpoint:
                checkpoint.fail_item(item_key, str(exc))
            failures.append({"file": str(enex_file), "error": str(exc)})
            emit(
                f"印象笔记转换失败：{relative.as_posix()}：{exc}",
                event="document.export.failed",
                level="error",
                doc={"path": relative.as_posix(), "index": index, "output": str(md_path)},
                error={"type": type(exc).__name__, "message": str(exc)},
            )

        if index % max(1, args.progress_every) == 0 or index == total:
            emit(
                "progress "
                f"done={index} queued={max(0, total - index)} "
                f"exported={exported} skipped={skipped} failures={len(failures)}",
                event="task.progress",
                progress={"current": index, "total": total},
                stats={"exportedDocs": exported, "skippedDocs": skipped, "failureCount": len(failures)},
            )

    write_index(output, md_files)
    report = {
        "provider": "yinxiang",
        "database": str(args.database),
        "output": str(output),
        "sourceEnexCount": total,
        "exportedDocs": exported,
        "skippedDocs": skipped,
        "failureCount": len(failures),
        "failures": failures,
    }
    if checkpoint:
        report["checkpoint"] = checkpoint.stats()
    report = finalize_report(report, provider="yinxiang", mode="export", output=output)
    if checkpoint:
        if failures:
            checkpoint.fail_task(f"{len(failures)} 个文档失败", status="failed")
        else:
            checkpoint.complete_task(report)
        checkpoint.close()
    emit(
        "印象笔记导出完成" if not failures else f"印象笔记导出完成，但有 {len(failures)} 个失败项",
        event="task.completed",
        level="success" if not failures else "warn",
        stats={"exportedDocs": exported, "skippedDocs": skipped, "failureCount": len(failures)},
    )
    return report


def write_index(output: Path, md_files: list[Path]) -> None:
    lines = [
        "# 印象笔记导出目录",
        "",
        "本文档由万能导根据本地同步的印象笔记内容生成。",
        "",
    ]
    for md_file in sorted(md_files):
        rel = md_file.relative_to(output).as_posix()
        if rel == "00-知识库入口.md":
            continue
        title = md_file.stem
        lines.append(f"- [{title}]({rel})")
    (output / "00-知识库入口.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_export(args: argparse.Namespace) -> dict[str, Any]:
    if not args.no_sync:
        sync_notes(args)
    enex_dir = export_enex(args)
    return convert_enex(args, enex_dir)


def run_gui() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext

    root = tk.Tk()
    root.title("万能导 - 印象笔记导出")
    root.geometry("780x620")

    username_var = tk.StringVar()
    password_var = tk.StringVar()
    output_var = tk.StringVar(value=str(default_output_dir()))
    incremental_var = tk.BooleanVar(value=True)

    frame = tk.Frame(root)
    frame.pack(fill="both", expand=True, padx=16, pady=16)

    tk.Label(frame, text="印象笔记账号").pack(anchor="w")
    tk.Entry(frame, textvariable=username_var).pack(fill="x", pady=(0, 8))
    tk.Label(frame, text="印象笔记密码").pack(anchor="w")
    tk.Entry(frame, textvariable=password_var, show="*").pack(fill="x", pady=(0, 8))
    tk.Label(frame, text="输出目录").pack(anchor="w")
    output_row = tk.Frame(frame)
    output_row.pack(fill="x", pady=(0, 8))
    tk.Entry(output_row, textvariable=output_var).pack(side="left", fill="x", expand=True)
    tk.Button(
        output_row,
        text="浏览",
        command=lambda: output_var.set(filedialog.askdirectory() or output_var.get()),
    ).pack(side="left", padx=(8, 0))
    tk.Checkbutton(frame, text="增量导出", variable=incremental_var).pack(anchor="w")

    log_box = scrolledtext.ScrolledText(frame, height=20)
    log_box.pack(fill="both", expand=True, pady=12)

    def run_action(extra: list[str], stdin_text: str | None = None) -> None:
        import subprocess

        command = [sys.executable, str(Path(__file__).resolve()), *extra]
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
            log_box.insert("end", line)
            log_box.see("end")
            root.update_idletasks()
        if proc.wait() != 0:
            messagebox.showerror("执行失败", "请查看日志。")

    def login_sync() -> None:
        if not username_var.get() or not password_var.get():
            messagebox.showwarning("缺少信息", "请填写账号和密码。")
            return
        run_action(
            ["--init-auth", "--username", username_var.get(), "--password-stdin"],
            password_var.get() + "\n",
        )

    def export_all() -> None:
        args = ["--output", output_var.get()]
        if incremental_var.get():
            args.append("--incremental")
        run_action(args)

    actions = tk.Frame(frame)
    actions.pack(fill="x")
    tk.Button(actions, text="登录并同步", command=login_sync).pack(side="left", padx=(0, 8))
    tk.Button(actions, text="导出全部", command=export_all).pack(side="left", padx=(0, 8))
    tk.Button(actions, text="退出", command=root.destroy).pack(side="right")

    root.mainloop()
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出印象笔记为 Markdown")
    parser.add_argument("--gui", action="store_true", help="打开简易图形界面")
    parser.add_argument("--init-auth", action="store_true", help="初始化/刷新印象笔记凭证并同步")
    parser.add_argument("--username", help="印象笔记账号，仅初始化凭证时需要")
    parser.add_argument("--password-stdin", action="store_true", help="从标准输入读取密码")
    parser.add_argument("--password-env", help="从指定环境变量读取密码")
    parser.add_argument("--backend", default="china", choices=["china", "evernote"], help="服务后端")
    parser.add_argument("--database", type=Path, default=default_database(), help="本地同步库路径")
    parser.add_argument("--output", type=Path, default=default_output_dir(), help="Markdown 输出目录")
    parser.add_argument("--enex-dir", type=Path, help="ENEX 中间文件目录")
    parser.add_argument("--convert-enex-only", action="store_true", help="只转换已有 ENEX，不连接印象笔记")
    parser.add_argument("--scan-toc", action="store_true", help="读取本地同步库目录")
    parser.add_argument("--doc-id", action="append", default=[], help="只导出指定笔记 GUID，可重复")
    parser.add_argument("--doc-id-file", default="", help="从文件读取要导出的笔记 GUID，JSON 数组或逐行文本均可")
    parser.add_argument("--incremental", action="store_true", help="已有 Markdown 文件时跳过")
    add_checkpoint_args(parser)
    parser.add_argument("--force", action="store_true", help="重新初始化本地同步库")
    parser.add_argument("--no-sync", action="store_true", help="导出前不重新同步")
    parser.add_argument("--include-trash", action="store_true", help="包含废纸篓笔记")
    parser.add_argument("--retry", type=int, default=3, help="网络重试次数")
    parser.add_argument("--max-workers", type=int, default=3, help="同步下载并发数")
    parser.add_argument("--max-chunk-results", type=int, default=250, help="每批同步数量")
    parser.add_argument("--cache-memory-limit", type=int, default=512, help="下载缓存内存限制 MB")
    parser.add_argument("--progress-every", type=int, default=1, help="每处理多少篇输出一次进度")
    args = parser.parse_args(argv)
    extend_arg_list_from_file(args, "doc_id")
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        args.database = args.database.expanduser().resolve()
        args.output = args.output.expanduser().resolve()
        if args.enex_dir:
            args.enex_dir = args.enex_dir.expanduser().resolve()

        if args.gui:
            print("旧版 Python GUI 已废弃，请使用 Electron 桌面端：start-wandao.cmd 或 ./start-wandao.sh", file=sys.stderr)
            return 2
        if args.init_auth:
            init_auth(args)
            emit_json({"provider": "yinxiang", "database": str(args.database), "status": "authenticated"})
            return 0
        if args.scan_toc:
            emit_json(scan_toc(args))
            return 0
        if args.convert_enex_only:
            if not args.enex_dir:
                raise ExportError("--convert-enex-only 需要同时指定 --enex-dir")
            result = convert_enex(args, args.enex_dir)
            emit_json(result)
            return 0
        result = run_export(args)
        emit_json(result)
        return 0
    except KeyboardInterrupt:
        emit("已停止。", event="task.stopped", level="warn")
        return 130
    except ExportError as exc:
        emit(
            str(exc),
            event="task.failed",
            level="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        return 1
    except Exception as exc:
        emit(
            f"印象笔记导出失败：{exc}",
            event="task.failed",
            level="error",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
