import argparse
import contextlib
import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from plugins.youdao.backend import export_youdao


def export_args(root: Path, *, resume: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        output=root / 'output', auth_file='', selected_doc_ids=[], checkpoint_file=str(root / 'checkpoint.sqlite'),
        checkpoint_task_id='stop-contract', reset_checkpoint=False, resume=resume, retry_failed=False,
        update_existing=False, incremental=False, progress_every=1, download_timeout=1, keep_remote_images=True,
    )


class FakeYoudaoClient:
    request_count = 0
    downloaded_ids: list[str] = []

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def download_file(self, node_id: str) -> export_youdao.DownloadedContent:
        self.request_count += 1
        self.downloaded_ids.append(node_id)
        return export_youdao.DownloadedContent(b'# note\n', 'text/markdown', 'https://example.test/note')


class YoudaoStopContractTests(unittest.TestCase):
    def test_cli_main_returns_130_when_export_reports_controlled_stop(self) -> None:
        args = argparse.Namespace(gui=False, login=False, scan_toc=False, output=Path('output'))
        with mock.patch.object(export_youdao, 'parse_args', return_value=args), mock.patch.object(export_youdao, 'export_youdao', return_value={'stopped': True}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(export_youdao.main(['--output', 'output']), 130)

    def test_cli_main_treats_stop_during_remote_tree_build_as_controlled_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = export_args(Path(tmp))
            args.gui = False
            args.login = False
            args.scan_toc = False
            stdout = io.StringIO()
            with mock.patch.object(export_youdao, 'parse_args', return_value=args), mock.patch.object(export_youdao, 'YoudaoClient', FakeYoudaoClient), mock.patch.object(export_youdao, 'build_remote_tree', side_effect=export_youdao.ExportStopped('用户已停止当前任务')), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(export_youdao.main(['--output', str(args.output)]), 130)

            self.assertIn('"stopped": true', stdout.getvalue())
            conn = sqlite3.connect(Path(args.checkpoint_file))
            try:
                task = conn.execute('SELECT status FROM tasks WHERE task_id = ?', ('stop-contract',)).fetchone()
            finally:
                conn.close()
            self.assertEqual(task[0], 'stopped')

    def test_stop_marks_checkpoint_and_resume_skips_completed_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stop_file = root / 'stop-requested'
            first = export_youdao.RemoteNode('first', 'first.md', False)
            second = export_youdao.RemoteNode('second', 'second.md', False)
            fake_client = FakeYoudaoClient
            fake_client.downloaded_ids = []

            def emit_with_stop(_message: str, **fields: object) -> None:
                if fields.get('event') == 'document.export.completed':
                    stop_file.write_text('stop', encoding='utf-8')

            with mock.patch.object(export_youdao, 'YoudaoClient', fake_client), mock.patch.object(export_youdao, 'build_remote_tree', return_value=([first, second], 'root')), mock.patch.object(export_youdao, 'emit', side_effect=emit_with_stop), mock.patch.dict(os.environ, {'WANDAO_STOP_FILE': str(stop_file)}, clear=False):
                report = export_youdao.export_youdao(export_args(root))

            self.assertTrue(report['stopped'])
            self.assertEqual(report['exportedDocs'], 1)
            self.assertEqual(fake_client.downloaded_ids, ['first'])
            conn = sqlite3.connect(root / 'checkpoint.sqlite')
            try:
                task = conn.execute('SELECT status FROM tasks WHERE task_id = ?', ('stop-contract',)).fetchone()
                item_rows = conn.execute('SELECT item_key, status FROM items WHERE task_id = ? ORDER BY item_key', ('stop-contract',)).fetchall()
            finally:
                conn.close()
            self.assertEqual(task[0], 'stopped')
            self.assertEqual(item_rows, [('youdao:node:first', 'completed'), ('youdao:node:second', 'failed')])

            stop_file.unlink()
            fake_client.downloaded_ids = []
            with mock.patch.object(export_youdao, 'YoudaoClient', fake_client), mock.patch.object(export_youdao, 'build_remote_tree', return_value=([first, second], 'root')), mock.patch.dict(os.environ, {'WANDAO_STOP_FILE': str(stop_file)}, clear=False):
                resumed = export_youdao.export_youdao(export_args(root, resume=True))

            self.assertFalse(resumed['stopped'])
            self.assertEqual(resumed['exportedDocs'], 1)
            self.assertEqual(resumed['skippedDocs'], 1)
            self.assertEqual(fake_client.downloaded_ids, ['second'])


if __name__ == '__main__':
    unittest.main()
