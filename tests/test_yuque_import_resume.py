import tempfile
import unittest
from pathlib import Path

import import_yuque
from wandao_core.checkpoint import WandaoCheckpoint


REPO_ROOT = Path(__file__).resolve().parents[1]


class YuqueImportResumeTests(unittest.TestCase):
    def test_parser_accepts_checkpoint_resume_and_retry_args(self) -> None:
        args = import_yuque.parse_args(
            [
                "--target-book-url", "https://www.yuque.com/demo/book",
                "--source-dir", "source",
                "--api-import-all", "--yes",
                "--checkpoint-file", "source/.wandao/yuque-import.sqlite",
                "--checkpoint-task-id", "task-1",
                "--resume", "--retry-failures",
            ]
        )

        self.assertEqual(args.checkpoint_file, "source/.wandao/yuque-import.sqlite")
        self.assertEqual(args.checkpoint_task_id, "task-1")
        self.assertTrue(args.resume)
        self.assertTrue(args.retry_failed)

    def test_import_uses_checkpoint_retry_attribute_consistently(self) -> None:
        source = (REPO_ROOT / "plugins" / "yuque" / "backend" / "import_yuque.py").read_text(encoding="utf-8")

        self.assertNotIn('getattr(args, "retry_failures"', source)

    def test_resume_skips_completed_items_and_retry_selects_failed_items(self) -> None:
        docs = [{"relativePath": "a.md"}, {"relativePath": "b.md"}]
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WandaoCheckpoint.open(
                Path(tmp) / "checkpoint.sqlite",
                task_id="task-1",
                provider_id="yuque-import",
                action="import",
            )
            checkpoint.start_task({"source": tmp, "target": "https://www.yuque.com/demo/book"})
            checkpoint.upsert_item("yuque-import:a.md", title="a.md", source_id="a.md")
            checkpoint.upsert_item("yuque-import:b.md", title="b.md", source_id="b.md")
            checkpoint.complete_item("yuque-import:a.md")
            checkpoint.fail_item("yuque-import:b.md", "stopped")

            try:
                resumed = import_yuque.select_checkpoint_docs(docs, checkpoint, resume=True, retry_failed=False)
                retried = import_yuque.select_checkpoint_docs(docs, checkpoint, resume=False, retry_failed=True)

                self.assertEqual([doc["relativePath"] for doc in resumed], ["b.md"])
                self.assertEqual([doc["relativePath"] for doc in retried], ["b.md"])
            finally:
                checkpoint.close()


if __name__ == "__main__":
    unittest.main()
