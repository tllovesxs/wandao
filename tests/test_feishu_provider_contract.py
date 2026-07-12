import unittest

from plugins.feishu.backend.export_feishu import annotate_selectable_toc


class FeishuProviderContractTests(unittest.TestCase):
    def test_scan_toc_marks_only_url_backed_docx_nodes_selectable(self) -> None:
        ordered = annotate_selectable_toc(
            [
                {"wiki_token": "folder", "title": "Folder", "obj_type": 0, "url": ""},
                {"wiki_token": "non-url", "title": "No URL", "obj_type": 22, "url": ""},
                {"wiki_token": "sheet", "title": "Sheet", "obj_type": 2, "url": "https://example.test/sheet"},
                {"wiki_token": "doc", "title": "Doc", "obj_type": 22, "url": "https://example.test/doc"},
            ]
        )

        self.assertEqual([item["selectable"] for item in ordered], [False, False, False, True])
        self.assertEqual([item["wiki_token"] for item in ordered if item["selectable"]], ["doc"])


if __name__ == "__main__":
    unittest.main()
