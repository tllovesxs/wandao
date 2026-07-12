import unittest

import import_feishu


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


if __name__ == "__main__":
    unittest.main()
