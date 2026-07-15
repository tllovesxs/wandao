import tempfile
import unittest
from pathlib import Path

from import_yuque import replace_resource_links, scan_local_resources


class YuqueImportResourceTests(unittest.TestCase):
    def test_scans_markdown_obsidian_html_and_parentheses_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = root / "assets"
            assets.mkdir()
            for name in ["a.png", "obsidian.png", "html.png", "has(paren).png", "escaped(paren).png"]:
                (assets / name).write_bytes(b"image")
            md = root / "doc.md"
            markdown = "\n".join(
                [
                    "![标准](assets/a.png)",
                    "![[assets/obsidian.png]]",
                    '<img src="assets/html.png">',
                    "![括号](assets/has(paren).png)",
                    r"![转义括号](assets/escaped\(paren\).png)",
                ]
            )

            resources, warnings = scan_local_resources(markdown, md)

            self.assertEqual(len(resources), 5)
            self.assertEqual(warnings, [])
            self.assertEqual({item["syntax"] for item in resources}, {"markdown", "obsidian", "html"})

    def test_scans_links_with_title_and_replaces_duplicate_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = root / "assets"
            assets.mkdir()
            image = assets / "same image.png"
            image.write_bytes(b"image")
            md = root / "doc.md"
            markdown = "\n".join(
                [
                    '![带标题](assets/same%20image.png "预览图")',
                    "![相对别名](./assets/same%20image.png)",
                ]
            )

            resources, warnings = scan_local_resources(markdown, md)
            uploads = {str(image.resolve()): {"url": "https://cdn.example.com/same.png"}}
            replaced = replace_resource_links(markdown, resources, uploads)

            self.assertEqual(warnings, [])
            self.assertEqual(len(resources), 1)
            self.assertEqual(set(resources[0]["targets"]), {"assets/same%20image.png", "./assets/same%20image.png"})
            self.assertEqual(replaced.count("https://cdn.example.com/same.png"), 2)

    def test_reports_missing_local_resources_without_warning_for_remote_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            md = root / "doc.md"
            markdown = "\n".join(
                [
                    "![缺失](assets/missing.png)",
                    "![远程](https://cdn.example.com/image.png)",
                ]
            )

            resources, warnings = scan_local_resources(markdown, md)

            self.assertEqual(resources, [])
            self.assertEqual(warnings, [{"target": "assets/missing.png", "reason": "local_file_missing"}])

    def test_markdown_cross_links_with_hash_are_not_resources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            linked = root / "01-#标签文章.md"
            linked.write_text("# 标签文章\n", encoding="utf-8")
            md = root / "index.md"
            markdown = "[文章](01-#标签文章.md)"

            resources, warnings = scan_local_resources(markdown, md)

            self.assertEqual(resources, [])
            self.assertEqual(warnings, [])

    def test_ignores_links_inside_fenced_code_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            markdown = '```java\ncitation.append("\\n\\n[查看原文](").append(sourceUrl).append(")");\n```\n'

            resources, warnings = scan_local_resources(markdown, root / "doc.md")

            self.assertEqual(resources, [])
            self.assertEqual(warnings, [])

    def test_replaces_extra_image_syntax_with_uploaded_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "diagram.png"
            image.write_bytes(b"image")
            markdown = "![[diagram.png]]\n<img src=\"diagram.png\">"
            resources, _warnings = scan_local_resources(markdown, root / "doc.md")
            uploads = {str(image.resolve()): {"url": "https://cdn.example.com/diagram.png"}}

            replaced = replace_resource_links(markdown, resources, uploads)

            self.assertEqual(replaced.count("https://cdn.example.com/diagram.png"), 2)
            self.assertNotIn("![[diagram.png]]", replaced)
            self.assertNotIn("<img", replaced)

    def test_rejects_resources_outside_the_import_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "source"
            root.mkdir()
            (base / "private.png").write_bytes(b"private")
            md = root / "doc.md"
            markdown = "![外部](../private.png)\n![编码](%2e%2e/private.png)"

            resources, warnings = scan_local_resources(markdown, md, root)

            self.assertEqual(resources, [])
            self.assertEqual([item["reason"] for item in warnings], ["unsafe_local_reference", "unsafe_local_reference"])


if __name__ == "__main__":
    unittest.main()
