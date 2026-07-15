import tempfile
import unittest
from pathlib import Path

from import_yinxiang import MarkdownToEnml


class YinxiangImportPathSecurityTests(unittest.TestCase):
    def test_outside_resources_are_not_constructed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            source.mkdir()
            markdown = source / "doc.md"
            markdown.write_text("# doc", encoding="utf-8")
            (base / "private.png").write_bytes(b"private")
            converter = MarkdownToEnml(markdown, source)

            self.assertIsNone(converter._resource_from_link("../private.png"))
            self.assertIsNone(converter._resource_from_link("%2e%2e/private.png"))
            self.assertIsNone(converter._resource_from_link("file:///private.png"))


if __name__ == "__main__":
    unittest.main()
