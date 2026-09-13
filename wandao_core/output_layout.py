"""Stable output-directory layout for document exporters."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


OUTPUT_LAYOUT_VERSION = 1
OUTPUT_LAYOUT_FILE = Path(".wandao") / "output-layout.json"
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def add_output_layout_args(parser: argparse.ArgumentParser) -> None:
    """Add the opt-in CLI flag used by the desktop Manifest form.

    Direct CLI callers keep the historical flat layout unless they explicitly
    opt in.  The desktop form passes ``--auto-output-folder`` by default.
    """

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--auto-output-folder",
        dest="auto_output_folder",
        action="store_true",
        default=False,
        help="Create a stable source-name subdirectory below the output root",
    )
    group.add_argument(
        "--flat-output",
        dest="auto_output_folder",
        action="store_false",
        help="Keep the historical flat output layout",
    )


def sanitize_output_folder_name(value: str, fallback: str = "导出内容") -> str:
    """Turn untrusted source text into one safe, readable directory name."""

    text = re.sub(r"[\x00-\x1f\x7f]", "", str(value or "")).strip()
    text = re.sub(r'[<>:"/\\|?*]', "-", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text or text in {".", ".."}:
        text = fallback
    if text.upper() in _WINDOWS_RESERVED_NAMES:
        text = f"{text}-来源"
    return text[:100].rstrip(" .") or fallback


def _source_key(source_id: str, source_name: str) -> str:
    identity = str(source_id or source_name or "unknown").strip()
    return identity or "unknown"


def _load_layout(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"version": OUTPUT_LAYOUT_VERSION, "sources": {}}
    if not isinstance(value, dict) or value.get("version") != OUTPUT_LAYOUT_VERSION:
        return {"version": OUTPUT_LAYOUT_VERSION, "sources": {}}
    sources = value.get("sources")
    return {
        "version": OUTPUT_LAYOUT_VERSION,
        "sources": dict(sources) if isinstance(sources, dict) else {},
    }


def _write_layout(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="output-layout-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _existing_directory_names(root: Path) -> set[str]:
    try:
        return {entry.name.casefold() for entry in root.iterdir() if entry.is_dir()}
    except OSError:
        return set()


def _has_legacy_export_content(root: Path) -> bool:
    """Return whether root contains files from the historical flat layout."""

    try:
        for entry in root.rglob("*"):
            relative = entry.relative_to(root)
            if entry.is_file() and OUTPUT_LAYOUT_FILE.parts[0] not in relative.parts:
                return True
    except OSError:
        return False
    return False


def _unique_name(root: Path, requested: str, owned: str | None = None) -> str:
    existing = _existing_directory_names(root)
    if owned and owned.casefold() == requested.casefold():
        return requested
    if requested.casefold() not in existing:
        return requested
    for index in range(2, 10000):
        candidate = sanitize_output_folder_name(f"{requested} ({index})")
        if candidate.casefold() not in existing:
            return candidate
    digest = hashlib.sha256(requested.encode("utf-8")).hexdigest()[:8]
    return sanitize_output_folder_name(f"{requested}-{digest}")


def resolve_output_directory(
    output_root: str | Path,
    source_name: str,
    source_id: str,
    *,
    auto_output_folder: bool = False,
    preserve_legacy: bool = False,
    fallback: str = "导出内容",
) -> Path:
    """Return the final directory for one export source.

    ``output_root`` remains the user-selected directory.  When enabled, one
    child directory is allocated per stable source identity and recorded in a
    small private index beneath ``output_root/.wandao``.  A failed or renamed
    source therefore returns to the same directory on later runs. Existing
    flat exports are kept in place when ``preserve_legacy`` is enabled and no
    source mapping has been written yet. That first legacy source is recorded
    as ``directory: "."`` so later sources can still receive child folders.
    """

    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not auto_output_folder:
        return root

    layout_path = root / OUTPUT_LAYOUT_FILE
    layout = _load_layout(layout_path)
    sources = layout["sources"]
    key = _source_key(source_id, source_name)
    existing_record = sources.get(key)
    if isinstance(existing_record, dict):
        raw_recorded = str(existing_record.get("directory") or "").strip()
        # ``.`` is the explicit compatibility mapping for a historical flat
        # export. It keeps the first known source in place while allowing a
        # later, different source to receive its own child directory.
        target = (
            root
            if raw_recorded in {"", ".", "./"}
            else root / sanitize_output_folder_name(raw_recorded, fallback)
        )
        target.mkdir(parents=True, exist_ok=True)
        source_text = str(source_name or fallback).strip() or fallback
        if existing_record.get("sourceName") != source_text:
            existing_record["sourceName"] = source_text
            _write_layout(layout_path, layout)
        return target

    if preserve_legacy and not sources and _has_legacy_export_content(root):
        source_text = str(source_name or fallback).strip() or fallback
        sources[key] = {
            "directory": ".",
            "sourceName": source_text,
            "legacy": True,
        }
        _write_layout(layout_path, layout)
        return root

    requested = sanitize_output_folder_name(source_name, fallback)
    owned_names = {
        str(record.get("directory") or "").casefold()
        for record in sources.values()
        if isinstance(record, dict) and record.get("directory")
    }
    candidate = requested
    existing = _existing_directory_names(root)
    if candidate.casefold() in existing and candidate.casefold() not in owned_names:
        candidate = _unique_name(root, candidate)
    elif candidate.casefold() in owned_names:
        candidate = _unique_name(root, candidate)

    sources[key] = {
        "directory": candidate,
        "sourceName": str(source_name or fallback).strip() or fallback,
    }
    _write_layout(layout_path, layout)
    target = root / candidate
    target.mkdir(parents=True, exist_ok=True)
    return target
