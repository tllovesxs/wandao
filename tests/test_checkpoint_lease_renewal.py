"""Regression tests for lease renewal after the owner's own lease expired.

A slow download or a suspended machine can push the wall clock past
``lease_expires_at`` while nobody else ever claimed the task.  Renewing must
still work in that case, but a lease that was really taken over by another run
(``lease_id`` changed) must keep failing loudly.
"""

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from wandao_core.checkpoint import CheckpointLeaseLostError, WandaoCheckpoint


def _rewrite_lease(path: Path, task_id: str, **columns: object) -> None:
    """Mutate lease bookkeeping from outside, like a crashed/stale peer would."""
    assignments = ", ".join(f"{name} = ?" for name in columns)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            f"UPDATE tasks SET {assignments} WHERE task_id = ?",
            (*columns.values(), task_id),
        )
        conn.commit()
    finally:
        conn.close()


def _lease_row(path: Path, task_id: str) -> sqlite3.Row:
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT lease_id, lease_heartbeat, lease_expires_at FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    finally:
        conn.close()


class CheckpointLeaseRenewalTests(unittest.TestCase):
    def test_own_expired_lease_can_be_renewed_when_nobody_took_over(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.sqlite"
            checkpoint = WandaoCheckpoint.open(
                path,
                task_id="task-1",
                provider_id="onenote",
                action="导出",
                lease_seconds=5,
            )
            try:
                checkpoint.start_task({})
                # A 300+ second download / system sleep: the lease lapsed while
                # this run kept working, and no other run claimed the task.
                _rewrite_lease(path, "task-1", lease_expires_at=time.time() - 3600)

                checkpoint.heartbeat()

                renewed = _lease_row(path, "task-1")
                self.assertEqual(renewed["lease_id"], checkpoint.run_id)
                self.assertGreater(renewed["lease_expires_at"], time.time())
                # Real work must keep flowing after the self-renewal.
                checkpoint.upsert_item("item-1", title="item")
                checkpoint.start_item("item-1", "content")
                checkpoint.complete_item("item-1", local_path="01-item.md")
                checkpoint.complete_task({"exportedDocs": 1})
            finally:
                checkpoint.close()

    def test_renewal_still_fails_when_another_run_took_the_lease_over(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.sqlite"
            first = WandaoCheckpoint.open(path, task_id="task-1", provider_id="onenote", action="导出")
            second = None
            try:
                first.start_task({})
                _rewrite_lease(path, "task-1", lease_expires_at=time.time() - 3600)

                second = WandaoCheckpoint.open(path, task_id="task-1", provider_id="onenote", action="导出")
                second.start_task({})
                self.assertNotEqual(second.run_id, first.run_id)

                with self.assertRaises(CheckpointLeaseLostError):
                    first.heartbeat()
                # The fence must survive the failed renewal, not just trip once.
                with self.assertRaises(CheckpointLeaseLostError):
                    first.heartbeat()
                self.assertEqual(_lease_row(path, "task-1")["lease_id"], second.run_id)
            finally:
                if second is not None:
                    second.close()
                first.close()

    def test_renewal_still_fails_when_the_lease_row_was_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.sqlite"
            checkpoint = WandaoCheckpoint.open(path, task_id="task-1", provider_id="onenote", action="导出")
            try:
                checkpoint.start_task({})
                # release/complete/fail blank the owner column; a stale run that
                # still believes it holds the lease must not resurrect it.
                _rewrite_lease(path, "task-1", lease_id="", lease_expires_at=None)

                with self.assertRaises(CheckpointLeaseLostError):
                    checkpoint.heartbeat()
                self.assertEqual(_lease_row(path, "task-1")["lease_id"], "")
            finally:
                checkpoint.close()


if __name__ == "__main__":
    unittest.main()
