import json
import unittest
from pathlib import Path


MANIFEST_PATH = Path(__file__).resolve().parents[1] / "plugins" / "feishu" / "providers" / "feishu-import" / "provider.json"


class FeishuImportManifestTests(unittest.TestCase):
    def test_first_import_notice_is_visible_and_documents_safe_order(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        fields = manifest["fields"]
        notice = fields[0]

        self.assertEqual(notice["name"], "first_import_steps")
        self.assertEqual(notice["type"], "notice")
        self.assertFalse(notice.get("advanced", False))
        self.assertIn("登录并保存 Cookie 凭证", notice["markdown"])
        self.assertIn("我已完成登录，保存凭证", notice["markdown"])
        self.assertIn("两类独立信息", notice["markdown"])
        self.assertIn("检测应用与目标 Wiki，并添加应用", notice["markdown"])
        self.assertIn("生成只读计划", notice["markdown"])
        self.assertIn("单篇导入测试", notice["markdown"])
        self.assertIn("确认后批量导入", notice["markdown"])
        self.assertIn("不会在 Wiki 创建或修改内容", notice["markdown"])
        self.assertIn("会在目标 Wiki 创建测试文档", notice["markdown"])
        self.assertIn("批量导入会在目标 Wiki 创建文档", notice["markdown"])

    def test_single_file_test_precedes_confirmed_batch_import(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        fields = {field["name"]: field for field in manifest["fields"]}
        actions = {action["id"]: action for action in manifest["actions"]}
        action_ids = [action["id"] for action in manifest["actions"]]

        self.assertEqual(fields["source_file"]["type"], "file")
        self.assertEqual(fields["source_file"]["arg"], "--source-file")
        self.assertEqual(fields["source_file"]["actions"], ["importOne"])
        self.assertIn("importOne", fields["source_dir"]["actions"])
        self.assertIn("importOne", fields["wiki_url"]["actions"])

        self.assertLess(action_ids.index("importOne"), action_ids.index("import"))
        self.assertEqual(actions["importOne"]["args"], ["--api-import-one", "--yes"])
        self.assertTrue(actions["importOne"]["secondary"])
        self.assertIn("确认", actions["importOne"]["confirm"])
        self.assertEqual(actions["import"]["args"], ["--api-import-all", "--yes"])
        self.assertIn("确认", actions["import"]["confirm"])


if __name__ == "__main__":
    unittest.main()
