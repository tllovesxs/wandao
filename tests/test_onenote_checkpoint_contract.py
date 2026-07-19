import tempfile
import unittest
from pathlib import Path
from unittest import mock

from plugins.onenote.backend import export_onenote as onenote


class _Checkpoint:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, dict]] = []
        self.completed_task = None
        self.closed = False

    def start_task(self, _metadata):
        return None

    def upsert_item(self, item_key, **kwargs):
        self.upserts.append((item_key, kwargs))

    def item_status(self, _item_key):
        return "completed"

    def complete_task(self, report):
        self.completed_task = report

    def stats(self):
        return {"items": {"completed": 1}}

    def close(self):
        self.closed = True


class OneNoteCheckpointContractTests(unittest.TestCase):
    def test_checkpoint_uses_current_toc_node_fields_before_resume_skip(self):
        page = onenote.TocNode(
            id="page-id",
            node_id="onenote-page:page-id",
            type="page",
            title="Page",
            parent_node_id="onenote-section:section-id",
            selectable=True,
            order=1,
            path_parts=["Notebook", "Section"],
            page_level=2,
            section_id="section-id",
        )
        checkpoint = _Checkpoint()
        with tempfile.TemporaryDirectory() as tmp:
            args = onenote.parse_args([
                "--output", str(Path(tmp) / "export"),
                "--doc-id", page.id,
                "--resume",
            ])
            with mock.patch.object(onenote, "open_checkpoint_from_args", return_value=checkpoint) as open_checkpoint:
                report = onenote.export_onenote(args, [page], [page])

        self.assertEqual(report["exported"], 0)
        open_checkpoint.assert_called_once_with(args, "onenote", "export")
        # Keep slow COM recovery safe without requiring the newer core API.
        self.assertEqual(checkpoint.lease_seconds, 15 * 60)
        self.assertEqual(report["skipped"], 1)
        self.assertTrue(checkpoint.closed)
        self.assertEqual(len(checkpoint.upserts), 1)
        item_key, item = checkpoint.upserts[0]
        self.assertEqual(item_key, "onenote:page:page-id")
        self.assertEqual(item["source_url"], "Notebook/Section")
        self.assertEqual(item["parent_key"], "onenote-section:section-id")
        self.assertEqual(
            item["metadata"],
            {
                "id": "page-id",
                "path": ["Notebook", "Section"],
                "level": 2,
                "parentNodeId": "onenote-section:section-id",
                "sectionId": "section-id",
            },
        )


if __name__ == "__main__":
    unittest.main()
