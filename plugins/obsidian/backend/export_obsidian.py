#!/usr/bin/env python3
"""Export an Obsidian Vault to a flat Markdown archive.

Reads a local Obsidian Vault, copies Markdown files and referenced images
or attachments into an output directory while preserving the original folder
hierarchy.  Wiki-style embeds (![[...]]) and standard Markdown relative-path
references are resolved against the vault and rewritten to point to the
actual copied resource locations under _resources/.

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

_RESOURCES_SUBDIR = "_resources"

# Matches: ![[path|optional-size]]  or  [[path|optional-alias]]
_WIKI_LINK_RE = re.compile(r"!\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")

# Matches: ![alt](url)
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

# Matches: [text](url)  (regular link, not image)
_MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)]+)\)")


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------

def _validate_vault_path(target: Path, vault: Path) -> Path:
    """Resolve target and verify it lies inside vault.

    Rejects:
      - Raw path containing .. components.
      - Resolved paths outside the vault directory.
      - Symlink / junction escapes from the vault.

    Returns the resolved absolute path.
    """
    raw = str(target)
    if ".." in Path(raw).parts:
        raise ValueError(f"Path contains forbidden .. traversal: {raw}")

    resolved = target.resolve()

    try:
        vault_resolved = vault.resolve()
        resolved.relative_to(vault_resolved)
    except ValueError:
        raise ValueError(f"Path outside vault: {resolved}")

    # Walk from vault to target verifying no component is an escaping symlink.
    rel = resolved.relative_to(vault_resolved)
    cursor = vault_resolved
    for part in rel.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            real = cursor.resolve()
            try:
                real.relative_to(vault_resolved)
            except ValueError:
                raise ValueError(f"Symlink escapes vault: {cursor} -> {real}")

    return resolved


def _validate_output_not_in_vault(output: Path, vault: Path) -> None:
    """Raise if output directory is inside (or equal to) vault."""
    try:
        output.resolve().relative_to(vault.resolve())
    except ValueError:
        return  # output is outside vault
    raise ValueError("Output directory must not be inside the vault")


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

    Every candidate is validated to be within the vault before returning.
    """
    # 1. relative to source .md
    try:
        candidate = _validate_vault_path(source_dir / ref, vault)
        if candidate.is_file():
            return candidate
    except ValueError:
        pass

    # 2. relative to vault root
    try:
        candidate = _validate_vault_path(vault / ref, vault)
        if candidate.is_file():
            return candidate
    except ValueError:
        pass

    # 3. filename-only lookup (only candidates already inside vault)
    key = Path(ref).name.lower()
    hits = [h for h in file_index.get(key, [])]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        same_dir_hits = [h for h in hits if source_dir in h.parents or h.parent == source_dir]
        if len(same_dir_hits) == 1:
            return same_dir_hits[0]
    return None


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
# Markdown reference rewriting
# ---------------------------------------------------------------------------

def _rewrite_markdown_refs(
    md_text: str,
    ref_to_new_path: Dict[str, str],
) -> str:
    """Replace every resource reference in *md_text* with a path that points
    to the actual copied resource location.

    Wiki embeds (![[...]]) are converted to standard Markdown image syntax.
    """
    replacements: List[Tuple[int, int, str]] = []

    # Wiki embeds: ![[ref|...]] -> ![name](new_path)
    for m in _WIKI_LINK_RE.finditer(md_text):
        full = m.group(1).strip()
        base_ref = full.split("|")[0].strip()
        if base_ref in ref_to_new_path:
            new_path = ref_to_new_path[base_ref]
            alt = Path(base_ref).name
            replacement = f"![{alt}]({new_path})"
            replacements.append((m.start(), m.end(), replacement))

    # Standard md images: ![alt](ref) -> ![alt](new_path)
    for m in _MD_IMAGE_RE.finditer(md_text):
        ref = m.group(1)
        if ref in ref_to_new_path:
            new_path = ref_to_new_path[ref]
            prefix_end = m.group(0).index("](") + 1
            alt_start = 2
            alt = m.group(0)[alt_start:prefix_end - 1]
            replacement = f"![{alt}]({new_path})"
            replacements.append((m.start(), m.end(), replacement))

    # Standard md links: [text](ref) -> [text](new_path)
    for m in _MD_LINK_RE.finditer(md_text):
        ref = m.group(2)
        if ref in ref_to_new_path:
            new_path = ref_to_new_path[ref]
            text = m.group(1)
            replacement = f"[{text}]({new_path})"
            replacements.append((m.start(), m.end(), replacement))

    # Apply replacements from end to start to preserve positions
    replacements.sort(key=lambda x: x[0], reverse=True)
    result = md_text
    for start, end, repl in replacements:
        result = result[:start] + repl + result[end:]

    return result


def _resource_dest_path(
    resolved_src: Path,
    vault: Path,
    output_root: Path,
) -> Path:
    """Compute destination path for a resource under output/_resources/.

    Uses the vault-relative path to guarantee uniqueness across directories.
    """
    vault_abs = vault.resolve()
    rel = resolved_src.resolve().relative_to(vault_abs)
    return output_root / _RESOURCES_SUBDIR / rel


# ---------------------------------------------------------------------------
# Scan TOC
# ---------------------------------------------------------------------------

def scan_toc_cmd(vault: Path) -> Dict[str, Any]:
    """Walk *vault* and return a generic TOC tree."""
    nodes: List[Dict[str, Any]] = []
    total_docs = 0
    folder_count = 0
    folder_ids: set = set()

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

    # Security: reject output directory inside vault
    _validate_output_not_in_vault(output, vault)

    # Build and validate selection set
    if doc_ids:
        selected = set()
        for raw_id in doc_ids:
            try:
                validated = _validate_vault_path(vault_abs / raw_id, vault_abs)
                if not validated.is_file():
                    raise ValueError(f"File not found: {raw_id}")
                selected.add(validated.relative_to(vault_abs).as_posix())
            except ValueError as exc:
                return {
                    "provider": "obsidian-export",
                    "mode": "export",
                    "totalDocs": len(doc_ids),
                    "exportedDocs": 0,
                    "successCount": 0,
                    "skippedDocs": 0,
                    "failureCount": 1,
                    "failures": [
                        {
                            "docId": raw_id,
                            "title": Path(raw_id).stem,
                            "error": str(exc),
                        }
                    ],
                    "resourceFailures": [],
                }
    else:
        selected = set()
        for entry in vault_abs.rglob("*.md"):
            if not _is_hidden(entry, vault_abs):
                selected.add(entry.relative_to(vault_abs).as_posix())

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
                    "error": "Source file not found",
                }
            )
            continue

        dest_file = output_abs / rel_path
        output_dir = dest_file.parent

        if incremental and dest_file.exists() and dest_file.stat().st_mtime >= source_file.stat().st_mtime:
            skipped_docs += 1
            processed_count += 1
            _emit_progress(processed_count, total_docs, progress_every)
            continue

        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            md_text = source_file.read_text(encoding="utf-8", errors="replace")
            resolved: Dict[str, Path] = {}
            ref_to_new_path: Dict[str, str] = {}
            resource_errors: List[Dict[str, Any]] = []

            for ref, _ in _find_resource_refs(md_text):
                if ref in resolved:
                    continue
                result = _resolve_resource(ref, source_file.parent, vault_abs, file_index)
                if result is not None:
                    resolved[ref] = result
                else:
                    resource_errors.append({"ref": ref, "error": "Resource not found"})

            # Copy resources and build ref map
            for ref, src_path in resolved.items():
                dest_resource = _resource_dest_path(src_path, vault_abs, output_abs)
                dest_resource.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dest_resource)

                try:
                    new_rel = Path(os.path.relpath(dest_resource, output_dir))
                except ValueError:
                    new_rel = Path(dest_resource.name)
                ref_to_new_path[ref] = new_rel.as_posix()

            # Rewrite markdown references
            if ref_to_new_path:
                md_text = _rewrite_markdown_refs(md_text, ref_to_new_path)

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
    parser = argparse.ArgumentParser(description="Obsidian Vault export")
    parser.add_argument("--vault", default=None, help="Obsidian Vault root directory")
    parser.add_argument("--output", help="Output directory (required for --export)")
    parser.add_argument("--scan-toc", action="store_true", help="Scan vault and output JSON TOC")
    parser.add_argument("--export", action="store_true", help="Start batch export")
    parser.add_argument("--doc-id", action="append", default=None, help="Relative path of file to export (repeatable)")
    parser.add_argument("--incremental", action="store_true", help="Skip files newer than output")
    parser.add_argument("--progress-every", type=int, default=1, help="Emit progress every N docs (0=off)")

    args = parser.parse_args()

    if not args.vault:
        print(json.dumps({"error": "--vault not specified"}, ensure_ascii=False), flush=True)
        sys.exit(1)

    vault = Path(args.vault).resolve()
    if not vault.is_dir():
        if vault.is_file():
            msg = f"--vault must be a directory, not a file: {vault}"
        else:
            msg = f"Vault directory not found: {vault}"
        print(json.dumps({"error": msg}, ensure_ascii=False), flush=True)
        sys.exit(1)

    if args.scan_toc:
        result = scan_toc_cmd(vault)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return

    if args.export:
        if not args.output:
            print(json.dumps({"error": "--export requires --output"}, ensure_ascii=False), flush=True)
            sys.exit(1)
        output = Path(args.output).resolve()
        try:
            result = export_cmd(
                vault=vault,
                output=output,
                doc_ids=args.doc_id,
                incremental=args.incremental,
                progress_every=args.progress_every,
            )
        except ValueError as exc:
            result = {
                "provider": "obsidian-export",
                "mode": "export",
                "totalDocs": 0,
                "exportedDocs": 0,
                "successCount": 0,
                "skippedDocs": 0,
                "failureCount": 1,
                "failures": [{"docId": "", "title": "", "error": str(exc)}],
                "resourceFailures": [],
            }
            print(json.dumps(result, ensure_ascii=False), flush=True)
            sys.exit(1)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if result.get("failureCount", 0) > 0 or result.get("failures"):
            sys.exit(1)
        return

    print(json.dumps({"error": "Specify --scan-toc or --export"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
