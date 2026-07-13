import argparse
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from plugins.yinxiang.backend.export_yinxiang import convert_enex, validate_enex_selection


class YinxiangSelectionMismatchTests(unittest.TestCase):
    def test_source_matches_are_validated_before_incremental_skips(self) -> None:
        with self.assertRaises(RuntimeError):
            validate_enex_selection({"stale"}, 2, 0)
        self.assertIsNone(validate_enex_selection({"matched", "stale"}, 2, 1))
        self.assertIsNone(validate_enex_selection({"stale"}, 0, 0))

    def test_convert_enex_rejects_stale_selection_before_writing_success_index(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            enex_dir = root / "enex"
            output = root / "output"
            enex_dir.mkdir()
            (enex_dir / "note.enex").write_text(
                "<en-export><note><guid>present</guid><title>Note</title><content><![CDATA[<div>body</div>]]></content></note></en-export>",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                output=output,
                doc_id=["stale"],
                incremental=True,
                progress_every=1,
                database=root / "notes.db",
                checkpoint_file="",
                retry_failed=False,
                resume=False,
            )
            with self.assertRaises(RuntimeError):
                convert_enex(args, enex_dir)
            self.assertFalse((output / "00-?????.md").exists())


if __name__ == "__main__":
    unittest.main()
