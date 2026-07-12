import importlib
import json
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class CheckpointProviderArgsTests(unittest.TestCase):
    def test_export_providers_accept_checkpoint_args(self) -> None:
        cases = [
            ("export_yuque", ["--book-url", "https://www.yuque.com/demo/book", "--output", "out"]),
            ("export_feishu", ["--wiki-url", "https://demo.feishu.cn/wiki/demo", "--output", "out"]),
            ("export_aliyun_thoughts", ["--workspace-url", "https://thoughts.aliyun.com/workspaces/demo/overview", "--output", "out"]),
            ("export_yinxiang", ["--output", "out"]),
            ("export_youdao", ["--output", "out"]),
            ("export_wiz", ["--output", "out"]),
            ("export_onenote", ["--output", "out"]),
            ("ima_knowledge", ["--knowledge-base-id", "kb-1", "--output", "out"]),
        ]

        for module_name, base_args in cases:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                args = module.parse_args(
                    [
                        *base_args,
                        "--checkpoint-file",
                        "out/.wandao/checkpoint.sqlite",
                        "--checkpoint-task-id",
                        "task-1",
                        "--resume",
                        "--retry-failed",
                    ]
                )

                self.assertEqual(args.checkpoint_file, "out/.wandao/checkpoint.sqlite")
                self.assertEqual(args.checkpoint_task_id, "task-1")
                self.assertTrue(args.resume)
                self.assertTrue(args.retry_failed)

    def test_yuque_import_accepts_checkpoint_args(self) -> None:
        module = importlib.import_module("import_yuque")
        args = module.parse_args(
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

    def test_directory_export_providers_accept_doc_id_file(self) -> None:
        cases = [
            ("export_yuque", ["--book-url", "https://www.yuque.com/demo/book", "--output", "out"], "selected_doc_ids"),
            ("export_feishu", ["--wiki-url", "https://demo.feishu.cn/wiki/demo", "--output", "out"], "selected_doc_ids"),
            ("export_aliyun_thoughts", ["--workspace-url", "https://thoughts.aliyun.com/workspaces/demo/overview", "--output", "out"], "selected_doc_ids"),
            ("export_yinxiang", ["--output", "out"], "doc_id"),
            ("export_youdao", ["--output", "out"], "selected_doc_ids"),
            ("export_wiz", ["--output", "out"], "selected_doc_ids"),
            ("export_onenote", ["--output", "out"], "selected_doc_ids"),
            ("ima_knowledge", ["--knowledge-base-id", "kb-1", "--output", "out"], "doc_id"),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            id_file = Path(tmp) / "ids.json"
            id_file.write_text(json.dumps({"docIds": ["doc-a", "doc-b"]}), encoding="utf-8")
            for module_name, base_args, attr in cases:
                with self.subTest(module=module_name):
                    module = importlib.import_module(module_name)
                    args = module.parse_args([*base_args, "--doc-id-file", str(id_file)])

                    self.assertEqual(getattr(args, attr)[-2:], ["doc-a", "doc-b"])

    def test_resource_failures_keep_export_items_resumable(self) -> None:
        expected_markers = {
            "plugins/aliyun_thoughts/backend/export_aliyun_thoughts.py": "checkpoint.fail_item(item_key, f\"{len(img_errors)} 个图片或附件下载失败\")",
            "plugins/feishu/backend/export_feishu.py": "checkpoint.fail_item(item_key, f\"{len(img_errors)} 个图片下载失败\")",
            "plugins/yuque/backend/export_yuque.py": "checkpoint.fail_item(item_key, f\"{len(resource_errors)} 个图片或附件下载失败\")",
            "plugins/wiz/backend/export_wiz.py": "checkpoint.fail_item(item_key, f\"{len(img_failures)} 个图片下载失败\")",
            "plugins/youdao/backend/export_youdao.py": "checkpoint.fail_item(item_key, f\"{resource_failures_in_doc} 个图片或附件下载失败\")",
            "plugins/zsxq/backend/export_zsxq.py": "checkpoint.fail_item(\n                            checkpoint_item_key",
        }
        for filename, marker in expected_markers.items():
            with self.subTest(script=filename):
                source = (REPO_ROOT / filename).read_text(encoding="utf-8")
                self.assertIn(marker, source)


if __name__ == "__main__":
    unittest.main()
