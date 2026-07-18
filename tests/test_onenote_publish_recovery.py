import base64
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from plugins.onenote.backend import export_onenote as onenote


class _Checkpoint:
    def __init__(self) -> None:
        self.failed_items: list[tuple[str, str]] = []
        self.completed_items: list[str] = []
        self.started_items: list[tuple[str, str]] = []
        self.closed = False

    def start_task(self, _metadata):
        return None

    def upsert_item(self, _item_key, **_kwargs):
        return None

    def item_status(self, _item_key):
        return ""

    def start_item(self, item_key, stage):
        self.started_items.append((item_key, stage))

    def fail_item(self, item_key, error):
        self.failed_items.append((item_key, error))

    def complete_item(self, item_key, **_kwargs):
        self.completed_items.append(item_key)

    def stats(self):
        return {"items": {"completed": len(self.completed_items), "failed": len(self.failed_items)}}

    def fail_task(self, *_args, **_kwargs):
        return None

    def complete_task(self, _report):
        return None

    def close(self):
        self.closed = True


def _page(page_id: str, title: str) -> onenote.TocNode:
    return onenote.TocNode(
        id=page_id,
        node_id=f"onenote-page:{page_id}",
        type="page",
        title=title,
        parent_node_id="onenote-section:section",
        selectable=True,
        order=1,
        path_parts=["Notebook", "Section"],
        section_id="section",
    )


class OneNotePublishRecoveryTests(unittest.TestCase):
    def test_publish_result_parser_decodes_recovery_and_failure_details(self):
        detail = base64.b64encode("0x800706BE".encode("utf-8")).decode("ascii")
        results = onenote.parse_publish_results(
            f"publish-result\tpage-a\tretried\t{detail}\n"
            f"publish-result\tpage-b\tfailed\t{detail}"
        )

        self.assertEqual(results["page-a"], {"status": "retried", "message": "0x800706BE"})
        self.assertEqual(results["page-b"], {"status": "failed", "message": "0x800706BE"})

    def test_failed_publish_is_checkpointed_while_later_page_is_converted(self):
        failed = _page("failed-page", "Failed")
        recovered = _page("recovered-page", "Recovered")
        checkpoint = _Checkpoint()

        def fake_bridge(args, **_kwargs):
            rows = Path(args[1]).read_text(encoding="utf-8").splitlines()
            output_lines = []
            for row in rows:
                page_id, output_path = row.split("\t", 1)
                if page_id == failed.id:
                    detail = base64.b64encode("0x800706BE after retry".encode("utf-8")).decode("ascii")
                    output_lines.append(f"publish-result\t{page_id}\tfailed\t{detail}")
                else:
                    Path(output_path).write_bytes(b"published MHT")
                    detail = base64.b64encode("0x800706BE recovered".encode("utf-8")).decode("ascii")
                    output_lines.append(f"publish-result\t{page_id}\tretried\t{detail}")
            return "\n".join(output_lines)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = onenote.parse_args(["--output", str(root / "export")])
            with mock.patch.object(onenote, "open_checkpoint_from_args", return_value=checkpoint), mock.patch.object(
                onenote, "run_bridge", side_effect=fake_bridge
            ), mock.patch.object(
                onenote, "convert_mht_to_markdown", return_value={"images": 0, "attachments": 0, "chars": 4}
            ) as convert:
                report = onenote.export_onenote(args, [failed, recovered], [failed, recovered])

        self.assertEqual(report["exported"], 1)
        self.assertEqual(len(report["failures"]), 1)
        self.assertEqual(report["failures"][0]["id"], failed.id)
        self.assertEqual(report["failures"][0]["stage"], "publish")
        self.assertEqual(checkpoint.failed_items, [(f"onenote:page:{failed.id}", "0x800706BE after retry")])
        self.assertEqual(checkpoint.completed_items, [f"onenote:page:{recovered.id}"])
        self.assertEqual(convert.call_count, 1)
        self.assertTrue(checkpoint.closed)

    def test_bridge_source_isolates_rpc_publish_failures_and_recreates_com(self):
        source = onenote.CSHARP_BRIDGE_SOURCE

        self.assertIn("catch (COMException ex)", source)
        self.assertIn("if (ex.ErrorCode != RpcCallFailed)", source)
        self.assertIn("HasPublishedFile(outputPath)", source)
        self.assertIn("ReleaseApplication(app)", source)
        self.assertIn("Thread.Sleep(3000)", source)
        self.assertIn('WritePublishResult(pageId, "failed", retryEx)', source)


if __name__ == "__main__":
    unittest.main()
