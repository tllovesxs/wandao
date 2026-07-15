import os
import tempfile
import unittest
from pathlib import Path

from wandao_core.source_paths import (
    inspect_local_reference,
    iter_regular_files_under_root,
    resolve_local_reference,
)


class SourcePathSecurityTests(unittest.TestCase):
    def test_allows_a_regular_file_inside_the_markdown_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            root.mkdir()
            markdown = root / "doc.md"
            markdown.write_text("# doc", encoding="utf-8")
            image = root / "assets" / "ok.png"
            image.parent.mkdir()
            image.write_bytes(b"image")

            self.assertEqual(resolve_local_reference(root, markdown, "assets/ok.png"), image.resolve())
            self.assertEqual(inspect_local_reference(root, markdown, "assets/ok.png?download=1"), (image.resolve(), None))

    def test_rejects_traversal_absolute_urls_and_outside_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "source"
            root.mkdir()
            markdown = root / "doc.md"
            markdown.write_text("# doc", encoding="utf-8")
            secret = base / "private.png"
            secret.write_bytes(b"private")

            rejected = ["../private.png", "%2e%2e/private.png", str(secret), "file:///private.png", "//server/private.png"]
            for target in rejected:
                with self.subTest(target=target):
                    self.assertIsNone(resolve_local_reference(root, markdown, target))

            link = root / "linked.png"
            try:
                os.symlink(secret, link)
            except (NotImplementedError, OSError):
                self.skipTest("当前环境不允许创建符号链接")
            self.assertIsNone(resolve_local_reference(root, markdown, "linked.png"))
            self.assertEqual(list(iter_regular_files_under_root(root)), [markdown.resolve()])


if __name__ == "__main__":
    unittest.main()
