import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.wiz.backend import export_wiz


class WizEmptyNoteTests(unittest.TestCase):
    def test_explicit_empty_html_exports_title_only_markdown(self) -> None:
        doc = export_wiz.WizDoc("kb", "doc", "空白笔记", "/", "note", "", 0, 0, {})
        args = argparse.Namespace(request_delay=0, request_jitter=0)
        snapshot = {"kbs": [{"kbGuid": "kb", "kbServer": "https://example.invalid"}]}

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "空白笔记.md"
            with patch.object(export_wiz, "fetch_note_download", return_value={"html": "<p><br></p>"}), patch.object(
                export_wiz, "fetch_ot_document", return_value=None
            ):
                export_wiz.export_doc(object(), snapshot, doc, target, args)

            self.assertEqual(target.read_text(encoding="utf-8"), "# 空白笔记\n")

    def test_nonempty_html_is_not_treated_as_empty(self) -> None:
        self.assertFalse(export_wiz.is_explicitly_empty_note_html("<p>正文</p>"))
        self.assertTrue(export_wiz.is_explicitly_empty_note_html("<p><br></p>"))


if __name__ == "__main__":
    unittest.main()
