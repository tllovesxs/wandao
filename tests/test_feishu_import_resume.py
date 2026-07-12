import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import import_feishu
from wandao_core.checkpoint import WandaoCheckpoint


class FeishuImportResumeTests(unittest.TestCase):
    def test_batch_import_accepts_checkpoint_resume_and_retry_args(self) -> None:
        args = import_feishu.parse_args(
            [
                "--wiki-url", "https://demo.feishu.cn/wiki/demo",
                "--source-dir", "source",
                "--api-import-all", "--yes",
                "--checkpoint-file", "source/.wandao/feishu-import.sqlite",
                "--checkpoint-task-id", "feishu-import:stable",
                "--resume", "--retry-failed",
            ]
        )

        self.assertEqual(args.checkpoint_file, "source/.wandao/feishu-import.sqlite")
        self.assertEqual(args.checkpoint_task_id, "feishu-import:stable")
        self.assertTrue(args.resume)
        self.assertTrue(args.retry_failed)

    def test_resume_restores_completed_parent_wiki_token_before_selecting_docs(self) -> None:
        docs = [{"relativePath": "A.md"}, {"relativePath": "A/child.md"}]
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WandaoCheckpoint.open(
                Path(tmp) / "checkpoint.sqlite",
                task_id="task-1",
                provider_id="feishu-import",
                action="import",
            )
            checkpoint.start_task({"source": tmp, "target": "https://demo.feishu.cn/wiki/demo"})
            checkpoint.upsert_item("feishu-import:A.md", title="A.md", source_id="A.md")
            checkpoint.upsert_item("feishu-import:A/child.md", title="A/child.md", source_id="A/child.md")
            checkpoint.complete_item("feishu-import:A.md", metadata={"wikiToken": "wiki-parent"})

            try:
                restored = import_feishu.restore_completed_import_tokens(checkpoint)
                resumed = import_feishu.select_checkpoint_docs(
                    docs,
                    checkpoint,
                    resume=True,
                    retry_failed=False,
                )

                self.assertEqual(
                    import_feishu.find_import_parent_token("A/child.md", restored, "wiki-root"),
                    "wiki-parent",
                )
                self.assertEqual([doc["relativePath"] for doc in resumed], ["A/child.md"])
            finally:
                checkpoint.close()

    def test_resume_restores_folder_token_without_creating_duplicate_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = WandaoCheckpoint.open(
                Path(tmp) / "checkpoint.sqlite",
                task_id="task-1",
                provider_id="feishu-import",
                action="import",
            )
            checkpoint.start_task({"source": tmp, "target": "https://demo.feishu.cn/wiki/demo"})
            checkpoint.save_cursor("folder_tokens", {"archive": "wiki-folder"})

            try:
                folder_tokens = import_feishu.restore_folder_tokens(checkpoint)
                args = import_feishu.parse_args(["--yes"])

                with patch.object(import_feishu, "create_folder_placeholder_markdown") as create_folder, patch.object(
                    import_feishu, "import_one_with_openapi"
                ) as import_folder:
                    parent_token = import_feishu.ensure_folder_parent_token(
                        args,
                        relative_path="archive/note.md",
                        imported_by_relative_path={},
                        folder_tokens=folder_tokens,
                        root_parent_token="wiki-root",
                        folder_pages=[],
                    )

                self.assertEqual(parent_token, "wiki-folder")
                create_folder.assert_not_called()
                import_folder.assert_not_called()
            finally:
                checkpoint.close()

    def test_single_import_records_completed_checkpoint_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint_file = Path(tmp) / "checkpoint.sqlite"
            source_file = Path(tmp) / "single.md"
            source_file.write_text("# Single\n", encoding="utf-8")
            args = import_feishu.parse_args(
                [
                    "--wiki-url", "https://demo.feishu.cn/wiki/demo",
                    "--source-file", str(source_file), "--api-import-one", "--yes",
                    "--checkpoint-file", str(checkpoint_file), "--checkpoint-task-id", "single",
                ]
            )
            with patch.object(import_feishu, "import_one_with_openapi", return_value={"wikiToken": "wiki-single"}):
                import_feishu.import_one_with_checkpoint(args)

            checkpoint = WandaoCheckpoint.open(checkpoint_file, "single", "feishu-import", "import")
            try:
                self.assertEqual(checkpoint.item_status(f"feishu-import:{source_file}"), "completed")
            finally:
                checkpoint.close()


if __name__ == "__main__":
    unittest.main()
