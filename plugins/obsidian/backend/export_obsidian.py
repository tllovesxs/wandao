#!/usr/bin/env python3
"""Export an Obsidian Vault to a flat Markdown archive.

Reads a local Obsidian Vault, copies Markdown files and referenced images
or attachments into an output directory while preserving the original folder
hierarchy.  Wiki-style embeds (![[...]]) and standard Markdown relative-path
references are resolved against the vault; unresolved resources are logged
in the final JSON report.

The script never modifies the source Vault.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COMMON_ATTACHMENT_DIRS = ("attachments", "assets", "resources", "images")
IMAGE_EXTENSIONS = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp",
        ".tiff", ".tif", ".ico",
    }
)

# Matches: ![[path|optional-size]]  or  [[path|optional-alias]]
_WIKI_LINK_RE = re.compile(r"!\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")

# Matches: ![alt](url)
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

# Matches: [text](url)  (regular link)
_MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)]+)\)")


# ---------------------------------------------------------------------------
# File index
# ---------------------------------------------------------------------------

def _build_file_index(vault: Path) -> Dict[str, List[Path]]:
    """Map every file name (lowercased) to a list of absolute vault paths."""
    cache: Dict[str, List[Path]] = {}
    for entry in vault.rglob("*"):
        if entry.is_file() and not _is_hidden(entry, vault):
            key = entry.name.lower()
            cache.setdefault(key, []).append(entry)
    return cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_hidden(entry: Path, vault: Path) -> bool:
    """True if any path component starts with a dot (e.g. .obsidian)."""
    for part in entry.relative_to(vault).parts:
        if part.startswith("."):
            return True
    return False


def _is_image(filename: str) -> bool:
    return Path(filename).suffix.lower() in IMAGE_EXTENSIONS


def _resolve_resource(
    ref: str,
    source_dir: Path,
    vault: Path,
    file_index: Dict[str, List[Path]],
) -> Optional[Path]:
    """Resolve a wiki-link or markdown-relative reference to a vault file.

    Resolution order:
      1. Relative to the source .md directory.
      2. Relative to the vault root.
      3. Filename-only lookup against pre-built index (best-effort).
    """
    # 1. relative to source .md
    candidate = (source_dir / ref).resolve()
    if candidate.is_file() and _is_under_vault(candidate, vault):
        return candidate

    # 2. relative to vault root
    candidate = (vault / ref).resolve()
    if candidate.is_file():
        return candidate

    # 3. filename-only lookup
    key = Path(ref).name.lower()
    hits = file_index.get(key, [])
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        # Prefer one in the same directory tree as source .md
        same_dir_hits = [h for h in hits if source_dir in h.parents or h.parent == source_dir]
        if len(same_dir_hits) == 1:
            return same_dir_hits[0]
    return None


def _is_under_vault(target: Path, vault: Path) -> bool:
    try:
        target.resolve().relative_to(vault.resolve())
        return True
    except ValueError:
        return False


def _find_resource_refs(md_text: str) -> List[Tuple[str, Optional[str]]]:
    """Return [(raw_reference, alt_text_or_None)] for every embedded resource.

    Covers:
      - ![[file]]          (Obsidian wiki embed)
      - ![alt](file)       (standard Markdown image)
      - [text](file)       (regular Markdown link to local file)
    """
    refs: List[Tuple[str, Optional[str]]] = []

    for match in _WIKI_LINK_RE.finditer(md_text):
        refs.append((match.group(1).strip(), None))

    for match in _MD_IMAGE_RE.finditer(md_text):
        url = match.group(1)
        if url and not url.startswith(("http://", "https://", "data:")):
            refs.append((url, None))

    for match in _MD_LINK_RE.finditer(md_text):
        url = match.group(2)
        if url and not url.startswith(("http://", "https://", "data:", "#")):
            refs.append((url, None))

    return refs


# ---------------------------------------------------------------------------
# Scan TOC
# ---------------------------------------------------------------------------

def scan_toc_cmd(vault: Path) -> Dict[str, Any]:
    """Walk *vault* and return a generic TOC tree."""
    nodes: List[Dict[str, Any]] = []
    total_docs = 0
    folder_count = 0
    folder_ids: set = set()

    # root pseudo-node
    nodes.append(
        {
            "nodeId": "folder:",
            "exportId": "",
            "title": "(root)",
            "parentNodeId": "",
            "selectable": False,
        }
    )

    vault_abs = vault.resolve()
    for dirpath, _dirnames, filenames in sorted(os.walk(vault_abs)):
        dirpath_p = Path(dirpath)
        if _is_hidden(dirpath_p, vault_abs):
            continue
        rel_dir = dirpath_p.relative_to(vault_abs)

        # Register folders
        parts = rel_dir.parts
        for depth in range(1, len(parts) + 1):
            sub_rel = Path(*parts[:depth])
            folder_id = "folder:" + sub_rel.as_posix()
            if folder_id not in folder_ids:
                parent = "folder:" + (sub_rel.parent.as_posix() if sub_rel.parent != Path() else "")
                nodes.append(
                    {
                        "nodeId": folder_id,
                        "exportId": "",
                        "title": parts[depth - 1],
                        "parentNodeId": parent if parent != "folder:." else "folder:",
                        "selectable": False,
                    }
                )
                folder_ids.add(folder_id)
                folder_count += 1

        # Register Markdown files
        md_files = sorted(f for f in filenames if f.lower().endswith(".md"))
        for fname in md_files:
            rel_file = (rel_dir / fname).as_posix()
            parent_id = "folder:" + (rel_dir.as_posix() if rel_dir != Path() else "")
            nodes.append(
                {
                    "nodeId": f"doc:{rel_file}",
                    "exportId": rel_file,
                    "title": Path(fname).stem,
                    "parentNodeId": parent_id if parent_id != "folder:." else "folder:",
                    "selectable": True,
                }
            )
            total_docs += 1

    return {
        "provider": "obsidian-export",
        "nodes": nodes,
        "totalDocs": total_docs,
        "folderCount": folder_count,
    }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_cmd(
    vault: Path,
    output: Path,
    doc_ids: Optional[List[str]],
    incremental: bool,
    progress_every: int,
) -> Dict[str, Any]:
    """Batch-export selected Markdown files from *vault* to *output*."""

    vault_abs = vault.resolve()
    output_abs = output.resolve()

    # Build selection set
    if doc_ids:
        selected = set(doc_ids)
    else:
        # Export all .md files in vault
        selected = set()
        for entry in vault_abs.rglob("*.md"):
            if not _is_hidden(entry, vault_abs):
                selected.add(entry.relative_to(vault_abs).as_posix())

    # Build file index for resource resolution
    file_index = _build_file_index(vault_abs)

    total_docs = len(selected)
    exported_docs = 0
    skipped_docs = 0
    failure_count = 0
    failures: List[Dict[str, Any]] = []
    resource_failures: List[Dict[str, Any]] = []
    processed_count = 0

    for rel_path in sorted(selected):
        source_file = vault_abs / rel_path
        if not source_file.is_file():
            failure_count += 1
            failures.append(
                {
                    "docId": rel_path,
                    "title": Path(rel_path).stem,
                    "error": "源文件不存在",
                }
            )
            continue

        dest_file = output_abs / rel_path
        output_dir = dest_file.parent

        # Incremental: skip if output exists and is newer
        if incremental and dest_file.exists() and dest_file.stat().st_mtime >= source_file.stat().st_mtime:
            skipped_docs += 1
            processed_count += 1
            _emit_progress(processed_count, total_docs, progress_every)
            continue

        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            md_text = source_file.read_text(encoding="utf-8", errors="replace")
            resolved: Dict[str, Path] = {}  # ref -> resolved file path
            resource_errors: List[Dict[str, Any]] = []

            # Resolve embedded resources
            for ref, _ in _find_resource_refs(md_text):
                if ref in resolved:
                    continue
                result = _resolve_resource(ref, source_file.parent, vault_abs, file_index)
                if result is not None:
                    resolved[ref] = result
                else:
                    resource_errors.append({"ref": ref, "error": "未找到引用文件"})

            # Copy resources
            for ref, src_path in resolved.items():
                dest_resource = output_dir / Path(ref).name
                dest_resource.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dest_resource)

            # Write the Markdown file
            dest_file.write_text(md_text, encoding="utf-8")

            exported_docs += 1

            if resource_errors:
                for err in resource_errors:
                    resource_failures.append(
                        {
                            "docId": rel_path,
                            "title": Path(rel_path).stem,
                            "ref": err["ref"],
                            "error": err["error"],
                        }
                    )

        except Exception as exc:
            failure_count += 1
            failures.append(
                {
                    "docId": rel_path,
                    "title": Path(rel_path).stem,
                    "error": str(exc),
                }
            )

        processed_count += 1
        _emit_progress(processed_count, total_docs, progress_every)

    return {
        "provider": "obsidian-export",
        "mode": "export",
        "totalDocs": total_docs,
        "exportedDocs": exported_docs,
        "successCount": exported_docs,
        "skippedDocs": skipped_docs,
        "failureCount": failure_count,
        "failures": failures,
        "resourceFailures": resource_failures,
    }


# ---------------------------------------------------------------------------
# Progress output
# ---------------------------------------------------------------------------

def _emit_progress(current: int, total: int, every: int) -> None:
    if every > 0 and (current % every == 0 or current == total):
        sys.stdout.write(f"progress {current}/{total} exported={current} failures=0\n")
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Obsidian Vault 归档导出")
    parser.add_argument("--vault", default=None, help="Obsidian Vault 根目录")
    parser.add_argument("--output", help="输出目录 (--export 时必需)")
    parser.add_argument("--scan-toc", action="store_true", help="扫描 Vault 目录并输出 JSON 目录树")
    parser.add_argument("--export", action="store_true", help="开始批量导出")
    parser.add_argument("--doc-id", action="append", default=None, help="要导出的文件相对路径（可多次传入）")
    parser.add_argument("--incremental", action="store_true", help="跳过比输出目录更新的文件")
    parser.add_argument("--progress-every", type=int, default=1, help="每隔 N 篇文档输出一次进度（0=不输出）")

    args = parser.parse_args()

    if not args.vault:
        print(json.dumps({"error": "--vault 未指定，请在界面中选择 Vault 根目录（文件夹）."}, ensure_ascii=False), flush=True)
        sys.exit(1)

    vault = Path(args.vault).resolve()
    if not vault.is_dir():
        if vault.is_file():
            msg = f"--vault 需要选择 Vault 根目录（文件夹），而不是单个文件。当前传入的是文件：{vault}"
        else:
            msg = f"Vault 目录不存在：{vault}"
        print(json.dumps({"error": msg}, ensure_ascii=False), flush=True)
        sys.exit(1)

    if args.scan_toc:
        result = scan_toc_cmd(vault)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return

    if args.export:
        if not args.output:
            print(json.dumps({"error": "--export 需要 --output 参数"}, ensure_ascii=False), flush=True)
            sys.exit(1)
        output = Path(args.output).resolve()
        result = export_cmd(
            vault=vault,
            output=output,
            doc_ids=args.doc_id,
            incremental=args.incremental,
            progress_every=args.progress_every,
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return

    print(json.dumps({"error": "请指定 --scan-toc 或 --export 操作."}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()