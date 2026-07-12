from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import time
import uuid
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
RUNNING_STATES = {"running", "interrupted"}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return {} if default is None else default
    try:
        return json.loads(value)
    except Exception:
        return {} if default is None else default


def resume_scope_key(provider_id: str, action: str, metadata: dict[str, Any]) -> str:
    explicit = str(metadata.get("resumeKey") or "").strip()
    if explicit:
        return explicit
    identity = {
        "provider": provider_id,
        "action": action,
        "source": str(metadata.get("source") or ""),
        "target": str(metadata.get("target") or ""),
        "outputDir": str(metadata.get("outputDir") or ""),
    }
    for key in (
        "workspaceId",
        "rootId",
        "groupId",
        "groupScope",
        "knowledgeBaseId",
        "entryKind",
        "sourceMode",
    ):
        if metadata.get(key) not in (None, ""):
            identity[key] = metadata[key]
    encoded = json_dumps(identity).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class WandaoCheckpoint:
    def __init__(self, path: Path, task_id: str, provider_id: str, action: str) -> None:
        self.path = Path(path).expanduser().resolve()
        self.task_id = task_id
        self.provider_id = provider_id
        self.action = action
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()
        self.recover_interrupted()

    @classmethod
    def open(
        cls,
        path: str | Path,
        task_id: str | None = None,
        provider_id: str = "",
        action: str = "",
    ) -> "WandaoCheckpoint":
        return cls(Path(path), task_id or str(uuid.uuid4()), provider_id, action)

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    provider_id TEXT,
                    action TEXT,
                    resume_key TEXT,
                    args_hash TEXT,
                    source TEXT,
                    target TEXT,
                    output_dir TEXT,
                    status TEXT,
                    current_stage TEXT,
                    metadata_json TEXT,
                    error_summary TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    completed_at TEXT
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cursors (
                    task_id TEXT,
                    cursor_name TEXT,
                    cursor_value_json TEXT,
                    updated_at TEXT,
                    PRIMARY KEY (task_id, cursor_name)
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS items (
                    task_id TEXT,
                    item_key TEXT,
                    parent_key TEXT,
                    title TEXT,
                    source_url TEXT,
                    source_id TEXT,
                    target_id TEXT,
                    local_path TEXT,
                    status TEXT,
                    stage TEXT,
                    attempts INTEGER DEFAULT 0,
                    last_error TEXT,
                    metadata_json TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    completed_at TEXT,
                    PRIMARY KEY (task_id, item_key)
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS resources (
                    task_id TEXT,
                    item_key TEXT,
                    resource_key TEXT,
                    resource_type TEXT,
                    source TEXT,
                    target TEXT,
                    local_path TEXT,
                    status TEXT,
                    attempts INTEGER DEFAULT 0,
                    last_error TEXT,
                    metadata_json TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    completed_at TEXT,
                    PRIMARY KEY (task_id, resource_key)
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    task_id TEXT,
                    level TEXT,
                    event TEXT,
                    message TEXT,
                    item_key TEXT,
                    resource_key TEXT,
                    data_json TEXT,
                    created_at TEXT
                )
                """
            )
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_items_status ON items (task_id, status)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_resources_status ON resources (task_id, status)")
            self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))

    def recover_interrupted(self) -> None:
        ts = now_iso()
        with self.conn:
            self.conn.execute(
                "UPDATE tasks SET status = 'interrupted', updated_at = ? WHERE task_id = ? AND status = 'running'",
                (ts, self.task_id),
            )
            self.conn.execute(
                "UPDATE items SET status = 'pending', updated_at = ? WHERE task_id = ? AND status = 'running'",
                (ts, self.task_id),
            )
            self.conn.execute(
                "UPDATE resources SET status = 'pending', updated_at = ? WHERE task_id = ? AND status = 'running'",
                (ts, self.task_id),
            )

    def reset_task(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM events WHERE task_id = ?", (self.task_id,))
            self.conn.execute("DELETE FROM resources WHERE task_id = ?", (self.task_id,))
            self.conn.execute("DELETE FROM items WHERE task_id = ?", (self.task_id,))
            self.conn.execute("DELETE FROM cursors WHERE task_id = ?", (self.task_id,))
            self.conn.execute("DELETE FROM tasks WHERE task_id = ?", (self.task_id,))

    def start_task(self, metadata: dict[str, Any] | None = None) -> None:
        metadata = metadata or {}
        ts = now_iso()
        resume_key = resume_scope_key(self.provider_id, self.action, metadata)
        previous = self.conn.execute(
            "SELECT provider_id, action, resume_key FROM tasks WHERE task_id = ?",
            (self.task_id,),
        ).fetchone()
        if previous and (
            str(previous["provider_id"] or "") != self.provider_id
            or str(previous["action"] or "") != self.action
            or str(previous["resume_key"] or "") != resume_key
        ):
            self.reset_task()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO tasks (
                    task_id, provider_id, action, resume_key, args_hash, source, target, output_dir,
                    status, current_stage, metadata_json, error_summary, created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, '', ?, ?, '')
                ON CONFLICT(task_id) DO UPDATE SET
                    provider_id = excluded.provider_id,
                    action = excluded.action,
                    resume_key = excluded.resume_key,
                    args_hash = excluded.args_hash,
                    source = excluded.source,
                    target = excluded.target,
                    output_dir = excluded.output_dir,
                    status = 'running',
                    current_stage = excluded.current_stage,
                    metadata_json = excluded.metadata_json,
                    error_summary = '',
                    updated_at = excluded.updated_at,
                    completed_at = ''
                """,
                (
                    self.task_id,
                    self.provider_id,
                    self.action,
                    resume_key,
                    str(metadata.get("argsHash") or ""),
                    str(metadata.get("source") or ""),
                    str(metadata.get("target") or ""),
                    str(metadata.get("outputDir") or ""),
                    str(metadata.get("stage") or "started"),
                    json_dumps(metadata),
                    ts,
                    ts,
                ),
            )

    def complete_task(self, summary: dict[str, Any] | None = None) -> None:
        ts = now_iso()
        with self.conn:
            self.conn.execute(
                """
                UPDATE tasks
                SET status = 'completed', current_stage = 'completed', metadata_json = ?,
                    updated_at = ?, completed_at = ?
                WHERE task_id = ?
                """,
                (json_dumps(summary or {}), ts, ts, self.task_id),
            )

    def fail_task(self, error: str, status: str = "failed") -> None:
        ts = now_iso()
        with self.conn:
            self.conn.execute(
                "UPDATE tasks SET status = ?, error_summary = ?, updated_at = ? WHERE task_id = ?",
                (status, str(error), ts, self.task_id),
            )

    def save_cursor(self, name: str, value: dict[str, Any]) -> None:
        ts = now_iso()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO cursors (task_id, cursor_name, cursor_value_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(task_id, cursor_name) DO UPDATE SET
                    cursor_value_json = excluded.cursor_value_json,
                    updated_at = excluded.updated_at
                """,
                (self.task_id, name, json_dumps(value), ts),
            )

    def load_cursor(self, name: str, default: Any = None) -> Any:
        row = self.conn.execute(
            "SELECT cursor_value_json FROM cursors WHERE task_id = ? AND cursor_name = ?",
            (self.task_id, name),
        ).fetchone()
        return json_loads(row["cursor_value_json"], default) if row else ({} if default is None else default)

    def upsert_item(
        self,
        item_key: str,
        title: str = "",
        source_url: str = "",
        source_id: str = "",
        parent_key: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not item_key:
            return
        ts = now_iso()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO items (
                    task_id, item_key, parent_key, title, source_url, source_id, target_id,
                    local_path, status, stage, attempts, last_error, metadata_json,
                    created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, '', '', 'pending', 'listed', 0, '', ?, ?, ?, '')
                ON CONFLICT(task_id, item_key) DO UPDATE SET
                    parent_key = COALESCE(NULLIF(excluded.parent_key, ''), items.parent_key),
                    title = COALESCE(NULLIF(excluded.title, ''), items.title),
                    source_url = COALESCE(NULLIF(excluded.source_url, ''), items.source_url),
                    source_id = COALESCE(NULLIF(excluded.source_id, ''), items.source_id),
                    metadata_json = CASE
                        WHEN items.status = 'completed' THEN items.metadata_json
                        ELSE excluded.metadata_json
                    END,
                    updated_at = excluded.updated_at
                """,
                (
                    self.task_id,
                    item_key,
                    parent_key,
                    title,
                    source_url,
                    source_id,
                    json_dumps(metadata or {}),
                    ts,
                    ts,
                ),
            )

    def start_item(self, item_key: str, stage: str | None = None) -> None:
        if not item_key:
            return
        ts = now_iso()
        with self.conn:
            self.conn.execute(
                """
                UPDATE items
                SET status = 'running', stage = COALESCE(?, stage), attempts = attempts + 1,
                    last_error = '', updated_at = ?
                WHERE task_id = ? AND item_key = ? AND status != 'completed'
                """,
                (stage, ts, self.task_id, item_key),
            )

    def complete_item(
        self,
        item_key: str,
        local_path: str | None = None,
        target_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not item_key:
            return
        ts = now_iso()
        with self.conn:
            self.conn.execute(
                """
                UPDATE items
                SET status = 'completed', stage = 'completed',
                    local_path = COALESCE(?, local_path),
                    target_id = COALESCE(?, target_id),
                    metadata_json = COALESCE(?, metadata_json),
                    updated_at = ?, completed_at = ?
                WHERE task_id = ? AND item_key = ?
                """,
                (
                    local_path,
                    target_id,
                    json_dumps(metadata) if metadata is not None else None,
                    ts,
                    ts,
                    self.task_id,
                    item_key,
                ),
            )

    def fail_item(self, item_key: str, error: str, retryable: bool = True) -> None:
        if not item_key:
            return
        ts = now_iso()
        status = "failed" if retryable else "skipped"
        with self.conn:
            self.conn.execute(
                "UPDATE items SET status = ?, last_error = ?, updated_at = ? WHERE task_id = ? AND item_key = ?",
                (status, str(error), ts, self.task_id, item_key),
            )

    def skip_item(self, item_key: str, reason: str = "") -> None:
        self.fail_item(item_key, reason, retryable=False)

    def item_status(self, item_key: str) -> str:
        row = self.conn.execute(
            "SELECT status FROM items WHERE task_id = ? AND item_key = ?",
            (self.task_id, item_key),
        ).fetchone()
        return str(row["status"]) if row else ""

    def completed_item_keys(self) -> set[str]:
        return {
            str(row["item_key"])
            for row in self.conn.execute(
                "SELECT item_key FROM items WHERE task_id = ? AND status = 'completed'",
                (self.task_id,),
            )
        }

    def completed_items(self) -> list[dict[str, Any]]:
        return self._items_by_status({"completed"})

    def pending_items(self) -> list[dict[str, Any]]:
        return self._items_by_status({"pending", "failed", "interrupted"})

    def failed_items(self) -> list[dict[str, Any]]:
        return self._items_by_status({"failed"})

    def _items_by_status(self, statuses: Iterable[str]) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            f"SELECT * FROM items WHERE task_id = ? AND status IN ({','.join('?' for _ in statuses)})",
            (self.task_id, *list(statuses)),
        ).fetchall()
        return [dict(row) for row in rows]

    def upsert_resource(
        self,
        item_key: str,
        resource_key: str,
        resource_type: str,
        source: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not resource_key:
            return
        ts = now_iso()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO resources (
                    task_id, item_key, resource_key, resource_type, source, target, local_path,
                    status, attempts, last_error, metadata_json, created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, '', '', 'pending', 0, '', ?, ?, ?, '')
                ON CONFLICT(task_id, resource_key) DO UPDATE SET
                    item_key = excluded.item_key,
                    resource_type = excluded.resource_type,
                    source = excluded.source,
                    metadata_json = CASE
                        WHEN resources.status = 'completed' THEN resources.metadata_json
                        ELSE excluded.metadata_json
                    END,
                    updated_at = excluded.updated_at
                """,
                (self.task_id, item_key, resource_key, resource_type, source, json_dumps(metadata or {}), ts, ts),
            )

    def start_resource(self, resource_key: str) -> None:
        if not resource_key:
            return
        ts = now_iso()
        with self.conn:
            self.conn.execute(
                """
                UPDATE resources
                SET status = 'running', attempts = attempts + 1, last_error = '', updated_at = ?
                WHERE task_id = ? AND resource_key = ? AND status != 'completed'
                """,
                (ts, self.task_id, resource_key),
            )

    def complete_resource(
        self,
        resource_key: str,
        local_path: str | None = None,
        target: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not resource_key:
            return
        ts = now_iso()
        with self.conn:
            self.conn.execute(
                """
                UPDATE resources
                SET status = 'completed',
                    local_path = COALESCE(?, local_path),
                    target = COALESCE(?, target),
                    metadata_json = COALESCE(?, metadata_json),
                    updated_at = ?, completed_at = ?
                WHERE task_id = ? AND resource_key = ?
                """,
                (
                    local_path,
                    target,
                    json_dumps(metadata) if metadata is not None else None,
                    ts,
                    ts,
                    self.task_id,
                    resource_key,
                ),
            )

    def fail_resource(self, resource_key: str, error: str, retryable: bool = True) -> None:
        if not resource_key:
            return
        ts = now_iso()
        status = "failed" if retryable else "skipped"
        with self.conn:
            self.conn.execute(
                "UPDATE resources SET status = ?, last_error = ?, updated_at = ? WHERE task_id = ? AND resource_key = ?",
                (status, str(error), ts, self.task_id, resource_key),
            )

    def resource_status(self, resource_key: str) -> str:
        row = self.conn.execute(
            "SELECT status FROM resources WHERE task_id = ? AND resource_key = ?",
            (self.task_id, resource_key),
        ).fetchone()
        return str(row["status"]) if row else ""

    def resource_record(self, resource_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM resources WHERE task_id = ? AND resource_key = ?",
            (self.task_id, resource_key),
        ).fetchone()
        return dict(row) if row else None

    def event(
        self,
        level: str,
        event: str,
        message: str,
        item_key: str = "",
        resource_key: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO events (event_id, task_id, level, event, message, item_key, resource_key, data_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    self.task_id,
                    level,
                    event,
                    message,
                    item_key,
                    resource_key,
                    json_dumps(data or {}),
                    now_iso(),
                ),
            )

    def stats(self) -> dict[str, Any]:
        item_rows = self.conn.execute(
            "SELECT status, COUNT(*) AS count FROM items WHERE task_id = ? GROUP BY status",
            (self.task_id,),
        ).fetchall()
        resource_rows = self.conn.execute(
            "SELECT status, COUNT(*) AS count FROM resources WHERE task_id = ? GROUP BY status",
            (self.task_id,),
        ).fetchall()
        return {
            "checkpointFile": str(self.path),
            "items": {str(row["status"]): int(row["count"]) for row in item_rows},
            "resources": {str(row["status"]): int(row["count"]) for row in resource_rows},
        }


def add_checkpoint_args(parser: ArgumentParser, retry_flag: str = "--retry-failed") -> None:
    parser.add_argument("--checkpoint-file", help="SQLite checkpoint file for precise resume")
    parser.add_argument("--checkpoint-task-id", default=os.environ.get("WANDAO_JOB_ID") or "default", help="Stable job id inside the checkpoint database")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint when available")
    parser.add_argument(retry_flag, dest="retry_failed", action="store_true", help="Only retry failed checkpoint items")
    parser.add_argument("--reset-checkpoint", action="store_true", help="Reset this checkpoint task before starting")


def open_checkpoint_from_args(
    args: Namespace,
    provider_id: str,
    action: str,
    *,
    default_task_id: str = "default",
) -> WandaoCheckpoint | None:
    checkpoint_file = str(getattr(args, "checkpoint_file", "") or "").strip()
    if not checkpoint_file:
        return None
    checkpoint = WandaoCheckpoint.open(
        Path(checkpoint_file).expanduser().resolve(),
        task_id=str(getattr(args, "checkpoint_task_id", "") or os.environ.get("WANDAO_JOB_ID") or default_task_id),
        provider_id=provider_id,
        action=action,
    )
    if getattr(args, "reset_checkpoint", False):
        checkpoint.reset_task()
    return checkpoint
