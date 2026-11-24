#!/usr/bin/env python3
"""Export selected SiYuan notebook documents to Markdown and local assets."""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_REPO_ROOT = _Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))

import argparse
import json
import mimetypes
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from wandao_core.browser import ExportError, ExportStopped, check_stopped, emit, sanitize_filename
from wandao_core.report import finalize_report

PLUGIN_ID = "siyuan"
PROVIDER_ID = "siyuan-export"
DEFAULT_SERVER_URL = "http://127.0.0.1:6806"
MAX_RESOURCE_BYTES = 64 * 1024 * 1024


class SiyuanError(ExportError):
    """A user-facing SiYuan plugin error."""


@dataclass(frozen=True)
class Document:
    notebook_id: str
    document_id: str
    title: str
    path: tuple[str, ...]
    parent_node_id: str

    @property
    def node_id(self) -> str:
        return f"{self.notebook_id}::{self.document_id}"


class SiyuanAPI:
    def __init__(self, server_url: str, token: str, timeout: float = 30.0) -> None:
        parsed = urllib.parse.urlsplit(server_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise SiyuanError("请输入有效的思源服务地址，例如 http://127.0.0.1:6806。")
        self.base_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
        self.token = token.strip()
        self.timeout = timeout

    def post_json(self, endpoint: str, payload: dict[str, Any]) -> Any:
        url = f"{self.base_url}{endpoint}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Token {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "Wandao-SiYuan-Plugin/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read(1024).decode("utf-8", errors="replace")
            if exc.code in {401, 403}:
                raise SiyuanError("思源 API Token 无效或当前账号无权访问该内容。") from exc
            raise SiyuanError(f"思源 API 请求失败（HTTP {exc.code}）：{detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SiyuanError(f"无法连接思源服务 {self.base_url}：{exc}") from exc
        try:
            result = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SiyuanError(f"思源 API 返回了无法识别的响应：{endpoint}") from exc
        if not isinstance(result, dict) or result.get("code") != 0:
            message = result.get("msg", "未知错误") if isinstance(result, dict) else "无效响应"
            raise SiyuanError(f"思源 API {endpoint} 返回错误：{message}")
        return result.get("data")

    def get_binary(self, endpoint: str, payload: dict[str, Any]) -> bytes:
        url = f"{self.base_url}{endpoint}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Token {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "Wandao-SiYuan-Plugin/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                content = response.read(MAX_RESOURCE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            detail = exc.read(1024).decode("utf-8", errors="replace")
            raise SiyuanError(f"读取思源资源失败（HTTP {exc.code}）：{detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SiyuanError(f"无法读取思源资源：{exc}") from exc
        if len(content) > MAX_RESOURCE_BYTES:
            raise SiyuanError("单个思源资源超过 64 MB，已跳过。")
        if not content:
            raise SiyuanError("思源返回了空资源。")
        return content

    def notebooks(self) -> list[dict[str, Any]]:
        data = self.post_json("/api/notebook/lsNotebooks", {})
        items = data.get("notebooks", []) if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise SiyuanError("思源未返回可识别的笔记本列表。")
        return [item for item in items if isinstance(item, dict) and item.get("id")]

    def notebook(self, notebook_id: str) -> dict[str, Any] | None:
        return next((item for item in self.notebooks() if str(item["id"]) == notebook_id), None)

    def document_tree(self, notebook_id: str) -> list[dict[str, Any]]:
        data = self.post_json("/api/filetree/listDocTree", {"notebook": notebook_id, "path": "/"})
        tree = data.get("tree", []) if isinstance(data, dict) else []
        if not isinstance(tree, list):
            raise SiyuanError("思源返回了无法识别的文档目录。")
        return tree

    def document_info(self, document_id: str) -> dict[str, Any]:
        data = self.post_json("/api/block/getDocInfo", {"id": document_id})
        return data if isinstance(data, dict) else {}

    def markdown(self, document_id: str) -> str:
        data = self.post_json("/api/export/exportMdContent", {"id": document_id, "yfm": False})
        if not isinstance(data, dict) or not isinstance(data.get("content"), str):
            raise SiyuanError("思源未返回文档 Markdown 内容。")
        return data["content"]

    def assets(self, document_id: str) -> list[str]:
        data = self.post_json("/api/asset/getDocAssets", {"id": document_id})
        return [str(item) for item in data if isinstance(item, str)] if isinstance(data, list) else []

    def resource(self, asset_path: str) -> bytes:
        relative = asset_path.split("?", 1)[0].split("#", 1)[0].replace("\\", "/").lstrip("/")
        if relative.startswith("data/"):
            api_path = "/" + relative
        elif relative.startswith("assets/"):
            api_path = "/data/" + relative
        else:
            raise SiyuanError(f"思源返回了不支持的资源路径：{asset_path}")
        return self.get_binary("/api/file/getFile", {"path": api_path})


def safe_server_url(value: str) -> str:
    return value.strip() or DEFAULT_SERVER_URL


def node_title(api: SiyuanAPI, document_id: str) -> str:
    info = api.document_info(document_id)
    title = info.get("name") or info.get("title") or info.get("hPath") or info.get("path") or document_id
    return str(title).replace("\\", "/").rstrip("/").split("/")[-1].removesuffix(".sy") or document_id


def collect_documents(api: SiyuanAPI, notebook_id: str, tree: list[dict[str, Any]]) -> list[Document]:
    documents: list[Document] = []

    def visit(nodes: list[dict[str, Any]], parent_id: str, path: tuple[str, ...]) -> None:
        for node in nodes:
            if not isinstance(node, dict) or not node.get("id"):
                continue
            document_id = str(node["id"])
            title = str(node.get("name") or node_title(api, document_id))
            document = Document(notebook_id, document_id, title, path + (title,), parent_id)
            documents.append(document)
            children = node.get("children")
            if isinstance(children, list):
                visit(children, document.node_id, document.path)

    visit(tree, f"notebook:{notebook_id}", ())
    return documents


def build_nodes(api: SiyuanAPI, selected_notebook: str = "") -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    notebooks = api.notebooks()
    for notebook in notebooks:
        notebook_id = str(notebook["id"])
        if selected_notebook and notebook_id != selected_notebook:
            continue
        notebook_title = str(notebook.get("name") or notebook_id)
        notebook_node_id = f"notebook:{notebook_id}"
        nodes.append({
            "nodeId": notebook_node_id,
            "exportId": f"{notebook_id}::*",
            "title": notebook_title,
            "parentNodeId": "root",
            "selectable": True,
        })
        documents = collect_documents(api, notebook_id, api.document_tree(notebook_id))
        for document in documents:
            nodes.append({
                "nodeId": document.node_id,
                "exportId": document.node_id,
                "title": document.title,
                "parentNodeId": document.parent_node_id,
                "selectable": True,
            })
    if selected_notebook and not nodes:
        raise SiyuanError(f"没有找到笔记本 ID：{selected_notebook}")
    return nodes


def selected_documents(api: SiyuanAPI, selected_ids: Iterable[str]) -> list[Document]:
    selections = {str(value) for value in selected_ids if value}
    if not selections:
        raise SiyuanError("请先在目录树中选择要导出的笔记本或目录。")
    notebooks = api.notebooks()
    trees: dict[str, list[Document]] = {}
    known_ids = {f"notebook:{item['id']}" for item in notebooks}
    known_ids.update(f"{item['id']}::*" for item in notebooks)
    for notebook in notebooks:
        notebook_id = str(notebook["id"])
        documents = collect_documents(api, notebook_id, api.document_tree(notebook_id))
        trees[notebook_id] = documents
        known_ids.update(document.node_id for document in documents)
    unknown = selections - known_ids
    if unknown:
        raise SiyuanError("所选目录已过期，请重新读取思源目录后再试。")

    output: list[Document] = []
    for notebook in notebooks:
        notebook_id = str(notebook["id"])
        notebook_selected = bool(selections.intersection({f"notebook:{notebook_id}", f"{notebook_id}::*"}))
        selected_here = {value for value in selections if value in {item.node_id for item in trees[notebook_id]}}
        selected_ancestors: set[str] = set()
        for document in trees[notebook_id]:
            include = notebook_selected or document.node_id in selected_here or document.parent_node_id in selected_ancestors
            if document.node_id in selected_here or document.parent_node_id in selected_ancestors:
                selected_ancestors.add(document.node_id)
            if include:
                output.append(document)
    if not output:
        raise SiyuanError("未能解析所选文档，请重新读取思源目录后再试。")
    return output


def normalized_asset_path(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip().replace("\\", "/"))
    path = urllib.parse.unquote(parsed.path)
    path = path.lstrip("/")
    return path


def asset_local_name(value: str) -> str:
    name = Path(normalized_asset_path(value)).name
    return sanitize_filename(name or "resource")


def rewrite_resource_links(markdown: str, replacements: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        target = match.group("target")
        normalized = normalized_asset_path(target)
        replacement = replacements.get(normalized) or replacements.get(Path(normalized).name)
        return f"{match.group('prefix')}{replacement or target}{match.group('suffix')}"

    return re.sub(
        r"(?P<prefix>!?\[[^\]]*\]\()(?P<target>[^\s)]+)(?P<suffix>(?:\s+(?:\"[^\"]*\"|'[^']*'))?\))",
        replace,
        markdown,
    )


def output_path(root: Path, document: Document) -> Path:
    path = root.joinpath(*(sanitize_filename(part) for part in document.path[:-1]))
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{sanitize_filename(document.title)}.md"


def scan(args: argparse.Namespace) -> dict[str, Any]:
    api = SiyuanAPI(safe_server_url(args.server_url), args.api_token)
    nodes = build_nodes(api, args.notebook_id)
    result = finalize_report({"platform": PLUGIN_ID, "totalDocs": len(nodes), "nodes": nodes, "failures": [], "resourceFailures": []}, provider=PROVIDER_ID, mode="scan")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def export(args: argparse.Namespace) -> dict[str, Any]:
    api = SiyuanAPI(safe_server_url(args.server_url), args.api_token)
    output_root = Path(args.output).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    documents = selected_documents(api, args.node_id)
    failures: list[dict[str, Any]] = []
    resource_failures: list[dict[str, Any]] = []
    exported = 0
    used_paths: set[Path] = set()
    started = time.time()
    for index, document in enumerate(documents, start=1):
        check_stopped(args)
        emit(args, f"正在导出 {index}/{len(documents)}：{document.title}", event="document.export.started", documentId=document.document_id)
        try:
            markdown = api.markdown(document.document_id)
            target = output_path(output_root, document)
            if target in used_paths:
                target = target.with_name(f"{target.stem}-{document.document_id}{target.suffix}")
            used_paths.add(target)
            assets_dir = target.parent / "assets"
            replacements: dict[str, str] = {}
            for asset_path in api.assets(document.document_id):
                check_stopped(args)
                local_name = asset_local_name(asset_path)
                try:
                    data = api.resource(asset_path)
                    assets_dir.mkdir(parents=True, exist_ok=True)
                    (assets_dir / local_name).write_bytes(data)
                    relative = (Path("assets") / local_name).as_posix()
                    replacements[normalized_asset_path(asset_path)] = relative
                    replacements[Path(normalized_asset_path(asset_path)).name] = relative
                except (SiyuanError, OSError) as exc:
                    resource_failures.append({"documentId": document.document_id, "path": asset_path, "message": str(exc)})
            markdown = rewrite_resource_links(markdown, replacements)
            target.write_text(markdown.rstrip() + "\n", encoding="utf-8")
            exported += 1
            emit(args, f"已导出 {document.title}", event="document.export.completed", documentId=document.document_id, output=str(target))
        except ExportStopped:
            raise
        except Exception as exc:
            failures.append({"documentId": document.document_id, "title": document.title, "message": str(exc)})
            emit(args, f"导出失败 {document.title}：{exc}", event="document.export.failed", level="error", documentId=document.document_id)

    summary = {
        "platform": PLUGIN_ID,
        "totalDocs": len(documents),
        "successCount": exported,
        "exportedDocs": exported,
        "failures": failures,
        "resourceFailures": resource_failures,
        "output": str(output_root),
        "elapsedSeconds": round(time.time() - started, 2),
    }
    report_path = output_root / "00-导出报告.json"
    report = finalize_report(summary, provider=PROVIDER_ID, mode="export", report_file=report_path, output=output_root)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    emit(args, "思源笔记导出完成。", event="task.completed", level="success" if not failures else "warn", reportFile=str(report_path), stats=report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    result.add_argument("--api-token", required=True)
    result.add_argument("--notebook-id", default="")
    result.add_argument("--output", default="exports/siyuan")
    result.add_argument("--node-id", action="append", default=[])
    result.add_argument("--scan-toc", action="store_true")
    result.add_argument("--export", action="store_true")
    result.add_argument("--progress-every", type=int, default=1)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.scan_toc:
            scan(args)
        elif args.export:
            export(args)
        else:
            raise SiyuanError("请指定 --scan-toc 或 --export。")
    except Exception as exc:
        report = finalize_report({
            "platform": PLUGIN_ID,
            "totalDocs": 0,
            "successCount": 0,
            "failures": [{"message": str(exc)}],
            "resourceFailures": [],
            "error": str(exc),
        }, provider=PROVIDER_ID, mode="scan" if args.scan_toc else "export")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
