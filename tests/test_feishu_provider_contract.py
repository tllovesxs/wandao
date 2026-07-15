import unittest

from plugins.feishu.backend.export_feishu import (
    annotate_selectable_toc,
    is_exportable_feishu_node,
)


class FeishuProviderContractTests(unittest.TestCase):
    def test_exportability_matches_supported_feishu_renderers(self) -> None:
        cases = [
            (
                "docx",
                {"wiki_token": "doc", "obj_type": 22, "url": "https://example.test/wiki/doc"},
                True,
            ),
            (
                "markdown-file-type",
                {
                    "wiki_token": "markdown",
                    "obj_type": 12,
                    "file_type": "md",
                    "url": "https://example.test/wiki/markdown",
                },
                True,
            ),
            (
                "markdown-icon-info",
                {
                    "wiki_token": "markdown-icon",
                    "obj_type": 12,
                    "icon_info": {"file_type": "md"},
                    "url": "https://example.test/wiki/markdown-icon",
                },
                True,
            ),
            (
                "pdf-file",
                {
                    "wiki_token": "pdf",
                    "obj_type": 12,
                    "file_type": "pdf",
                    "url": "https://example.test/wiki/pdf",
                },
                False,
            ),
            (
                "sheet",
                {"wiki_token": "sheet", "obj_type": 2, "url": "https://example.test/wiki/sheet"},
                False,
            ),
            (
                "folder",
                {"wiki_token": "folder", "obj_type": 0, "url": "https://example.test/drive/folder/folder"},
                False,
            ),
            (
                "docx-without-url",
                {"wiki_token": "no-url", "obj_type": 22, "url": ""},
                False,
            ),
            (
                "markdown-without-url",
                {"wiki_token": "markdown-no-url", "obj_type": 12, "file_type": "md", "url": ""},
                False,
            ),
        ]

        for label, node, expected in cases:
            with self.subTest(label=label):
                self.assertIs(is_exportable_feishu_node(node), expected)

    def test_scan_toc_marks_docx_and_markdown_nodes_selectable(self) -> None:
        ordered = annotate_selectable_toc(
            [
                {"wiki_token": "folder", "title": "Folder", "obj_type": 0, "url": ""},
                {"wiki_token": "non-url", "title": "No URL", "obj_type": 22, "url": ""},
                {"wiki_token": "sheet", "title": "Sheet", "obj_type": 2, "url": "https://example.test/sheet"},
                {
                    "wiki_token": "pdf",
                    "title": "Guide.pdf",
                    "obj_type": 12,
                    "file_type": "pdf",
                    "url": "https://example.test/wiki/pdf",
                },
                {"wiki_token": "doc", "title": "Doc", "obj_type": 22, "url": "https://example.test/doc"},
                {
                    "wiki_token": "markdown",
                    "title": "README.md",
                    "obj_type": 12,
                    "file_type": "md",
                    "url": "https://example.test/wiki/markdown",
                },
                {
                    "wiki_token": "markdown-icon",
                    "title": "CHANGELOG.md",
                    "obj_type": 12,
                    "icon_info": {"file_type": "md"},
                    "url": "https://example.test/wiki/markdown-icon",
                },
            ]
        )

        self.assertEqual(
            [item["selectable"] for item in ordered],
            [False, False, False, False, True, True, True],
        )
        self.assertEqual(
            [item["wiki_token"] for item in ordered if item["selectable"]],
            ["doc", "markdown", "markdown-icon"],
        )

    def test_document_with_children_remains_selectable(self) -> None:
        ordered = annotate_selectable_toc(
            [
                {
                    "wiki_token": "parent-doc",
                    "title": "Parent document",
                    "obj_type": 22,
                    "has_child": True,
                    "url": "https://example.test/wiki/parent-doc",
                }
            ]
        )

        self.assertTrue(ordered[0]["selectable"])


if __name__ == "__main__":
    unittest.main()
