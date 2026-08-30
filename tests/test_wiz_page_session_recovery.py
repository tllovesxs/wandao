import argparse
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from plugins.wiz.backend import export_wiz


class FakeCdp:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class CapturingCdp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float]] = []

    def evaluate(self, expression: str, timeout: float = 60):
        self.calls.append((expression, timeout))
        if expression == export_wiz.WIZ_HELPER_JS:
            return True
        return {"html": "<p>正文</p>"}


def make_doc(doc_guid: str, title: str) -> export_wiz.WizDoc:
    return export_wiz.WizDoc("kb", doc_guid, title, "/", "note", "", 0, 0, {})


def make_snapshot(*docs: export_wiz.WizDoc) -> dict[str, object]:
    return {
        "account": {"userGuid": "account-guid", "userId": "account-id", "hasToken": True},
        "kbs": [{"kbGuid": "kb", "kbServer": "https://example.invalid"}],
        "docs": [
            {
                "kbGuid": doc.kb_guid,
                "docGuid": doc.doc_guid,
                "title": doc.title,
                "category": doc.category,
                "type": doc.note_type,
                "fileType": doc.file_type,
            }
            for doc in docs
        ],
    }


def make_args(output: Path, checkpoint_file: str = "") -> argparse.Namespace:
    return argparse.Namespace(
        output=str(output),
        checkpoint_file=checkpoint_file,
        checkpoint_task_id="wiz-page-recovery",
        reset_checkpoint=False,
        selected_doc_ids=[],
        incremental=False,
        resume=False,
        retry_failed=False,
        progress_every=100,
        close_started_chrome=False,
        request_delay=0,
        request_jitter=0,
    )


class WizPageSessionRecoveryTests(unittest.TestCase):
    def test_note_read_keeps_helper_install_within_the_note_timeout_budget(self) -> None:
        cdp = CapturingCdp()

        export_wiz.fetch_note_download(cdp, make_doc("doc-1", "笔记"))

        self.assertEqual(len(cdp.calls), 2)
        self.assertTrue(all(timeout <= export_wiz.WIZ_NOTE_DOWNLOAD_TIMEOUT for _expression, timeout in cdp.calls))

    def test_health_probe_keeps_helper_install_within_the_health_timeout_budget(self) -> None:
        cdp = CapturingCdp()
        cdp.evaluate = Mock(side_effect=export_wiz.ExportError("timed out"))

        with self.assertRaises(export_wiz.ExportError):
            export_wiz.check_wiz_page_health(cdp)

        cdp.evaluate.assert_called_once_with(export_wiz.WIZ_HELPER_JS, timeout=export_wiz.WIZ_PAGE_HEALTH_TIMEOUT)

    def test_empty_note_response_retries_only_the_current_note_once(self) -> None:
        doc = make_doc("doc-1", "笔记")
        args = make_args(Path("unused"))
        snapshot = make_snapshot(doc)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "笔记.md"
            with (
                patch.object(export_wiz, "fetch_note_download", side_effect=[{}, {"html": "<p>正文</p>"}]) as download,
                patch.object(export_wiz, "fetch_ot_document") as ot_document,
                patch.object(export_wiz.time, "sleep") as sleep,
            ):
                export_wiz.export_doc(FakeCdp(), snapshot, doc, target, args)

            self.assertEqual(target.read_text(encoding="utf-8"), "# 笔记\n\n正文\n")

        self.assertEqual(download.call_count, 2)
        self.assertEqual(download.call_args_list[1].kwargs["timeout"], export_wiz.WIZ_EMPTY_BODY_RETRY_TIMEOUT)
        sleep.assert_called_once_with(export_wiz.WIZ_EMPTY_BODY_RETRY_DELAY)
        ot_document.assert_not_called()

    def test_persistent_empty_note_response_remains_a_failure(self) -> None:
        doc = make_doc("doc-1", "笔记")
        args = make_args(Path("unused"))
        snapshot = make_snapshot(doc)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "笔记.md"
            with (
                patch.object(export_wiz, "fetch_note_download", side_effect=[{}, {}]),
                patch.object(export_wiz, "fetch_ot_document", return_value=None),
                patch.object(export_wiz.time, "sleep"),
                self.assertRaisesRegex(export_wiz.ExportError, "noteDownload: 未返回可用正文"),
            ):
                export_wiz.export_doc(FakeCdp(), snapshot, doc, target, args)

            self.assertFalse(target.exists())

    def test_pdf_note_does_not_wait_for_another_empty_body_retry(self) -> None:
        doc = export_wiz.WizDoc(
            "kb",
            "doc-1",
            "示例附件.pdf",
            "/",
            "note",
            "application/pdf",
            0,
            0,
            {"fileType": "application/pdf", "dataSize": 123, "attachmentCount": 1},
        )
        args = make_args(Path("unused"))
        snapshot = make_snapshot(doc)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "示例附件.pdf.md"
            with (
                patch.object(export_wiz, "fetch_note_download", return_value={"html": "<object data='file.pdf'></object>"}) as download,
                patch.object(export_wiz, "fetch_ot_document", return_value=None),
                patch.object(export_wiz.time, "sleep") as sleep,
                self.assertRaisesRegex(export_wiz.ExportError, "文件型笔记元数据：fileType=application/pdf"),
            ):
                export_wiz.export_doc(FakeCdp(), snapshot, doc, target, args)

        download.assert_called_once()
        sleep.assert_not_called()

    def test_healthy_note_timeout_falls_back_to_ot_document(self) -> None:
        doc = make_doc("doc-1", "笔记")
        args = make_args(Path("unused"))
        snapshot = make_snapshot(doc)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "笔记.md"
            with (
                patch.object(export_wiz, "fetch_note_download", side_effect=export_wiz.ExportError("request timed out")),
                patch.object(export_wiz, "check_wiz_page_health") as health_check,
                patch.object(export_wiz, "fetch_ot_document", return_value={"blocks": [{"type": "paragraph"}]}),
                patch.object(export_wiz, "blocks_to_markdown", return_value="正文\n"),
            ):
                export_wiz.export_doc(FakeCdp(), snapshot, doc, target, args)

            health_check.assert_called_once()
            self.assertEqual(target.read_text(encoding="utf-8"), "# 笔记\n\n正文\n")

    def test_timeout_with_failed_health_probe_marks_page_session_lost(self) -> None:
        doc = make_doc("doc-1", "笔记")
        args = make_args(Path("unused"))
        snapshot = make_snapshot(doc)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "笔记.md"
            with (
                patch.object(export_wiz, "fetch_note_download", side_effect=export_wiz.ExportError("request timed out")),
                patch.object(export_wiz, "check_wiz_page_health", side_effect=export_wiz.ExportError("health timed out")),
                self.assertRaises(export_wiz.WizPageSessionLost),
            ):
                export_wiz.export_doc(FakeCdp(), snapshot, doc, target, args)

    def test_recovery_retries_only_the_current_document(self) -> None:
        first_doc = make_doc("doc-1", "第一篇")
        second_doc = make_doc("doc-2", "第二篇")
        snapshot = make_snapshot(first_doc, second_doc)
        initial_cdp = FakeCdp()
        recovered_cdp = FakeCdp()

        with tempfile.TemporaryDirectory() as directory:
            args = make_args(Path(directory) / "output")
            with (
                patch.object(export_wiz, "connect_wiz_browser", return_value=(initial_cdp, None)),
                patch.object(export_wiz, "wait_for_login_state", return_value=snapshot),
                patch.object(
                    export_wiz,
                    "export_doc",
                    side_effect=[export_wiz.WizPageSessionLost("stalled"), (0, []), (0, [])],
                ) as export_doc_mock,
                patch.object(export_wiz, "recover_wiz_page", return_value=(recovered_cdp, snapshot, None)) as recover_mock,
                patch.object(export_wiz, "emit"),
            ):
                report = export_wiz.export_wiz(args)

        self.assertEqual(report["exported"], 2)
        self.assertEqual([item.args[2].doc_guid for item in export_doc_mock.call_args_list], ["doc-1", "doc-1", "doc-2"])
        recover_mock.assert_called_once_with(args, initial_cdp, snapshot)
        self.assertTrue(recovered_cdp.closed)

    def test_second_session_loss_stops_and_preserves_remaining_checkpoint_items(self) -> None:
        first_doc = make_doc("doc-1", "第一篇")
        second_doc = make_doc("doc-2", "第二篇")
        snapshot = make_snapshot(first_doc, second_doc)
        initial_cdp = FakeCdp()
        recovered_cdp = FakeCdp()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint_file = root / "checkpoint.sqlite"
            args = make_args(root / "output", str(checkpoint_file))
            with (
                patch.object(export_wiz, "connect_wiz_browser", return_value=(initial_cdp, None)),
                patch.object(export_wiz, "wait_for_login_state", return_value=snapshot),
                patch.object(
                    export_wiz,
                    "export_doc",
                    side_effect=[export_wiz.WizPageSessionLost("stalled"), export_wiz.WizPageSessionLost("stalled")],
                ) as export_doc_mock,
                patch.object(export_wiz, "recover_wiz_page", return_value=(recovered_cdp, snapshot, None)),
                patch.object(export_wiz, "emit"),
                self.assertRaises(export_wiz.WizPageSessionUnrecoverable),
            ):
                export_wiz.export_wiz(args)

            connection = sqlite3.connect(checkpoint_file)
            try:
                task_status = connection.execute("SELECT status FROM tasks WHERE task_id = ?", ("wiz-page-recovery",)).fetchone()[0]
                item_statuses = dict(
                    connection.execute("SELECT item_key, status FROM items WHERE task_id = ?", ("wiz-page-recovery",)).fetchall()
                )
            finally:
                connection.close()

        self.assertEqual([item.args[2].doc_guid for item in export_doc_mock.call_args_list], ["doc-1", "doc-1"])
        self.assertEqual(task_status, "failed")
        self.assertEqual(item_statuses["wiz:doc:doc-1"], "failed")
        self.assertEqual(item_statuses["wiz:doc:doc-2"], "pending")

    def test_recovery_rejects_a_different_wiz_account(self) -> None:
        previous_cdp = Mock()
        fresh_cdp = Mock()
        expected = {"account": {"userGuid": "expected", "userId": "expected-id"}}
        actual = {"account": {"userGuid": "different", "userId": "different-id"}}
        args = make_args(Path("unused"))

        with (
            patch.object(export_wiz, "connect_wiz_browser", return_value=(fresh_cdp, None)),
            patch.object(export_wiz, "wait_for_login_state", return_value=actual),
            self.assertRaises(export_wiz.ExportError),
        ):
            export_wiz.recover_wiz_page(args, previous_cdp, expected)

        previous_cdp.close.assert_called_once()
        fresh_cdp.close.assert_called_once()

    def test_failed_recovery_closes_a_browser_started_for_this_run(self) -> None:
        previous_cdp = Mock()
        fresh_cdp = Mock()
        chrome_proc = Mock()
        args = make_args(Path("unused"))
        args.close_started_chrome = True

        with (
            patch.object(export_wiz, "connect_wiz_browser", return_value=(fresh_cdp, chrome_proc)),
            patch.object(export_wiz, "wait_for_login_state", side_effect=export_wiz.ExportError("登录未就绪")),
            self.assertRaises(export_wiz.ExportError),
        ):
            export_wiz.recover_wiz_page(args, previous_cdp, {"account": {"userGuid": "expected"}})

        previous_cdp.close.assert_called_once()
        fresh_cdp.close.assert_called_once()
        chrome_proc.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
