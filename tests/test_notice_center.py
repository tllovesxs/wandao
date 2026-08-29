import hashlib
import json
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class NoticeCenterTests(unittest.TestCase):
    def test_notice_manifest_includes_ai_project_learning_tutorial(self) -> None:
        manifest = json.loads((REPO_ROOT / "docs" / "tutorial-announcements.json").read_text(encoding="utf-8"))
        items = manifest["items"]

        self.assertEqual(
            [item["id"] for item in items],
            ["provider-co-creation-invite", "project-learning-ai-prompt", "wandao-ai-project-learning"],
        )
        self.assertTrue(items[0]["pinned"])
        self.assertEqual(items[0]["type"], "announcement")
        self.assertEqual(items[1]["type"], "announcement")
        self.assertEqual(items[1]["badge"], "AI 学习")
        self.assertEqual(items[2]["type"], "tutorial")
        self.assertEqual(items[2]["title"], "用万能导 + Codex 辅助学习代码项目")
        self.assertEqual(items[2]["date"], "2026-08-29")
        self.assertEqual(items[2]["path"], "docs/tutorials/wandao-ai-project-learning.md")

    def test_ai_project_learning_tutorial_keeps_expected_structure_and_original_images(self) -> None:
        tutorial = (REPO_ROOT / "docs" / "tutorials" / "wandao-ai-project-learning.md").read_text(encoding="utf-8")
        self.assertIn("# 用万能导 + Codex 辅助学习代码项目", tutorial)
        self.assertIn("## 一、使用万能导导出教学文档", tutorial)
        self.assertIn("## 三、为 Codex 配置 API Key", tutorial)
        self.assertIn("## 四、在 Codex 中创建学习项目", tutorial)
        self.assertIn("## 六、如何向 AI 提问", tutorial)
        image_references = re.findall(r"!\[[^\]]*\]\(\.\./images/wandao-ai-project-learning/([^\)]+)\)", tutorial)
        self.assertEqual(image_references, [
            "01.png", "02.png", "03.png", "04.png", "05.jpeg",
            "06.png", "07.png", "08.png", "09.png", "10.png",
        ])

        expected_hashes = {
            "01.png": "a705ce3bf38b5cffb4d28ecbe02e1b7646cf8ca746bde9585100bb1c1702d8aa",
            "02.png": "abeb43b14c5621ece6ec836937888c6e5a85b212189ab7b8a18907215c32581b",
            "03.png": "bc1379f119fdf89c24660b8f4e5c04335f0908c5c271dae0014d1ddab41b79a3",
            "04.png": "e2d637a8b96299d8e64a6a5681676deb87f785fa84fa8439e646506cc157d2fc",
            "05.jpeg": "f73d9290ef35d33ce8866220da05b99a0eaaceae0c739a6362e18655db9691db",
            "06.png": "a4fffaef4c2de89603598ebfdd7a5427a940e78393181c3f975d546d38b461db",
            "07.png": "da30400a557f369a60ff99a0460f06cbcd4ab6602a749f1ad08eb9d9184be193",
            "08.png": "d24bb14f14bf26b0fbc5240198826c1ece77ad826d63b290d30e2ec1f6dbdaff",
            "09.png": "ed77e777a16e434b70ad1a301110d84d1efcc9594831c4e361de2f5860fd4435",
            "10.png": "bc2ded5a14936b6fc26bb0b4ecb8231b8e8af4db5128f29ba0a580dba1547773",
        }
        image_root = REPO_ROOT / "docs" / "images" / "wandao-ai-project-learning"
        actual_hashes = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(image_root.iterdir())
            if path.is_file()
        }
        self.assertEqual(actual_hashes, expected_hashes)

    def test_notice_document_loading_guards_against_stale_requests(self) -> None:
        app_js = (REPO_ROOT / "wandao_electron" / "renderer" / "app.js").read_text(encoding="utf-8")

        self.assertIn("selectedBodyId", app_js)
        self.assertIn("bodyCache", app_js)
        self.assertIn("bodyRequestSeq", app_js)
        self.assertIn("noticeCenterState.bodyRequestSeq !== requestSeq", app_js)
        self.assertIn("noticeCenterState.selectedId !== itemId", app_js)
        self.assertIn("bodyMatchesSelection", app_js)


if __name__ == "__main__":
    unittest.main()
