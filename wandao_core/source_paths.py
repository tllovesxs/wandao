"""Safe local-file resolution for Markdown importers.

An import directory is a trust boundary: Markdown inside it must not be able to
reference arbitrary files elsewhere on the machine.  These helpers resolve
references only when the final regular file remains below that boundary.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit


def resolve_source_root(source_root: str | Path) -> Path:
    """Return a canonical import root, rejecting a missing/non-directory root."""

    root = Path(source_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Markdown 来源目录不存在或不是目录：{root}")
    return root


def is_within_source_root(source_root: str | Path, candidate: str | Path) -> bool:
    """Whether *candidate* resolves within *source_root*."""

    root = resolve_source_root(source_root)
    try:
        Path(candidate).expanduser().resolve().relative_to(root)
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def source_root_for_file(configured_root: str | Path | None, markdown_file: str | Path) -> Path:
    """Use the configured root when it contains the file, otherwise its parent.

    A standalone ``--source-file`` is intentionally scoped to its own parent
    directory instead of an unrelated default ``--source-dir``.
    """

    file_path = Path(markdown_file).expanduser().resolve()
    if configured_root:
        try:
            root = resolve_source_root(configured_root)
            file_path.relative_to(root)
            return root
        except (OSError, RuntimeError, ValueError):
            pass
    return resolve_source_root(file_path.parent)


def _decoded_relative_target(raw_target: str) -> tuple[str | None, str | None]:
    target = str(raw_target or "").strip().strip("<>")
    if not target or "\x00" in target:
        return None, "empty_or_invalid"
    try:
        parsed = urlsplit(target)
    except ValueError:
        return None, "invalid_url"
    # A scheme (including file: or C:) and a network-path reference are never
    # local Markdown resources that this importer should read.
    if parsed.scheme or parsed.netloc:
        return None, "non_local_url"
    decoded = unquote(parsed.path or "")
    normalized = decoded.replace("\\", "/")
    if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized):
        return None, "absolute_path"
    if any(part == ".." for part in normalized.split("/")):
        return None, "parent_traversal"
    return normalized, None


def inspect_local_reference(
    source_root: str | Path,
    markdown_file: str | Path,
    raw_target: str,
) -> tuple[Path | None, str | None]:
    """Resolve a Markdown target and return ``(file, None)`` only when safe.

    The reason is intended for a user-visible warning and deliberately avoids
    including the resolved path, which may itself be sensitive.
    """

    root = resolve_source_root(source_root)
    relative_target, reason = _decoded_relative_target(raw_target)
    if reason:
        return None, reason
    try:
        markdown_path = Path(markdown_file).expanduser().resolve()
        markdown_path.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None, "markdown_outside_source_root"
    try:
        candidate = (markdown_path.parent / Path(relative_target)).resolve()
        candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None, "outside_source_root"
    try:
        if not candidate.is_file():
            return None, "missing_or_not_regular_file"
    except OSError:
        return None, "unreadable"
    return candidate, None


def resolve_local_reference(source_root: str | Path, markdown_file: str | Path, raw_target: str) -> Path | None:
    """Return a safe local resource, or ``None`` for every rejected target."""

    path, _reason = inspect_local_reference(source_root, markdown_file, raw_target)
    return path


def iter_regular_files_under_root(
    source_root: str | Path,
    *,
    suffixes: Iterable[str] | None = None,
) -> Iterator[Path]:
    """Yield regular files below the canonical root, excluding escaping symlinks."""

    root = resolve_source_root(source_root)
    normalized_suffixes = {str(suffix).lower() for suffix in suffixes or ()}
    for candidate in root.rglob("*"):
        try:
            if not candidate.is_file():
                continue
            if normalized_suffixes and candidate.suffix.lower() not in normalized_suffixes:
                continue
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            continue
        yield resolved
