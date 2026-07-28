import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class NoticeCenterTests(unittest.TestCase):
    def test_notice_manifest_only_keeps_co_creation_and_ai_learning(self) -> None:
        manifest = json.loads((REPO_ROOT / "docs" / "tutorial-announcements.json").read_text(encoding="utf-8"))
        items = manifest["items"]

        self.assertEqual([item["id"] for item in items], ["provider-co-creation-invite", "project-learning-ai-prompt"])
        self.assertTrue(items[0]["pinned"])
        self.assertEqual(items[0]["type"], "announcement")
        self.assertEqual(items[1]["type"], "announcement")
        self.assertEqual(items[1]["badge"], "AI 学习")

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
