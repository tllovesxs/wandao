import argparse
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.wiz.backend import export_wiz
from wandao_core.checkpoint import WandaoCheckpoint


class FakeCdp:
    def close(self) -> None:
        pass


def make_doc_snapshot() -> dict[str, object]:
    return {
        "account": {"userGuid": "account-guid", "userId": "account-id", "hasToken": True},
        "kbs": [{"kbGuid": "kb", "kbServer": "https://example.invalid"}],
        "docs": [{"kbGuid": "kb", "docGuid": "doc-1", "title": "图片失败的笔记", "category": "/", "type": "note"}],
    }


def make_args(output: Path, checkpoint_file: Path, *, retry_failed: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        output=str(output),
        checkpoint_file=str(checkpoint_file),
        checkpoint_task_id="wiz-retry-failed",
        reset_checkpoint=False,
        selected_doc_ids=[],
        incremental=False,
        resume=retry_failed,
        retry_failed=retry_failed,
        progress_every=100,
        close_started_chrome=False,
        request_delay=0,
        request_jitter=0,
    )


class WizRetryFailedTests(unittest.TestCase):
    def test_retry_failed_does_not_skip_existing_markdown(self) -> None:
        self.assertFalse(
            export_wiz.should_skip_existing_doc(
                incremental=True,
                path_exists=True,
                retry_failed=True,
            )
        )

    def test_incremental_export_still_skips_existing_markdown_normally(self) -> None:
        self.assertTrue(
            export_wiz.should_skip_existing_doc(
                incremental=True,
                path_exists=True,
                retry_failed=False,
            )
        )

    def test_image_failure_completes_document_and_is_not_retried_as_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint_file = root / "checkpoint.sqlite"
            args = make_args(root / "output", checkpoint_file)
            with (
                patch.object(export_wiz, "connect_wiz_browser", return_value=(FakeCdp(), None)),
                patch.object(export_wiz, "wait_for_login_state", return_value=make_doc_snapshot()),
                patch.object(export_wiz, "fetch_note_download", return_value={"html": '<p>正文<img src="https://images.example.invalid/fail.png"></p>'}),
                patch.object(export_wiz.ResourceSaver, "fetch_external_base64", side_effect=export_wiz.ExportError("timed out")),
                patch.object(export_wiz, "emit"),
            ):
                report = export_wiz.export_wiz(args)

            self.assertEqual(report["exported"], 1)
            self.assertEqual(report["failureCount"], 0)
            self.assertEqual(report["outcome"], "partial")

            connection = sqlite3.connect(checkpoint_file)
            try:
                item_status = connection.execute(
                    "SELECT status FROM items WHERE task_id = ? AND item_key = ?",
                    ("wiz-retry-failed", "wiz:doc:doc-1"),
                ).fetchone()[0]
                resource = connection.execute(
                    "SELECT status, source FROM resources WHERE task_id = ?",
                    ("wiz-retry-failed",),
                ).fetchone()
                task_status = connection.execute(
                    "SELECT status FROM tasks WHERE task_id = ?",
                    ("wiz-retry-failed",),
                ).fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(item_status, "completed")
            self.assertEqual(resource, ("failed", "https://images.example.invalid/fail.png"))
            self.assertEqual(task_status, "completed")

            retry_args = make_args(root / "output", checkpoint_file, retry_failed=True)
            with (
                patch.object(export_wiz, "connect_wiz_browser", return_value=(FakeCdp(), None)),
                patch.object(export_wiz, "wait_for_login_state", return_value=make_doc_snapshot()),
                patch.object(export_wiz, "export_doc") as export_doc,
                patch.object(export_wiz, "emit"),
            ):
                retry_report = export_wiz.export_wiz(retry_args)

        self.assertEqual(retry_report["total"], 0)
        export_doc.assert_not_called()

    def test_retry_failed_migrates_legacy_image_failure_with_existing_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint_file = root / "checkpoint.sqlite"
            output = root / "output"
            doc = export_wiz.docs_from_snapshot(make_doc_snapshot())[0]
            md_path = export_wiz.PathPlanner(output).markdown_path(doc)
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text("# 图片失败的笔记\n\n正文\n", encoding="utf-8")
            checkpoint = WandaoCheckpoint.open(checkpoint_file, "wiz-retry-failed", "wiz", "export")
            try:
                checkpoint.start_task({"source": export_wiz.WIZ_APP_URL, "outputDir": str(output)})
                checkpoint.upsert_item("wiz:doc:doc-1", title=doc.title, source_id=doc.doc_guid)
                checkpoint.fail_item("wiz:doc:doc-1", "1 个图片下载失败")
                checkpoint.fail_task("1 个图片下载失败")
            finally:
                checkpoint.close()

            with (
                patch.object(export_wiz, "connect_wiz_browser", return_value=(FakeCdp(), None)),
                patch.object(export_wiz, "wait_for_login_state", return_value=make_doc_snapshot()),
                patch.object(export_wiz, "export_doc") as export_doc,
                patch.object(export_wiz, "emit"),
            ):
                retry_report = export_wiz.export_wiz(make_args(output, checkpoint_file, retry_failed=True))

            connection = sqlite3.connect(checkpoint_file)
            try:
                item_status = connection.execute(
                    "SELECT status FROM items WHERE task_id = ? AND item_key = ?",
                    ("wiz-retry-failed", "wiz:doc:doc-1"),
                ).fetchone()[0]
            finally:
                connection.close()

        self.assertEqual(retry_report["total"], 0)
        self.assertEqual(item_status, "completed")
        export_doc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
