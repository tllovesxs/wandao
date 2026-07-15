import unittest

from plugins.feishu.backend.export_feishu import select_exportable_docs


class FeishuSelectionMismatchTests(unittest.TestCase):
    def test_explicit_stale_selection_is_rejected_but_partial_match_exports(self) -> None:
        docs = [{"wiki_token": "doc", "obj_type": 22, "url": "https://example.test/doc"}]
        with self.assertRaises(RuntimeError):
            select_exportable_docs(docs, {"stale"})
        self.assertEqual([item["wiki_token"] for item in select_exportable_docs(docs, {"doc", "stale"})], ["doc"])

    def test_mixed_tree_exports_docx_and_markdown_but_not_other_file_types(self) -> None:
        ordered = [
            {"wiki_token": "doc", "obj_type": 22, "url": "https://example.test/wiki/doc"},
            {
                "wiki_token": "markdown",
                "obj_type": 12,
                "file_type": "md",
                "url": "https://example.test/wiki/markdown",
            },
            {
                "wiki_token": "markdown-icon",
                "obj_type": 12,
                "icon_info": {"file_type": "md"},
                "url": "https://example.test/wiki/markdown-icon",
            },
            {
                "wiki_token": "pdf",
                "obj_type": 12,
                "file_type": "pdf",
                "url": "https://example.test/wiki/pdf",
            },
            {"wiki_token": "sheet", "obj_type": 2, "url": "https://example.test/wiki/sheet"},
        ]

        self.assertEqual(
            [item["wiki_token"] for item in select_exportable_docs(ordered)],
            ["doc", "markdown", "markdown-icon"],
        )
        self.assertEqual(
            [item["wiki_token"] for item in select_exportable_docs(ordered, {"markdown", "pdf"})],
            ["markdown"],
        )

    def test_unsupported_url_backed_nodes_do_not_become_exportable_as_a_fallback(self) -> None:
        ordered = [
            {"wiki_token": "sheet", "obj_type": 2, "url": "https://example.test/wiki/sheet"},
            {
                "wiki_token": "pdf",
                "obj_type": 12,
                "file_type": "pdf",
                "url": "https://example.test/wiki/pdf",
            },
        ]

        self.assertEqual(select_exportable_docs(ordered), [])


if __name__ == "__main__":
    unittest.main()
