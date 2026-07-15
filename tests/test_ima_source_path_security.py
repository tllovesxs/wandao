import tempfile
import unittest
from pathlib import Path

from plugins.ima.backend.ima_knowledge import collect_markdown_referenced_files, scan_source_files


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


if __name__ == "__main__":
    unittest.main()
