import argparse
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from plugins.yinxiang.backend import export_yinxiang
from plugins.yinxiang.backend.export_yinxiang import convert_enex, validate_enex_selection
from wandao_core.checkpoint import WandaoCheckpoint


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
            self.assertFalse((output / "00-\u77e5\u8bc6\u5e93\u5165\u53e3.md").exists())


    def test_stale_selection_marks_and_closes_checkpoint(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            enex_dir = root / "enex"
            enex_dir.mkdir()
            (enex_dir / "note.enex").write_text(
                "<en-export><note><guid>present</guid><title>Note</title><content><![CDATA[<div>body</div>]]></content></note></en-export>",
                encoding="utf-8",
            )
            checkpoint_file = root / "checkpoint.sqlite"
            actual_checkpoint = WandaoCheckpoint.open(checkpoint_file, "stale-selection", "yinxiang", "export")
            checkpoint = mock.Mock(wraps=actual_checkpoint)
            args = argparse.Namespace(
                output=root / "output",
                doc_id=["stale"],
                incremental=False,
                progress_every=1,
                database=root / "notes.db",
                checkpoint_file=str(checkpoint_file),
                checkpoint_task_id="stale-selection",
                reset_checkpoint=False,
                retry_failed=False,
                resume=False,
            )
            try:
                with mock.patch.object(export_yinxiang, "open_checkpoint_from_args", return_value=checkpoint):
                    with self.assertRaises(RuntimeError):
                        convert_enex(args, enex_dir)

                checkpoint.fail_task.assert_called_once()
                checkpoint.close.assert_called_once()
                with self.assertRaises(sqlite3.ProgrammingError):
                    actual_checkpoint.conn.execute("SELECT 1")

                conn = sqlite3.connect(checkpoint_file)
                try:
                    row = conn.execute(
                        "SELECT status, error_summary FROM tasks WHERE task_id = ?", ("stale-selection",)
                    ).fetchone()
                finally:
                    conn.close()
                self.assertEqual(row[0], "failed")
                self.assertIn("stale", row[1])
            finally:
                if checkpoint.close.call_count == 0:
                    actual_checkpoint.close()


if __name__ == "__main__":
    unittest.main()
