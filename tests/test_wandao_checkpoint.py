import argparse
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from wandao_checkpoint import (
    CheckpointInUseError,
    CheckpointLeaseLostError,
    WandaoCheckpoint,
    add_checkpoint_args,
    open_checkpoint_from_args,
)


class WandaoCheckpointTests(unittest.TestCase):
    def test_checkpoint_records_task_item_cursor_and_resource(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.sqlite"
            checkpoint = WandaoCheckpoint.open(path, task_id="task-1", provider_id="zsxq-group", action="导出")
            try:
                checkpoint.start_task({"source": "https://wx.zsxq.com/group/1", "outputDir": tmp})
                checkpoint.save_cursor("group", {"end_time": "2026-07-09T10:00:00.000+0800", "page": 3})
                checkpoint.upsert_item("zsxq:topic:1", title="测试帖子", source_url="https://wx.zsxq.com/topic/1")
                checkpoint.start_item("zsxq:topic:1", "content")
                checkpoint.upsert_resource("zsxq:topic:1", "image:https://example.com/a.png", "image", "https://example.com/a.png")
                checkpoint.start_resource("image:https://example.com/a.png")
                checkpoint.complete_resource("image:https://example.com/a.png", local_path="assets/a.png")
                checkpoint.complete_item("zsxq:topic:1", local_path="01-测试帖子.md")
                checkpoint.complete_task({"exportedDocs": 1})

                self.assertEqual(checkpoint.load_cursor("group")["page"], 3)
                self.assertIn("zsxq:topic:1", checkpoint.completed_item_keys())
                stats = checkpoint.stats()
                self.assertEqual(stats["items"]["completed"], 1)
                self.assertEqual(stats["resources"]["completed"], 1)
            finally:
                checkpoint.close()

    def test_open_does_not_recover_or_take_over_a_live_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.sqlite"
            checkpoint = WandaoCheckpoint.open(path, task_id="task-1", provider_id="zsxq-group", action="导出")
            try:
                checkpoint.start_task({})
                checkpoint.upsert_item("item-1", title="item")
                checkpoint.start_item("item-1", "content")

                reopened = WandaoCheckpoint.open(path, task_id="task-1", provider_id="zsxq-group", action="导出")
                try:
                    self.assertEqual(reopened.item_status("item-1"), "running")
                    with self.assertRaises(CheckpointInUseError):
                        reopened.reset_task()
                    with self.assertRaises(CheckpointInUseError):
                        reopened.start_task({})
                    self.assertEqual(checkpoint.item_status("item-1"), "running")
                finally:
                    reopened.close()
            finally:
                checkpoint.close()

    def test_close_releases_lease_and_next_claim_recovers_running_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.sqlite"
            checkpoint = WandaoCheckpoint.open(path, task_id="task-1", provider_id="zsxq-group", action="导出")
            checkpoint.start_task({})
            checkpoint.upsert_item("item-1", title="item")
            checkpoint.start_item("item-1", "content")
            checkpoint.close()

            reopened = WandaoCheckpoint.open(path, task_id="task-1", provider_id="zsxq-group", action="导出")
            try:
                self.assertEqual(reopened.item_status("item-1"), "running")
                reopened.start_task({})
                self.assertEqual(reopened.item_status("item-1"), "pending")
            finally:
                reopened.close()

    def test_heartbeat_renews_and_close_clears_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.sqlite"
            checkpoint = WandaoCheckpoint.open(
                path,
                task_id="task-1",
                provider_id="zsxq-group",
                action="导出",
                lease_seconds=5,
            )
            checkpoint.start_task({})
            before = checkpoint.conn.execute(
                "SELECT lease_id, lease_heartbeat, lease_expires_at FROM tasks WHERE task_id = ?",
                ("task-1",),
            ).fetchone()
            time.sleep(0.01)
            checkpoint.heartbeat()
            renewed = checkpoint.conn.execute(
                "SELECT lease_id, lease_heartbeat, lease_expires_at FROM tasks WHERE task_id = ?",
                ("task-1",),
            ).fetchone()
            self.assertEqual(renewed["lease_id"], checkpoint.run_id)
            self.assertGreater(renewed["lease_heartbeat"], before["lease_heartbeat"])
            self.assertGreater(renewed["lease_expires_at"], before["lease_expires_at"])
            checkpoint.close()

            conn = sqlite3.connect(path)
            try:
                lease = conn.execute(
                    "SELECT lease_id, lease_pid, lease_heartbeat, lease_expires_at FROM tasks WHERE task_id = ?",
                    ("task-1",),
                ).fetchone()
                self.assertEqual(lease[0], "")
                self.assertEqual(lease[1:], (None, None, None))
            finally:
                conn.close()

    def test_expired_lease_can_be_taken_over_and_fences_old_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.sqlite"
            first = WandaoCheckpoint.open(path, task_id="task-1", provider_id="zsxq-group", action="导出")
            second = None
            try:
                first.start_task({})
                first.upsert_item("item-1", title="item")
                first.start_item("item-1", "content")
                conn = sqlite3.connect(path)
                try:
                    conn.execute(
                        "UPDATE tasks SET lease_expires_at = ? WHERE task_id = ?",
                        (time.time() - 1, "task-1"),
                    )
                    conn.commit()
                finally:
                    conn.close()

                second = WandaoCheckpoint.open(path, task_id="task-1", provider_id="zsxq-group", action="导出")
                second.start_task({})
                self.assertEqual(second.item_status("item-1"), "pending")
                with self.assertRaises(CheckpointLeaseLostError):
                    first.start_item("item-1", "content")
            finally:
                if second is not None:
                    second.close()
                first.close()

    def test_legacy_database_is_migrated_without_losing_checkpoint_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.sqlite"
            conn = sqlite3.connect(path)
            try:
                conn.execute(
                    """
                    CREATE TABLE tasks (
                        task_id TEXT PRIMARY KEY, provider_id TEXT, action TEXT, resume_key TEXT,
                        args_hash TEXT, source TEXT, target TEXT, output_dir TEXT, status TEXT,
                        current_stage TEXT, metadata_json TEXT, error_summary TEXT, created_at TEXT,
                        updated_at TEXT, completed_at TEXT
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO tasks (task_id, provider_id, action, status) VALUES ('task-1', 'demo', 'export', 'completed')"
                )
                conn.commit()
            finally:
                conn.close()

            checkpoint = WandaoCheckpoint.open(path, task_id="task-1", provider_id="demo", action="export")
            try:
                columns = {row["name"] for row in checkpoint.conn.execute("PRAGMA table_info(tasks)")}
                self.assertTrue({"lease_id", "lease_pid", "lease_heartbeat", "lease_expires_at"}.issubset(columns))
                self.assertEqual(checkpoint.conn.execute("SELECT status FROM tasks WHERE task_id = 'task-1'").fetchone()["status"], "completed")
            finally:
                checkpoint.close()

    def test_source_scope_change_resets_old_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.sqlite"
            checkpoint = WandaoCheckpoint.open(path, task_id="default", provider_id="demo", action="export")
            try:
                checkpoint.start_task({"source": "book-a", "outputDir": tmp})
                checkpoint.upsert_item("item-a", title="A")
                checkpoint.complete_item("item-a", local_path="a.md")

                checkpoint.start_task({"source": "book-a", "outputDir": tmp})
                self.assertEqual(checkpoint.item_status("item-a"), "completed")

                checkpoint.start_task({"source": "book-b", "outputDir": tmp})
                self.assertEqual(checkpoint.item_status("item-a"), "")
                self.assertEqual(checkpoint.stats()["items"], {})
            finally:
                checkpoint.close()

    def test_open_checkpoint_from_args_can_reset_current_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.sqlite"
            checkpoint = WandaoCheckpoint.open(path, task_id="default", provider_id="demo", action="export")
            checkpoint.start_task({})
            checkpoint.upsert_item("item-1", title="item")
            checkpoint.close()

            args = argparse.Namespace(
                checkpoint_file=str(path),
                checkpoint_task_id="default",
                reset_checkpoint=True,
            )
            reopened = open_checkpoint_from_args(args, "demo", "export")
            try:
                self.assertIsNotNone(reopened)
                assert reopened is not None
                self.assertEqual(reopened.pending_items(), [])
                self.assertEqual(reopened.stats()["items"], {})
            finally:
                reopened.close()

    def test_add_checkpoint_args_uses_retry_failed_dest(self) -> None:
        parser = argparse.ArgumentParser()
        add_checkpoint_args(parser)

        args = parser.parse_args(["--checkpoint-file", "out/.wandao/checkpoint.sqlite", "--resume", "--retry-failed"])

        self.assertEqual(args.checkpoint_file, "out/.wandao/checkpoint.sqlite")
        self.assertTrue(args.resume)
        self.assertTrue(args.retry_failed)


if __name__ == "__main__":
    unittest.main()
