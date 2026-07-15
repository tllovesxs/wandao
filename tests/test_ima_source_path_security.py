import tempfile
import unittest
from pathlib import Path

from plugins.ima.backend.ima_knowledge import (
    collect_markdown_referenced_files,
    scan_source_files,
    should_skip_source_path,
)


class ImaSourcePathSecurityTests(unittest.TestCase):
    def test_outside_markdown_reference_is_not_collected_or_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            source.mkdir()
            (base / "private.pdf").write_bytes(b"private")
            markdown = source / "doc.md"
            markdown.write_text("# doc\n\n[private](../private.pdf)\n", encoding="utf-8")

            self.assertEqual(collect_markdown_referenced_files(source), set())
            scanned = scan_source_files(source)
            self.assertEqual([item["relativePath"] for item in scanned], ["doc.md"])

    def test_source_path_aliases_are_normalized_before_relative_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            nested = source / "nested"
            nested.mkdir(parents=True)
            markdown = source / "doc.md"
            markdown.write_text("# doc\n", encoding="utf-8")

            aliased_source = nested / ".."
            self.assertFalse(should_skip_source_path(aliased_source, markdown.resolve()))

            outside = base / "outside.md"
            outside.write_text("outside\n", encoding="utf-8")
            self.assertTrue(should_skip_source_path(source, outside))

if __name__ == "__main__":
    unittest.main()
