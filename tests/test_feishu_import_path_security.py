import tempfile
import unittest
from pathlib import Path

import import_feishu


class FeishuImportPathSecurityTests(unittest.TestCase):
    def test_outside_images_are_never_collected_or_inlined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            source.mkdir()
            secret = base / "private.png"
            secret.write_bytes(b"private")
            markdown = source / "doc.md"
            markdown.write_text("![x](../private.png)\n<img src=\"%2e%2e/private.png\">", encoding="utf-8")

            self.assertEqual(import_feishu.collect_local_images_from_markdown(markdown, source), [])
            for prepare in (
                import_feishu.prepare_markdown_for_image_blocks,
                import_feishu.prepare_markdown_with_inlined_local_images,
            ):
                with self.subTest(prepare=prepare.__name__):
                    prepared, temporary_dir = prepare(markdown, source)
                    self.assertEqual(prepared, markdown)
                    self.assertIsNone(temporary_dir)


if __name__ == "__main__":
    unittest.main()
